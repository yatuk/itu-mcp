"""Client for İTÜ OBS (obs.itu.edu.tr) student portal JSON APIs.

OBS is a Vue SPA. Auth reuses the same İTÜ SSO cookies as Ninova, then
exchanges them for a short-lived JWT via ``/ogrenci/auth/jwt``.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .cache import TtlCache
from .client import DEFAULT_HEADERS, NinovaAuthError, NinovaClient, NinovaError, _request_delay_seconds
from .http_security import request_with_safe_redirects
from .parsing import (
    clean_text,
    extract_course_search_results,
    extract_course_select_options,
    extract_prerequisite_list,
    make_soup,
    normalize_lookup_text,
)

DEFAULT_OBS_BASE_URL = "https://obs.itu.edu.tr"
JWT_PATH = "/ogrenci/auth/jwt"
STUDENT_HOME = "/ogrenci/"


class ObsError(NinovaError):
    """OBS-specific error."""


class ObsClient:
    def __init__(
        self,
        ninova_client: NinovaClient | None = None,
        base_url: str | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("NINOVA_OBS_BASE_URL") or DEFAULT_OBS_BASE_URL
        ).rstrip("/")
        parsed_base = urlparse(self.base_url)
        if parsed_base.scheme != "https" or (parsed_base.hostname or "").lower() != "obs.itu.edu.tr":
            raise ObsError("NINOVA_OBS_BASE_URL must be https://obs.itu.edu.tr")
        self._ninova = ninova_client
        self._jwt: str | None = None
        self._jwt_obtained_at: float | None = None
        self._jwt_ttl_seconds = float(os.getenv("NINOVA_OBS_JWT_TTL_SECONDS") or "1500")

    @property
    def ninova(self) -> NinovaClient:
        if self._ninova is None:
            self._ninova = NinovaClient()
        return self._ninova

    @property
    def session(self) -> requests.Session:
        return self.ninova.session

    def _safe_request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        return self.ninova._safe_request(method, url, **kwargs)

    def ensure_ready(self) -> dict[str, Any]:
        """Ensure Ninova/SSO session exists and OBS JWT is available."""
        self.ninova.ensure_logged_in()
        self._safe_request("GET", self.base_url + STUDENT_HOME, timeout=30)
        token = self._get_jwt(force=False)
        return {
            "obs_base_url": self.base_url,
            "jwt_present": bool(token),
            "login_method": self.ninova.login_method,
        }

    def _get_jwt(self, *, force: bool = False) -> str:
        now = time.time()
        if (
            not force
            and self._jwt
            and self._jwt_obtained_at is not None
            and (now - self._jwt_obtained_at) < self._jwt_ttl_seconds
        ):
            return self._jwt

        self.ninova.ensure_logged_in()
        self._safe_request("GET", self.base_url + STUDENT_HOME, timeout=30)
        response = self._safe_request("GET", self.base_url + JWT_PATH, timeout=30)
        if response.status_code != 200 or not response.text.strip():
            raise NinovaAuthError(
                "Could not obtain OBS JWT. Log in via Ninova credentials and open "
                "https://obs.itu.edu.tr/ogrenci/ in a browser to verify access."
            )
        token = response.text.strip().strip('"')
        if token.count(".") < 2:
            raise NinovaAuthError("OBS JWT response did not look like a JWT token.")
        self._jwt = token
        self._jwt_obtained_at = now
        return token

    def _headers(self) -> dict[str, str]:
        token = self._get_jwt()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/plain, */*",
            "Referer": self.base_url + STUDENT_HOME,
            "X-Requested-With": "XMLHttpRequest",
        }

    def api_get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        self.ninova._throttle()
        response = self._safe_request(
            "GET",
            url,
            headers=self._headers(),
            params=params,
            timeout=45,
        )
        if response.status_code in {401, 403}:
            # Refresh JWT once.
            self._get_jwt(force=True)
            self.ninova._throttle()
            response = self._safe_request(
                "GET",
                url,
                headers=self._headers(),
                params=params,
                timeout=45,
            )
        if response.status_code >= 400:
            # Some OBS endpoints return 500 when data is not yet published.
            raise ObsError(f"OBS API {path} failed with HTTP {response.status_code}")
        if not response.content:
            return None
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "json" in content_type or response.text[:1] in {"{", "["}:
            return response.json()
        return response.text

    # --- High-level reads ---

    def get_profile(self) -> dict[str, Any]:
        return self.api_get("/api/ogrenci/KisiselBilgiler/")

    def list_programs(self) -> dict[str, Any]:
        return self.api_get("/api/ogrenci/OgrenciProgamListesi/")

    def list_semesters(self) -> dict[str, Any]:
        return self.api_get("/api/ogrenci/DonemListesi/")

    def get_registration_status(self) -> dict[str, Any]:
        return self.api_get("/api/ogrenci/KayitDurumu")

    def get_lesson_registration_status(self) -> dict[str, Any]:
        return self.api_get("/api/ogrenci/DersKayitDurumu")

    def get_advisor(self) -> dict[str, Any]:
        return self.api_get("/api/ogrenci/DanismanBilgi")

    def get_internships(self) -> dict[str, Any]:
        return self.api_get("/api/ogrenci/StajBilgi")

    def get_contacts(self) -> dict[str, Any]:
        return self.api_get("/api/ogrenci/IletisimBilgileri")

    def get_academic_status(self, program_id: int | str) -> dict[str, Any]:
        return self.api_get(f"/api/ogrenci/AkademikDurum/{program_id}")

    def get_debts(self, program_id: int | str) -> dict[str, Any]:
        return self.api_get(f"/api/ogrenci/BorcBilgisi/{program_id}")

    def get_graduation_remaining(self, program_id: int | str) -> dict[str, Any]:
        return self.api_get(f"/api/ogrenci/MezuniyetimeNeKaldi/{program_id}")

    def list_registered_courses(self, academic_semester_id: int | str) -> dict[str, Any]:
        return self.api_get(f"/api/ogrenci/sinif/KayitliSinifListesi/{academic_semester_id}")

    def get_letter_grades(self, class_id: int | str) -> dict[str, Any]:
        return self.api_get(f"/api/ogrenci/Sinif/SinifHarfNotuListesi/{class_id}")

    def get_midterm_grades(self, class_id: int | str) -> dict[str, Any]:
        return self.api_get(f"/api/ogrenci/Sinif/SinifDonemIciNotListesi/{class_id}")

    def get_attendance(self, class_id: int | str) -> dict[str, Any]:
        return self.api_get(f"/api/ogrenci/Sinif/SinifOgrenciYoklama/{class_id}")

    def get_schedule(self, academic_semester_id: int | str) -> dict[str, Any]:
        return self.api_get(f"/api/ogrenci/Takvim/DersTakvimi/{academic_semester_id}")

    def get_final_calendar(self, academic_semester_id: int | str) -> dict[str, Any]:
        return self.api_get(
            "/api/ogrenci/Takvim/FinalTakvimi",
            params={"donemId": academic_semester_id},
        )

    def get_transcript_preview(self, *, english: bool = False) -> dict[str, Any]:
        path = (
            "/api/ogrenci/Belgeler/TranskriptIngilizceOnizleme"
            if english
            else "/api/ogrenci/Belgeler/TranskriptOnizleme"
        )
        return self.api_get(path)

    # -- campus card (portal) ---------------------------------------------

    def get_card_info(self) -> dict[str, Any]:
        """Return structured campus card balance and info from the İTÜ Portal."""
        html, url = self.ninova.get_portal_html("/apps/default/")
        from .parsing import extract_campus_card_info

        return extract_campus_card_info(html, url, base_url=self.base_url)

    def save_transcript_pdf(
        self,
        output_dir: str | Path,
        *,
        english: bool = False,
        filename: str | None = None,
    ) -> dict[str, Any]:
        payload = self.get_transcript_preview(english=english)
        b64 = payload.get("belgeAsByteArray") if isinstance(payload, dict) else None
        if not b64:
            raise ObsError("Transcript preview did not include belgeAsByteArray.")
        raw = base64.b64decode(b64)
        target_dir = Path(output_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        name = filename or ("transcript-en.pdf" if english else "transcript-tr.pdf")
        path = target_dir / name
        path.write_bytes(raw)
        return {
            "path": str(path),
            "size_bytes": len(raw),
            "english": english,
            "statusCode": payload.get("statusCode") if isinstance(payload, dict) else None,
        }

    def default_program_id(self) -> int:
        programs = self.list_programs()
        items = programs.get("ogrenciProgramBilgiListesi") or []
        if not items:
            raise ObsError("No OBS student programs found.")
        return int(items[0]["ogrenciProgramId"])

    def resolve_semester(
        self,
        semester: str | int | None = None,
    ) -> dict[str, Any]:
        data = self.list_semesters()
        items = data.get("ogrenciDonemListesi") or []
        if not items:
            raise ObsError("No OBS semesters found.")
        if semester is None:
            return items[-1]
        target_raw = str(semester).strip()
        target = normalize_lookup_text(target_raw)
        target_compact = "".join(ch for ch in target if ch.isalnum())
        for item in items:
            if str(item.get("akademikDonemId")) == target_raw:
                return item
            if str(item.get("donemKodu")) == target_raw:
                return item
            name = normalize_lookup_text(item.get("akademikDonemAdi"))
            name_en = normalize_lookup_text(item.get("akademikDonemAdiEN"))
            name_compact = "".join(ch for ch in name if ch.isalnum())
            name_en_compact = "".join(ch for ch in name_en if ch.isalnum())
            if target and (target in name or target in name_en):
                return item
            if target_compact and (
                target_compact in name_compact or target_compact in name_en_compact
            ):
                return item
            # token match: "2025-2026 guz" -> both tokens present (ü->u via normalize)
            tokens = [tok for tok in target.replace("-", " ").split() if tok]
            if tokens and all(tok in name or tok in name_en for tok in tokens):
                return item
        options = ", ".join(
            f"{i.get('akademikDonemId')}:{i.get('akademikDonemAdi')}" for i in items[-8:]
        )
        raise ObsError(f"Semester not found: {semester}. Examples: {options}")


class ObsPublicClient:
    """Client for OBS **public** (no-auth) endpoints: course catalog and prerequisites.

    These endpoints return HTML, not JSON, so parsing is handled by
    ``parsing.py`` extractors.  No JWT or login is needed.
    """

    DEFAULT_BASE_URL = "https://obs.itu.edu.tr"
    DEFAULT_CACHE_TTL = 3600.0  # 1 hour — course catalog rarely changes
    DEFAULT_REQUEST_DELAY_SECONDS = 0.12
    ALLOWED_PUBLIC_HOSTS = frozenset({"obs.itu.edu.tr", "www.takvim.sis.itu.edu.tr"})

    def __init__(
        self,
        session: requests.Session | None = None,
        base_url: str | None = None,
        cache_ttl: float | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("NINOVA_OBS_BASE_URL") or self.DEFAULT_BASE_URL).rstrip("/")
        parsed_base = urlparse(self.base_url)
        if parsed_base.scheme != "https" or (parsed_base.hostname or "").lower() != "obs.itu.edu.tr":
            raise ObsError("NINOVA_OBS_BASE_URL must be https://obs.itu.edu.tr")
        self._session = session
        cttl = cache_ttl if cache_ttl is not None else float(
            os.getenv("NINOVA_OBS_PUBLIC_CACHE_TTL_SECONDS") or self.DEFAULT_CACHE_TTL
        )
        self._cache: TtlCache[Any] = TtlCache(cttl)
        self._schedule_cache_ttl = float(
            os.getenv("NINOVA_PUBLIC_SCHEDULE_CACHE_TTL_SECONDS")
            or self.DEFAULT_SCHEDULE_CACHE_TTL
        )
        self._course_index: dict[str, int] | None = None  # normalised code → bransKoduId
        self._min_request_interval = _request_delay_seconds()
        self._last_request_at = 0.0

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": DEFAULT_HEADERS.get("User-Agent", "itu-mcp/0.2"),
                "Accept-Language": DEFAULT_HEADERS.get("Accept-Language", "tr-TR,tr;q=0.9,en;q=0.7"),
            })
        return self._session

    # -- internal helpers -------------------------------------------------

    def _throttle(self) -> None:
        if self._min_request_interval <= 0:
            return
        now = time.monotonic()
        remaining = self._min_request_interval - (now - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in self.ALLOWED_PUBLIC_HOSTS
        ):
            raise ObsError("OBS public request destination is not allowlisted")

    def _safe_request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        try:
            return request_with_safe_redirects(
                self.session,
                method,
                url,
                validate_url=self._validate_url,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise ObsError(f"OBS public request failed safely: {exc}") from exc

    def _get_html(self, path: str, *, params: dict[str, Any] | None = None) -> tuple[str, str]:
        """GET a public OBS page and return ``(html, final_url)``."""
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        self._throttle()
        response = self._safe_request("GET", url, params=params, timeout=30)
        if response.status_code >= 400:
            raise ObsError(
                f"OBS public endpoint {path} returned HTTP {response.status_code}"
            )
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or response.encoding
        return response.text, response.url

    def _post_json(self, path: str, *, data: dict[str, Any]) -> Any:
        """POST form data to an exact public OBS route and decode JSON."""
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        self._throttle()
        response = self._safe_request("POST", url, data=data, timeout=30)
        if response.status_code >= 400:
            raise ObsError(f"OBS public endpoint {path} returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ObsError(f"OBS public endpoint {path} did not return JSON") from exc

    # -- course index -----------------------------------------------------

    def _build_course_index(self) -> dict[str, int]:
        """Fetch and parse the master course-select dropdown.

        Returns a mapping of normalised course code → ``DersBransKoduId``.
        Cached for the session.
        """
        cached = self._cache.get("course_index")
        if cached is not None:
            return cached  # type: ignore[return-value]

        html, url = self._get_html("/public/GenelTanimlamalar/DersOnsartList")
        options = extract_course_select_options(html, url)
        index: dict[str, int] = {}
        for opt in options:
            text = opt.get("text") or ""
            value_str = opt.get("value") or ""
            try:
                numeric_id = int(value_str)
            except (ValueError, TypeError):
                continue

            # The option text is usually "DEPARTMENT_CODE - Department Name".
            # Store multiple variations so lookups succeed with or without spaces,
            # trailing letters, etc.
            parts = text.split("-", 1)
            dept_code = clean_text(parts[0]) if parts else ""
            if dept_code:
                index[normalize_lookup_text(dept_code)] = numeric_id

            # Also index the raw value as key for partial-match lookups later.
            index[value_str] = numeric_id

        self._cache.set("course_index", index)
        self._course_index = index
        return index

    # -- course search ----------------------------------------------------

    def search_courses(self, query: str) -> list[dict[str, Any]]:
        """Search the OBS public course catalog."""
        html, url = self._get_html(
            "/public/DersBilgi/Search",
            params={"searchText": query.strip()},
        )
        return extract_course_search_results(html, url)

    # -- prerequisites ----------------------------------------------------

    def get_prerequisites(self, course_id: int | str) -> dict[str, Any]:
        """Return prerequisites for a course identified by its ``DersBransKoduId``.

        Tries several URL patterns that OBS is known to use.
        """
        cid = str(course_id)
        cache_key = f"prereq:{cid}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        errors: list[str] = []
        html = url = ""

        # Pattern 1: REST-style GET with id as path segment
        try:
            html, url = self._get_html(f"/public/GenelTanimlamalar/DersOnsartList/{cid}")
        except ObsError as exc:
            errors.append(f"GET /{cid}: {exc}")

        # Pattern 2: GET with query param
        if not html or not self._looks_like_prereq_page(html):
            try:
                html, url = self._get_html(
                    "/public/GenelTanimlamalar/DersOnsartDetay",
                    params={"dersBransKoduId": cid},
                )
            except ObsError as exc:
                errors.append(f"GET DersOnsartDetay: {exc}")

        # Pattern 3: The DersOnsartList page itself with a POST-like approach —
        # try /DersOnsartListesi
        if not html or not self._looks_like_prereq_page(html):
            try:
                html, url = self._get_html(
                    "/public/GenelTanimlamalar/DersOnsartListesi",
                    params={"DersBransKoduId": cid},
                )
            except ObsError as exc:
                errors.append(f"GET DersOnsartListesi: {exc}")

        if not html:
            result: dict[str, Any] = {
                "course_id": cid,
                "available": False,
                "error": "Tüm önşart URL desenleri başarısız oldu.",
                "tried_patterns": errors,
            }
            self._cache.set(cache_key, result)
            return result

        parsed = extract_prerequisite_list(html, url, base_url=self.base_url)
        parsed["course_id"] = cid
        parsed["available"] = bool(parsed.get("prerequisites") or parsed.get("raw_tables"))
        if errors:
            parsed["_fallback_attempts"] = errors
        self._cache.set(cache_key, parsed, ttl_seconds=self._schedule_cache_ttl)
        return parsed

    def get_postrequisites(self, course_id: int | str) -> dict[str, Any]:
        """Find courses that list *this* course as a prerequisite.

        This requires scanning the full catalog, so results are cached
        aggressively.
        """
        cid = str(course_id)
        cache_key = f"postreq:{cid}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        target_code = self._resolve_brans_id_to_code(course_id)
        if target_code is None:
            return {
                "course_id": cid,
                "available": False,
                "error": f"Course id {cid} could not be resolved to a code for reverse lookup.",
            }

        # Build the full index, then scan for matching prerequisites.
        index = self._build_course_index()
        postrequisites: list[dict[str, Any]] = []
        scanned = 0
        max_scan = 200  # safety cap

        for _, other_id in index.items():
            if scanned >= max_scan:
                break
            if str(other_id) == cid:
                continue
            try:
                prereq_data = self.get_prerequisites(other_id)
            except ObsError:
                continue
            scanned += 1

            prereqs = prereq_data.get("prerequisites") or []
            for prereq in prereqs:
                prereq_code = prereq.get("code") or ""
                if normalize_lookup_text(prereq_code) == normalize_lookup_text(target_code):
                    postrequisites.append({
                        "code": prereq_data.get("_resolved_code", str(other_id)),
                        "course_id": str(other_id),
                        "prerequisite_type": prereq.get("type"),
                        "group": prereq.get("group"),
                    })
                    break

        result: dict[str, Any] = {
            "course_id": cid,
            "course_code": target_code,
            "available": True,
            "postrequisites": postrequisites,
            "scanned_courses": scanned,
            "note": (
                "Postrequisite lookup scans the full course index. "
                f"Only {scanned} courses were checked."
            ) if scanned >= max_scan else None,
        }
        self._cache.set(cache_key, result)
        return result

    # -- course info (public) ---------------------------------------------

    def get_course_info_public(self, course_code: str) -> dict[str, Any]:
        """Return public catalog metadata for a course code."""
        results = self.search_courses(course_code)
        target = normalize_lookup_text(course_code)
        best: dict[str, Any] | None = None
        for item in results:
            item_code = normalize_lookup_text(item.get("code") or "")
            if target == item_code:
                best = item
                break
            if target in item_code and best is None:
                best = item
        if best is None and results:
            best = results[0]
        return {
            "query": course_code,
            "found": best is not None,
            "course": best,
            "all_results_count": len(results),
        }

    # -- code resolution ---------------------------------------------------

    def resolve_course_code(self, course_code: str) -> dict[str, Any]:
        """Resolve a course code like ``"BBF 201E"`` to an OBS bransKoduId.

        Returns ``{"code": str, "brans_kodu_id": int, "name": str, ...}``.
        Raises ``ObsError`` if no match is found.
        """
        raw = course_code.strip()
        target = normalize_lookup_text(raw)

        # Try the department-level index first (quick).
        index = self._build_course_index()
        if target in index:
            return {
                "code": raw.upper(),
                "brans_kodu_id": index[target],
                "source": "dept_index",
            }

        # Also try prefix match (e.g. "BBF 201E" → department code "bbf").
        for key, value in index.items():
            if len(key) >= 2 and target.startswith(key):
                return {
                    "code": raw.upper(),
                    "brans_kodu_id": value,
                    "matched_key": key,
                    "source": "dept_index_prefix",
                }

        # Fallback: search the catalog.
        results = self.search_courses(raw)
        if not results:
            raise ObsError(
                f"Course not found in OBS public catalog: {raw}. "
                "Try a different code format (e.g. 'BBF 201E' vs 'BBF201E')."
            )

        # Prefer exact code match.
        for item in results:
            if normalize_lookup_text(item.get("code") or "") == target:
                return {
                    "code": item.get("code") or raw.upper(),
                    "name": item.get("name"),
                    "brans_kodu_id": None,
                    "url": item.get("url"),
                    "source": "search_exact",
                }

        best = results[0]
        return {
            "code": best.get("code") or raw.upper(),
            "name": best.get("name"),
            "brans_kodu_id": None,
            "url": best.get("url"),
            "source": "search_fuzzy",
        }

    # -- DersProgram (public schedule) ------------------------------------

    PROGRAM_TYPE_MAP: dict[str, str] = {
        "ls": "LS",
        "lisans": "LS",
        "lu": "LU",
        "lisansüstü": "LU",
        "lisansustu": "LU",
        "yüksek lisans": "LU",
        "yuksek lisans": "LU",
        "öl": "ÖL",
        "önlisans": "ÖL",
        "onlisans": "ÖL",
        "lui": "LUİ",
        "luİ": "LUİ",
        "lisansüstü 2": "LUİ",
        "lisansustu 2": "LUİ",
        "lisansüstü 2.öğretim": "LUİ",
    }

    DEFAULT_SCHEDULE_CACHE_TTL = 60.0

    @classmethod
    def _normalize_program_type(cls, raw: str) -> str:
        key = normalize_lookup_text(raw)
        if key in cls.PROGRAM_TYPE_MAP:
            return cls.PROGRAM_TYPE_MAP[key]
        if raw.upper() in {"LS", "LU", "ÖL", "LUİ"}:
            return raw.upper()
        valid = ", ".join(sorted(set(cls.PROGRAM_TYPE_MAP.values())))
        raise ObsError(
            f"Geçersiz program tipi: {raw!r}. Geçerli değerler: {valid} "
            f"(veya 'Lisans', 'Lisansüstü', 'Önlisans')."
        )

    def list_departments(self, program_type: str) -> list[dict[str, Any]]:
        """Return the department list for a program type (no auth)."""
        pt = self._normalize_program_type(program_type)
        cache_key = f"depts:{pt}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        html, _url = self._get_html(
            "/public/DersProgram/SearchBransKoduByProgramSeviye",
            params={"programSeviyeTipiAnahtari": pt},
        )
        import json as _json

        try:
            raw: list[dict[str, Any]] = _json.loads(html)
        except _json.JSONDecodeError:
            raise ObsError("Bölüm listesi JSON parse edilemedi; OBS yanıtı değişmiş olabilir.")

        departments = [
            {
                "brans_kodu_id": item.get("bransKoduId"),
                "code": item.get("dersBransKodu"),
            }
            for item in raw
        ]
        self._cache.set(cache_key, departments)
        return departments

    def get_active_semester(self, program_type: str) -> str:
        """Return the active semester name for a program type."""
        pt = self._normalize_program_type(program_type)
        cache_key = f"semester:{pt}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        html, _url = self._get_html(
            "/public/DersProgram/GetAktifDonemByProgramSeviye",
            params={"programSeviyeTipiAnahtari": pt},
        )
        import json as _json

        try:
            data = _json.loads(html)
        except _json.JSONDecodeError:
            raise ObsError("Dönem bilgisi JSON parse edilemedi.")

        semester = data.get("aktifDonem") or "Bilinmeyen Dönem"
        self._cache.set(cache_key, semester)
        return semester

    def get_course_schedule(
        self,
        program_type: str,
        department: str,
    ) -> dict[str, Any]:
        """Fetch and parse the course schedule for a department."""
        pt = self._normalize_program_type(program_type)
        dept_id = self._resolve_department(pt, department)
        cache_key = f"schedule:{pt}:{dept_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        html, url = self._get_html(
            "/public/DersProgram/DersProgramSearch",
            params={
                "ProgramSeviyeTipiAnahtari": pt,
                "DersBransKoduId": str(dept_id),
            },
        )

        dept_name = getattr(self, "_dept_cache", {}).get(dept_id, {}).get("code", department)

        from .parsing import extract_course_schedule_table

        parsed = extract_course_schedule_table(html, url, base_url=self.base_url)
        parsed["program_type"] = pt
        parsed["department_code"] = dept_name
        parsed["department_id"] = dept_id

        try:
            parsed["semester"] = self.get_active_semester(pt)
        except ObsError:
            parsed["semester"] = None

        self._cache.set(cache_key, parsed)
        return parsed

    def get_course_schedule_by_crn(
        self,
        program_type: str,
        department: str,
        crn: str,
    ) -> dict[str, Any]:
        """Return a single course from the schedule, matched by CRN."""
        schedule = self.get_course_schedule(program_type, department)
        courses = schedule.get("courses") or []
        target = str(crn).strip()
        for course in courses:
            if str(course.get("crn")) == target:
                return {
                    **schedule,
                    "count": 1,
                    "courses": [course],
                    "filtered_by_crn": crn,
                }
        raise ObsError(
            f"CRN {crn} bulunamadı. "
            f"Mevcut CRN'ler: {', '.join(str(c.get('crn')) for c in courses[:20])}"
        )

    def get_prerequisite_detail(
        self,
        brans_kodu: str,
        ders_no: str,
    ) -> dict[str, Any]:
        """Return prerequisite information from a course's OBS detail page."""
        cache_key = f"prereq_detail:{brans_kodu}:{ders_no}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        html, url = self._get_html(
            "/public/DersBilgi",
            params={"bransKodu": brans_kodu.upper(), "dersNo": ders_no},
        )

        from .parsing import extract_prerequisite_list

        parsed = extract_prerequisite_list(html, url, base_url=self.base_url)
        parsed["brans_kodu"] = brans_kodu.upper()
        parsed["ders_no"] = ders_no
        self._cache.set(cache_key, parsed)
        return parsed

    # -- department resolution --------------------------------------------

    def _resolve_department(self, program_type: str, department: str) -> int:
        """Resolve a department code or name to its numeric ``bransKoduId``."""
        depts = self.list_departments(program_type)
        target = normalize_lookup_text(department)

        self._dept_cache: dict[int, dict[str, Any]] = getattr(self, "_dept_cache", {})

        # Exact code match
        for d in depts:
            if normalize_lookup_text(d.get("code") or "") == target:
                self._dept_cache[int(d["brans_kodu_id"])] = d
                return int(d["brans_kodu_id"])

        # Try numeric ID
        if department.strip().isdigit():
            did = int(department.strip())
            if any(d.get("brans_kodu_id") == did for d in depts):
                return did

        # Fuzzy match
        for d in depts:
            code = normalize_lookup_text(d.get("code") or "")
            if code and (target in code or code in target):
                self._dept_cache[int(d["brans_kodu_id"])] = d
                return int(d["brans_kodu_id"])

        available = ", ".join(
            f"{d.get('code')} (id={d.get('brans_kodu_id')})" for d in depts[:20]
        )
        raise ObsError(
            f"Bölüm kodu bulunamadı: {department!r}. "
            f"Mevcut bölümler (ilk 20): {available}"
        )

    # -- internal helpers -------------------------------------------------

    def _resolve_brans_id_to_code(self, course_id: int | str) -> str | None:
        """Best-effort reverse lookup: bransKoduId → course department code."""
        cid = str(course_id)
        index = self._build_course_index()
        for key, value in index.items():
            if str(value) == cid:
                return key
        return None

    @staticmethod
    def _looks_like_prereq_page(html: str) -> bool:
        """Heuristic: does the HTML contain prerequisite-related content?"""
        if not html or len(html) < 200:
            return False
        lower = html[:8000].casefold()
        return any(
            token in lower
            for token in ("onsart", "önşart", "on sart", "ön sart", "dersonsart")
        )


    # -- academic calendar (takvim.sis.itu.edu.tr) -----------------------

    def get_academic_calendar(self) -> dict[str, Any]:
        """Fetch the İTÜ academic calendar (public, no auth)."""
        from .parsing import extract_academic_calendar

        cached = self._cache.get("academic_calendar")
        if cached is not None:
            return cached  # type: ignore[return-value]
        url = "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/EN/academic-calendar/index.php"
        self._throttle()
        resp = self._safe_request("GET", url, timeout=30, headers={
            "User-Agent": DEFAULT_HEADERS.get("User-Agent", "itu-mcp"),
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
        })
        if resp.status_code >= 400:
            raise ObsError(f"Academic calendar returned HTTP {resp.status_code}")
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or resp.encoding
        result = extract_academic_calendar(resp.text, resp.url)
        return self._cache.set("academic_calendar", result)

    # -- degree plans ----------------------------------------------------

    VALID_PLAN_TYPES = {
        "on-lisans",
        "lisans",
        "cap",
        "yandal",
        "muhendislik-tamamlama",
        "bilimsel-hazirlik",
        "yuksek-lisans",
        "tezsiz-yuksek-lisans",
        "yuksek-lisans-ikinci-ogretim",
        "doktora",
        "uolp",
    }

    def list_degree_faculties(self) -> list[dict[str, Any]]:
        """List official faculty/unit ids exposed by the public degree-plan form."""
        cached = self._cache.get("degree_faculties")
        if cached is not None:
            return cached  # type: ignore[return-value]
        html, _ = self._get_html("/public/DersPlan")
        soup = make_soup(html)
        select = soup.select_one("select#akademikBirimId")
        if select is None:
            raise ObsError("Ders planı akademik birim listesi bulunamadı.")
        faculties = []
        for option in select.select("option[value]"):
            raw = str(option.get("value") or "").strip()
            if raw.isdigit():
                faculties.append({"faculty_id": int(raw), "faculty_name": clean_text(option.get_text(" ", strip=True))})
        if not faculties:
            raise ObsError("Ders planı akademik birim listesi boş döndü.")
        return self._cache.set("degree_faculties", faculties)

    def list_degree_programs(self, faculty_id: int, plan_type: str = "lisans") -> list[dict[str, Any]]:
        """List degree programs for a faculty and official plan type."""
        plan_type = plan_type.strip().lower()
        if plan_type not in self.VALID_PLAN_TYPES:
            raise ObsError(f"Geçersiz ders planı tipi: {plan_type}")
        cache_key = f"degree_programs:{faculty_id}:{plan_type}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        payload = self._post_json(
            "/public/DersPlan/GetAkademikProgramByBirimIdAndPlanTipi",
            data={"birimId": str(faculty_id), "planTipiKodu": plan_type},
        )
        programs = [
            {"program_code": item.get("programKodu"), "program_name": item.get("programAdi")}
            for item in (payload or [])
            if isinstance(item, dict)
        ]
        return self._cache.set(cache_key, programs)

    def list_degree_plans(
        self,
        *,
        faculty_id: int,
        program_code: str,
        plan_type: str = "lisans",
    ) -> dict[str, Any]:
        """List historical plan versions for an official degree program."""
        plan_type = plan_type.strip().lower()
        if plan_type not in self.VALID_PLAN_TYPES:
            raise ObsError(f"Geçersiz ders planı tipi: {plan_type}")
        programs = self.list_degree_programs(faculty_id, plan_type)
        valid_codes = {str(item.get("program_code") or "").upper() for item in programs}
        code = program_code.strip().upper()
        if code not in valid_codes:
            raise ObsError(
                f"Program kodu {code!r}, fakülte {faculty_id} / {plan_type} için bulunamadı. "
                f"Geçerli kodlar: {', '.join(sorted(valid_codes))}"
            )
        cache_key = f"degree_plans:{faculty_id}:{plan_type}:{code}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        html, url = self._get_html(
            "/public/DersPlan/DersPlanlariList",
            params={"PlanTipiKodu": plan_type, "programKodu": code, "FakulteId": str(faculty_id)},
        )
        from .public_parsing import extract_degree_plan_list

        result = extract_degree_plan_list(html, url)
        result.update({"faculty_id": faculty_id, "program_code": code, "plan_type": plan_type})
        return self._cache.set(cache_key, result)

    def get_degree_plan(self, plan_id: int | str) -> dict[str, Any]:
        """Return semester-by-semester courses for one official plan version."""
        raw = str(plan_id).strip()
        if not raw.isdigit() or int(raw) <= 0:
            raise ObsError("plan_id pozitif bir tamsayı olmalı.")
        cache_key = f"degree_plan:{raw}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        html, url = self._get_html(f"/public/DersPlan/DersPlanDetay/{raw}")
        from .public_parsing import extract_degree_plan_detail

        result = extract_degree_plan_detail(html, url)
        result["plan_id"] = int(raw)
        return self._cache.set(cache_key, result)


def redact_obs_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove highly sensitive personal fields from OBS profile payloads."""
    import copy

    data = copy.deepcopy(payload)
    personal = data.get("kisiselBilgiler")
    if isinstance(personal, dict):
        for key in ("kimlikNo", "tcKimlikNo", "tckn", "pasaportNo"):
            if key in personal and personal[key]:
                personal[key] = "***REDACTED***"
    contacts = data.get("iletisimBilgiList")
    if isinstance(contacts, list):
        for group in contacts:
            for item in group.get("iletisimBilgiList") or []:
                if not isinstance(item, dict):
                    continue
                tip = (item.get("iletisimTipiAdiTR") or item.get("iletisimTipiAdiEN") or "").casefold()
                if any(token in tip for token in ("telefon", "phone", "cep", "mobile", "adres", "address")):
                    if item.get("degeri"):
                        item["degeri"] = "***REDACTED***"
    return data
