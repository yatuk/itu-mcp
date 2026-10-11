"""Public OBS course schedule: program types, departments, sections."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .obs_errors import ObsError
from .obs_public_transport import ObsPublicTransport
from .parsing import normalize_lookup_text


# OBS'nin kendi formu bu iki anahtarı Türkçe harfsiz gönderir; harfli hali HTTP 500 döndürür.
_WIRE_PROGRAM_TYPES = {"ÖL": "OL", "LUİ": "LUI"}


def _wire_program_type(program_type: str) -> str:
    """The value the public schedule form submits for a canonical programme type."""
    return _WIRE_PROGRAM_TYPES.get(program_type, program_type)


class ObsPublicSchedule(ObsPublicTransport):
    """Department lists and the course schedule of the active semester."""

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

    @classmethod
    def _normalize_program_type(cls, raw: str) -> str:
        """The canonical programme type of an alias; case and Turkish marks do not matter."""
        found = _folded_program_types(cls.PROGRAM_TYPE_MAP).get(_program_type_key(raw))
        if found is not None:
            return found
        accepted = "; ".join(
            f"{value} ({', '.join(alias for alias, target in cls.PROGRAM_TYPE_MAP.items() if target == value)})"
            for value in dict.fromkeys(cls.PROGRAM_TYPE_MAP.values())
        )
        raise ObsError(
            f"Geçersiz program tipi: {raw!r}. Kabul edilen değerler: {accepted}. "
            "Büyük/küçük harf ve Türkçe karakter farkı önemsizdir."
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
            params={"programSeviyeTipiAnahtari": _wire_program_type(pt)},
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
            params={"programSeviyeTipiAnahtari": _wire_program_type(pt)},
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
                "ProgramSeviyeTipiAnahtari": _wire_program_type(pt),
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

    # -- department resolution --------------------------------------------

    def _resolve_department(self, program_type: str, department: str) -> int:
        """Resolve a department code or name to its numeric ``bransKoduId``."""
        target = normalize_lookup_text(department)
        if not target:
            # Empty text would equal a missing code and be part of every code.
            raise ObsError(
                "Bölüm kodu boş olamaz. 'BLG' veya 'MAT' gibi bir bölüm kodu verin; bölüm kodu "
                "ders kodunun harf kısmıdır (BLG 223E için BLG)."
            )
        depts = self.list_departments(program_type)

        self._dept_cache: dict[int, dict[str, Any]] = getattr(self, "_dept_cache", {})

        exact = next((d for d in depts if normalize_lookup_text(d.get("code") or "") == target), None)
        if exact is not None:
            return self._remember_department(exact)
        listed_id = _listed_department_id(depts, department)
        if listed_id is not None:
            return listed_id
        partial = next((d for d in depts if _codes_overlap(normalize_lookup_text(d.get("code") or ""), target)), None)
        if partial is not None:
            return self._remember_department(partial)
        available = ", ".join(
            f"{d.get('code')} (id={d.get('brans_kodu_id')})" for d in depts[:20]
        )
        raise ObsError(
            f"Bölüm kodu bulunamadı: {department!r}. "
            f"Mevcut bölümler (ilk 20): {available}"
        )

    def _remember_department(self, department: dict[str, Any]) -> int:
        """Keep a department found by its code, so the schedule can name it; return its id."""
        department_id = int(department["brans_kodu_id"])
        self._dept_cache[department_id] = department
        return department_id


def _program_type_key(value: Any) -> str:
    """The key an alias is looked up under: the same folding for the map and for the input."""
    return normalize_lookup_text(value if isinstance(value, str) else "")


_FOLDED_PROGRAM_TYPES: dict[int, tuple[dict[str, str], dict[str, str]]] = {}


def _folded_program_types(aliases: dict[str, str]) -> dict[str, str]:
    """``aliases`` with folded keys, plus every canonical value under its own folded key.

    Built once per map and rebuilt only when the map's content changes, so a
    key written with Turkish letters (``lisansüstü 2.öğretim``) is reachable.
    """
    cached = _FOLDED_PROGRAM_TYPES.get(id(aliases))
    if cached is not None and cached[0] == aliases:
        return cached[1]
    folded = {_program_type_key(value): value for value in aliases.values()}
    folded.update((_program_type_key(alias), value) for alias, value in aliases.items())
    folded.pop("", None)
    _FOLDED_PROGRAM_TYPES[id(aliases)] = (dict(aliases), folded)
    return folded


def _listed_department_id(depts: list[dict[str, Any]], department: str) -> int | None:
    """The number the caller gave, when it is the id of a listed department."""
    text = department.strip()
    if not text.isdigit():
        return None
    department_id = int(text)
    return department_id if any(d.get("brans_kodu_id") == department_id for d in depts) else None


def _codes_overlap(code: str, target: str) -> bool:
    """True when a department code holds the query or the query holds the code."""
    return bool(code) and (target in code or code in target)
