"""Public OBS degree plans: faculties, programs, plan versions, plan detail."""

from __future__ import annotations

from typing import Any

from .obs_errors import ObsError
from .obs_public_transport import ObsPublicTransport
from .parsing import clean_text, make_soup


class ObsPublicDegreePlans(ObsPublicTransport):
    """Official degree plans as published on the public plan pages."""

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
