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

import requests

from .client import NinovaAuthError, NinovaClient, NinovaError
from .parsing import normalize_lookup_text

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

    def ensure_ready(self) -> dict[str, Any]:
        """Ensure Ninova/SSO session exists and OBS JWT is available."""
        self.ninova.ensure_logged_in()
        self.session.get(self.base_url + STUDENT_HOME, timeout=30, allow_redirects=True)
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
        self.session.get(self.base_url + STUDENT_HOME, timeout=30, allow_redirects=True)
        response = self.session.get(self.base_url + JWT_PATH, timeout=30, allow_redirects=True)
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
        response = self.session.get(
            url,
            headers=self._headers(),
            params=params,
            timeout=45,
            allow_redirects=True,
        )
        if response.status_code in {401, 403}:
            # Refresh JWT once.
            self._get_jwt(force=True)
            self.ninova._throttle()
            response = self.session.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=45,
                allow_redirects=True,
            )
        if response.status_code >= 400:
            detail = response.text[:300]
            # Some OBS endpoints return 500 when data is not yet published.
            raise ObsError(
                f"OBS API {path} failed with HTTP {response.status_code}: {detail}"
            )
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
