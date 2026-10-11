"""Client for İTÜ OBS (obs.itu.edu.tr) student portal JSON APIs.

OBS is a Vue SPA. Auth reuses the same İTÜ SSO cookies as Ninova, then
exchanges them for a short-lived JWT via ``/ogrenci/auth/jwt``.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from .cache import TtlCache
from .client import DEFAULT_HEADERS, _request_delay_seconds
from .http_security import request_with_safe_redirects
from .obs_errors import ObsError
from .obs_registration_api import ObsRegistrationApi
from .obs_redaction import (
    _EMAIL as _EMAIL,
    _REDACTION_NOTE as _REDACTION_NOTE,
    _SENSITIVE_CONTACT_TYPE as _SENSITIVE_CONTACT_TYPE,
    _SENSITIVE_KEY as _SENSITIVE_KEY,
    REDACTED,
    redact_obs_profile,
)
from .obs_semesters import (
    _SEASON_WORDS as _SEASON_WORDS,
    _TERM_FILLER_WORDS as _TERM_FILLER_WORDS,
    _resolve_semester_by_name as _resolve_semester_by_name,
    _semester_lookup_text as _semester_lookup_text,
    _semester_recency as _semester_recency,
    _semester_tokens as _semester_tokens,
    describe_semester,
)
from .obs_session import DEFAULT_OBS_BASE_URL, JWT_PATH, STUDENT_HOME
from .obs_student_api import ObsStudentRecordsApi
from .parsing import (
    clean_text,
    extract_course_search_results,
    extract_course_select_options,
    extract_prerequisite_list,
    make_soup,
    normalize_lookup_text,
)

__all__ = [
    "DEFAULT_OBS_BASE_URL",
    "JWT_PATH",
    "ObsClient",
    "ObsError",
    "ObsPublicClient",
    "REDACTED",
    "STUDENT_HOME",
    "describe_semester",
    "redact_obs_profile",
]


class ObsClient(ObsStudentRecordsApi, ObsRegistrationApi):
    """Authenticated client for the OBS student API.

    The session and request core is ``ObsSessionCore``; the endpoint groups
    are ``ObsStudentRecordsApi`` and ``ObsRegistrationApi``.
    """


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
        """Read the public course information form's actual result endpoint."""
        from .archive import split_course_code

        try:
            branch, number = split_course_code(query)
        except ValueError as exc:
            raise ObsError("A full course code is required for an OBS catalog lookup.") from exc
        html, url = self._get_html(
            "/public/DersBilgi/DersBilgiSearch",
            params={"bransKodu": branch, "dersNo": number},
        )
        results = extract_course_search_results(html, url)
        if not results:
            raise ObsError("OBS did not return recognizable course information for this code.")
        exact = f"{branch} {number}"
        results.sort(key=lambda row: row.get("code") != exact)
        return results

    # -- prerequisites ----------------------------------------------------

    def get_prerequisites(self, course_id: int | str) -> dict[str, Any]:
        """Read an exact course rule, preserving its AND/OR expression.

        Numeric identifiers refer to branches, not individual courses. Legacy
        numeric calls return branch data with an explicit unknown course verdict.
        """
        from .archive import split_course_code

        cid = str(course_id).strip()
        if cid.isascii() and cid.isdigit():
            html, url = self._get_html(
                "/public/GenelTanimlamalar/OnsartAra",
                params={"DersBransKoduId": cid},
            )
            parsed = extract_prerequisite_list(html, url, base_url=self.base_url)
            parsed.update({
                "course_id": cid,
                "scope": "branch",
                "available": bool(parsed.get("prerequisites") or parsed.get("raw_tables")),
                "prerequisite_status": "unknown",
                "prerequisite_status_note": "A branch ID cannot identify a course's prerequisites. Supply the full course code.",
            })
            return parsed
        try:
            branch, number = split_course_code(cid)
        except ValueError as exc:
            raise ObsError("A full course code such as BLG 223E is required.") from exc
        return self.get_prerequisite_detail(branch, number)

    def get_postrequisites(
        self, course_id: int | str,
        progress: Callable[[float, float | None, str | None], None] | None = None,
    ) -> dict[str, Any]:
        """Find exact course references in official branch prerequisite rules.

        ``progress`` is an optional ``(done, total, message)`` callback, called
        once per branch of a cold scan. Its failures are ignored.
        """
        from .archive import normalize_course_code
        from .prerequisites import flatten_courses

        try:
            target_code = normalize_course_code(str(course_id))
        except ValueError as exc:
            raise ObsError("A full course code is required for postrequisite lookup.") from exc
        cache_key = f"postreq:{target_code}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        index = self._build_course_index()
        branches = sorted({key.upper() for key in index if not key.isdigit()})
        max_scan = 200
        postrequisites: list[dict[str, Any]] = []
        unknown_branches: list[str] = []
        sources: list[str] = []
        scanned_courses = 0
        scan_total = len(branches[:max_scan])
        for position, branch in enumerate(branches[:max_scan]):
            if progress is not None:
                try:
                    progress(position, scan_total, f"Ön şart tablosu taranıyor ({position + 1}/{scan_total}): {branch}")
                except Exception:  # nosec B110
                    # Progress is advisory. The callback is supplied by the
                    # caller and must never change the outcome of the scan.
                    pass
            try:
                data = self.get_branch_prerequisites(branch)
            except ObsError:
                unknown_branches.append(branch)
                continue
            if not data.get("table_parsed"):
                unknown_branches.append(branch)
                continue
            if data.get("url"):
                sources.append(data["url"])
            for code, rule in (data.get("rules") or {}).items():
                scanned_courses += 1
                matches = [leaf for leaf in flatten_courses(rule.get("requirement_tree"))
                           if leaf.get("code") == target_code]
                if matches:
                    postrequisites.append({
                        "code": code,
                        "course_id": code,
                        "name": rule.get("course_name"),
                        "prerequisite_type": "prerequisite_reference",
                        "group": None,
                        "requirement_tree": rule.get("requirement_tree"),
                        "expression": rule.get("expression"),
                        "minimum_grades": rule.get("minimum_grades"),
                        "credit_requirement": rule.get("credit_requirement"),
                        "credit_requirement_text": rule.get("credit_requirement_text"),
                    })
        if progress is not None:
            try:
                progress(scan_total, scan_total, "Ön şart taraması tamamlandı")
            except Exception:  # nosec B110
                # Advisory, as above.
                pass
        complete = bool(branches) and not unknown_branches and len(branches) <= max_scan
        result: dict[str, Any] = {
            "course_id": target_code,
            "course_code": target_code,
            "available": bool(sources),
            "complete": complete,
            "postrequisite_status": "has_postrequisites" if postrequisites else "no_postrequisites" if complete else "unknown",
            "postrequisites": sorted(postrequisites, key=lambda row: row["code"]),
            "scanned_courses": scanned_courses,
            "scanned_branches": len(branches[:max_scan]),
            "unknown_branches": unknown_branches,
            "unscanned_branches": branches[max_scan:],
            "sources": sources,
            "note": "These courses reference the selected course in their prerequisite expressions. Other alternatives and minimum grades still apply. "
                    + ("All listed branches were checked." if complete else "Coverage is incomplete; an empty result does not prove no postrequisites."),
            "untrusted_external_content": True,
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

        # Stamped once per real fetch, so a cached copy still says how old it is.
        parsed["fetched_at"] = datetime.now(tz=UTC).isoformat()
        self._cache.set(cache_key, parsed, ttl_seconds=self._schedule_cache_ttl)
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
        """Read the authoritative branch rule for an exact course code."""
        from .archive import normalize_course_code, split_course_code
        from .prerequisites import flatten_courses

        try:
            code = normalize_course_code(f"{brans_kodu} {ders_no}")
            branch, number = split_course_code(code)
        except ValueError as exc:
            raise ObsError("A valid branch and course number are required, such as BLG and 223E.") from exc
        cache_key = f"prereq_detail:{code}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        parsed: dict[str, Any] = {
            "course_id": code,
            "course_code": code,
            "brans_kodu": branch,
            "ders_no": number,
            "title": code,
            "prerequisites": [],
            "requirement_tree": None,
            "prerequisite_status": "unknown",
            "available": False,
            "parse_warnings": None,
            "raw_tables": [],
            "text_excerpt": "",
            "untrusted_external_content": True,
        }
        try:
            branch_rules = self.get_branch_prerequisites(branch)
        except ObsError as exc:
            parsed["prerequisite_status_note"] = (
                f"The official branch prerequisite table could not be read: {exc}. An empty result is not evidence of no prerequisites."
            )
        else:
            rule = branch_rules.get("rules", {}).get(code)
            parsed["url"] = branch_rules.get("url")
            parsed["prerequisite_source"] = branch_rules.get("url")
            if rule is not None:
                leaves = flatten_courses(rule.get("requirement_tree"))
                seen: set[tuple[str, str | None]] = set()
                for leaf in leaves:
                    identity = (leaf["code"], leaf.get("min_grade"))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    parsed["prerequisites"].append({
                        "code": leaf["code"], "name": None,
                        "min_grade": leaf.get("min_grade"),
                        "type": "prerequisite_reference", "group": None,
                    })
                parsed["available"] = True
                parsed["prerequisite_status"] = "has_prerequisites" if leaves or rule.get("credit_requirement") is not None else "unknown"
                parsed["official_prerequisite"] = rule
                parsed["requirement_tree"] = rule.get("requirement_tree")
                parsed["credit_requirement"] = rule.get("credit_requirement")
                parsed["credit_requirement_text"] = rule.get("credit_requirement_text")
                parsed["text_excerpt"] = rule.get("expression") or ""
                parsed["prerequisite_status_note"] = "The prerequisite list contains references, not a list of courses that are all mandatory. Use the full requirement_tree for AND/OR alternatives and minimum grades."
            elif branch_rules.get("table_parsed"):
                # An invalid code is also absent from the prerequisite table.
                # Verify its catalog identity before claiming it has no rule.
                try:
                    catalog = self.search_courses(code)
                except ObsError:
                    catalog = []
                if any(row.get("code") == code for row in catalog):
                    parsed["available"] = True
                    parsed["prerequisite_status"] = "no_prerequisites"
                    parsed["prerequisite_status_note"] = f"{code} exists in the official catalog and has no entry in the parsed {branch} prerequisite table."
                else:
                    parsed["prerequisite_status_note"] = "The course is absent from the prerequisite table, but its exact catalog identity could not be verified."
            else:
                parsed["prerequisite_status_note"] = "The official branch prerequisite table could not be parsed; an empty result is not evidence of no prerequisites."
        if parsed["prerequisite_status"] != "unknown":
            self._cache.set(cache_key, parsed)
        return parsed

    def get_branch_prerequisites(self, branch: str) -> dict[str, Any]:
        """Return the official prerequisite rules for every course in a branch.

        Reads ``/public/GenelTanimlamalar/OnsartAra``, the same table the OBS
        "Önşartlar" page renders. This is the only public source that gives the
        full Ve/Veya expression with minimum grades and the credit requirement,
        and it covers 4-digit capstone codes that the per-course page does not.
        """
        key = branch.strip().upper()
        cache_key = f"branch_prereq:{key}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        index = self._build_course_index()
        branch_id = index.get(normalize_lookup_text(key))
        if branch_id is None:
            raise ObsError(f"Branş kodu OBS önşart listesinde bulunamadı: {branch!r}")

        html, url = self._get_html(
            "/public/GenelTanimlamalar/OnsartAra",
            params={"DersBransKoduId": branch_id},
        )

        from .prerequisites import extract_branch_prerequisites

        parsed = extract_branch_prerequisites(html, url, key)
        parsed["brans_kodu_id"] = branch_id
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

    # -- academic calendar (takvim.sis.itu.edu.tr) -----------------------

    # The page lists one whole academic year. The Turkish address is the one
    # the English page links to as its other language.
    ACADEMIC_CALENDAR_URLS = {
        "en": "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/EN/academic-calendar/index.php",
        "tr": "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/TR/akademik-takvim/index.php",
    }
    # Each academic year costs two requests (English and Turkish) when not cached.
    MAX_CALENDAR_ACADEMIC_YEARS = 2

    def get_academic_calendar(self, date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
        """Fetch the İTÜ academic calendar (public, no auth).

        Without dates the academic year the site shows by default is returned.
        With dates, the academic years those dates fall in are read instead,
        at most ``MAX_CALENDAR_ACADEMIC_YEARS`` of them; what could not be
        read is named in ``coverage_warning`` and ``missing_academic_years``.
        Every page is cached per language and year.
        """
        from datetime import date as _date

        from .academic_calendar import TIMEZONE, academic_years_for_range, combine_calendars, merge_calendar_languages
        from zoneinfo import ZoneInfo

        current = self._calendar_page("en", None)
        if current.get("coverage", {}).get("scope") != "academic_year" or not current.get("academic_year"):
            # No year list on the page: only what the page itself shows is known.
            return current
        today = datetime.now(ZoneInfo(TIMEZONE)).date()
        try:
            start = _date.fromisoformat(date_from) if date_from else None
            end = _date.fromisoformat(date_to) if date_to else None
        except ValueError:
            start = end = None
        wanted = [current["academic_year"]]
        if start or end:
            first, last = start or min(end, today), end or max(start, today)  # type: ignore[type-var]
            wanted = academic_years_for_range(first, last) if first <= last else wanted
        warnings: list[str] = []
        chosen = wanted
        if len(wanted) > self.MAX_CALENDAR_ACADEMIC_YEARS:
            def distance(label: str) -> int:
                return abs((_date(int(label[:4]) + 1, 2, 1) - today).days)
            chosen = sorted(sorted(wanted, key=distance)[: self.MAX_CALENDAR_ACADEMIC_YEARS])
            warnings.append(
                f"The requested dates span {len(wanted)} academic years; at most {self.MAX_CALENDAR_ACADEMIC_YEARS} "
                f"are read per call. Not read: {', '.join(y for y in wanted if y not in chosen)}."
            )
        ids = {item["academic_year"]: item["id"] for item in current.get("available_academic_years") or []}
        pages: list[dict[str, Any]] = []
        for label in chosen:
            if label == current["academic_year"]:
                english = current
                year_id = None
            elif label in ids:
                year_id = ids[label]
                try:
                    english = self._calendar_page("en", year_id)
                except ObsError as exc:
                    warnings.append(f"The {label} calendar could not be read: {exc}")
                    continue
                if english.get("academic_year") != label:
                    warnings.append(f"The site did not return the {label} calendar.")
                    continue
            else:
                warnings.append(f"The {label} academic year is not offered by the calendar page (older years are in its archive).")
                continue
            try:
                turkish = self._calendar_page("tr", year_id)
            except ObsError:
                turkish = {}  # reported through "languages" and "language_note"
            if turkish.get("academic_year") == label:
                english = merge_calendar_languages(english, turkish)
            pages.append(english)
        return combine_calendars(pages, warnings=warnings, requested_years=wanted, today=today)

    def _calendar_page(self, language: str, year_id: str | None) -> dict[str, Any]:
        from .parsing import extract_academic_calendar

        if year_id is not None and not year_id.isdigit():
            raise ObsError("Academic calendar year id must be numeric")
        cache_key = f"academic_calendar:{language}:{year_id or 'current'}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        url = self.ACADEMIC_CALENDAR_URLS[language]
        if year_id is not None:
            # The same address the page's own year selector navigates to.
            url += f"?akademikyil={year_id}&takvimadi=&katagori=&baslangic=&bitis="
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
        return self._cache.set(cache_key, result)

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
