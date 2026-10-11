"""Synthetic OBS and Ninova payloads sized like a real account, for output-size checks.

Every name, code, CRN and number here is invented. The shapes follow the
fields the server reads, padded with the kind of duplicate and bookkeeping
fields the upstream systems send (Turkish and English copies of a label, ids,
envelope fields), so the size of a tool result can be measured without a
login or a network call.

The student has 8 terms, a degree plan of 45 rows and 6 registered courses.
The dashboard lists 7 courses.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

ENVELOPE = {"statusCode": 0, "resultCode": "SUCCESS", "resultMessage": "İşlem başarılı."}

_TERMS = [
    ("202310", "2022-2023 Güz Dönemi", "2022-2023 Fall Semester"),
    ("202320", "2022-2023 Bahar Dönemi", "2022-2023 Spring Semester"),
    ("202410", "2023-2024 Güz Dönemi", "2023-2024 Fall Semester"),
    ("202420", "2023-2024 Bahar Dönemi", "2023-2024 Spring Semester"),
    ("202510", "2024-2025 Güz Dönemi", "2024-2025 Fall Semester"),
    ("202520", "2024-2025 Bahar Dönemi", "2024-2025 Spring Semester"),
    ("202610", "2025-2026 Güz Dönemi", "2025-2026 Fall Semester"),
    ("202620", "2025-2026 Bahar Dönemi", "2025-2026 Spring Semester"),
]
CURRENT_TERM = "202620"
# A finished term: its six courses are graded in the degree plan only, which
# is how the registered-course endpoint behaves on a real account.
GRADED_TERM = "202520"


def semesters() -> dict[str, Any]:
    return {**ENVELOPE, "ogrenciDonemListesi": [
        {
            "akademikDonemId": 100 + index, "donemKodu": code,
            "akademikDonemAdi": name, "akademikDonemAdiEN": name_en,
            "akademikDonemKodu": code, "aktifDonem": code == CURRENT_TERM,
            "baslangicTarihi": f"{code[:4]}-02-09T00:00:00", "bitisTarihi": f"{code[:4]}-06-20T00:00:00",
        }
        for index, (code, name, name_en) in enumerate(_TERMS)
    ]}


def programs() -> dict[str, Any]:
    return {**ENVELOPE, "ogrenciProgramBilgiListesi": [{
        "ogrenciProgramId": 4242, "programAdi": "Sentetik Mühendisliği (%100 İngilizce)",
        "programAdiEN": "Synthetic Engineering (100% English)", "programKodu": "SNTE",
        "fakulteAdi": "Sentetik Fakültesi", "fakulteAdiEN": "Faculty of Synthetics",
        "programSeviyeTipiAnahtari": "LS", "durum": "Aktif",
    }]}


_REGISTERED = [
    ("SNT", "322E", "Sentetik Mimari", "Synthetic Architecture", 31001, "Pazartesi", "Monday", 510, 689, "A12", "EEB"),
    ("SNT", "336E", "Sentetik İşaret İşleme", "Synthetic Signal Processing", 31002, "Salı", "Tuesday", 630, 809, "5102", "EEB"),
    ("SNT", "354E", "Sentetik Ağlar", "Synthetic Networks", 31003, "Çarşamba", "Wednesday", 510, 689, "B204", "MED"),
    ("SNT", "372E", "Sentetik Veritabanları", "Synthetic Databases", 31004, "Perşembe", "Thursday", 810, 989, "D301", "EEB"),
    ("MAT", "281E", "Sentetik Lineer Cebir", "Synthetic Linear Algebra", 31005, "Cuma", "Friday", 510, 689, "FEB-112", "FEB"),
    ("ING", "201", "Sentetik İngilizce", "Synthetic English", 31006, "Salı", "Tuesday", 510, 629, "YD-07", "YDY"),
]


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _registered_row(index: int, branch: str, number: str, name: str, name_en: str, crn: int,
                    term: tuple[str, str, str], where: tuple[str, str], grade: str | None = None,
                    credit: Any = None) -> dict[str, Any]:
    return {
        "sinifId": 900100 + index, "crn": crn, "bransKodu": branch, "dersKodu": number,
        "dersAdiTR": name, "dersAdiEN": name_en,
        "kredi": credit, "akts": 5.0, "harfNotu": grade,
        "akademikDonemId": 100 + [item[0] for item in _TERMS].index(term[0]), "akademikDonemKodu": term[0],
        "akademikDonemAdi": term[1], "akademikDonemAdiEN": term[2],
        "ogretimUyesiAdSoyad": f"Dr. Öğr. Üyesi Sentetik Hoca {index + 1}",
        "yerZamanBilgiTR": where[0], "yerZamanBilgiEN": where[1],
        "dersTipiAdiTR": "Zorunlu", "dersTipiAdiEN": "Compulsory",
        "ogretimDiliTR": "İngilizce", "ogretimDiliEN": "English",
        "kayitTarihi": "2026-02-03T10:14:00", "birakildiMi": False,
    }


def registered_courses(term_code: str = CURRENT_TERM) -> dict[str, Any]:
    """Six courses of the current term, or the six graded ones of ``GRADED_TERM`` plus two edge cases."""
    rows = []
    if term_code == GRADED_TERM:
        term = _TERMS[5]
        where = ("Pazartesi 08:30-11:29 EEB A12", "Monday 08:30-11:29 EEB A12")
        for index in range(30, 36):
            plan = _plan_row(index)
            branch, number = plan["bransKodu"].split()
            rows.append(_registered_row(index, branch, number, plan["dersAdi"], plan["dersAdiEN"],
                                        plan["crn"], term, where))
        # A zero-credit pass/fail course and a failed course that is not in the plan.
        rows.append(_registered_row(40, "ING", "100", "Sentetik Hazırlık", "Synthetic Prep", 20900, term, where,
                                    grade="BL", credit="0"))
        rows.append(_registered_row(41, "SNT", "999E", "Sentetik Başarısız", "Synthetic Failed", 20901, term, where))
        return {**ENVELOPE, "kayitSinifResultList": rows}
    for index, (branch, number, name, name_en, crn, day, day_en, start, end, room, building) in enumerate(_REGISTERED):
        span = f"{_clock(start)}-{_clock(end)} {building} {room}"
        rows.append(_registered_row(index, branch, number, name, name_en, crn, _TERMS[-1],
                                    (f"{day} {span}", f"{day_en} {span}")))
    return {**ENVELOPE, "kayitSinifResultList": rows}


def schedule() -> dict[str, Any]:
    rows = []
    for index, (branch, number, name, name_en, crn, day, day_en, start, end, room, building) in enumerate(_REGISTERED):
        meetings = [{
            "gunAdiTR": day, "gunAdiEN": day_en, "gunNo": index % 5 + 1,
            "baslangicSaati": start, "bitisSaati": end,
            "mekanAdi": "Derslik", "mekanKapiTabelasi": room, "binaAdi": building,
            "binaAdiEN": building, "kampusAdi": "Ayazağa", "kampusAdiEN": "Ayazaga",
        }]
        if index == 0:
            meetings.append({**meetings[0], "gunAdiTR": "Perşembe", "gunAdiEN": "Thursday", "gunNo": 4,
                             "baslangicSaati": 510, "bitisSaati": 569})
        rows.append({
            "sinifId": 900100 + index, "crn": crn, "bransKodu": branch, "dersKodu": number,
            "dersAdiTR": name, "dersAdiEN": name_en,
            "akademikDonemAdi": _TERMS[-1][1], "akademikDonemAdiEN": _TERMS[-1][2],
            "ogretimUyesiAdSoyad": f"Dr. Öğr. Üyesi Sentetik Hoca {index + 1}",
            "sinifYerZaman": meetings,
        })
    # Deliberately not in day order, as the source sends them.
    return {**ENVELOPE, "kayitSinifResultList": rows[::-1]}


def final_calendar() -> dict[str, Any]:
    return {**ENVELOPE, "finalTakvimi": [
        {
            "sinifId": 900100 + index, "crn": crn, "bransKodu": branch, "dersKodu": number,
            "dersAdiTR": name, "dersAdiEN": name_en,
            "sinavTarihi": f"2026-06-{8 + index:02d}T00:00:00",
            "baslangicSaati": 570, "bitisSaati": 690,
            "sinavTipiAdiTR": "Final Sınavı", "sinavTipiAdiEN": "Final Exam",
            "mekanAdi": f"{building} {room}", "akademikDonemAdi": _TERMS[-1][1],
            "akademikDonemAdiEN": _TERMS[-1][2],
        }
        for index, (branch, number, name, name_en, crn, _d, _e, _s, _f, room, building) in enumerate(_REGISTERED)
    ]}


def _plan_row(index: int) -> dict[str, Any]:
    """One row of the 45-row degree plan: 36 completed, 4 taken now, 5 still open."""
    semester = index // 6 + 1
    elective = index % 9 == 8
    met = index < 36
    current = 36 <= index < 40
    code = f"SNT {100 + index * 7}E"
    name = f"Sentetik Ders {index + 1}"
    if current:
        code = f"{_REGISTERED[index - 36][0]} {_REGISTERED[index - 36][1]}"
        name = _REGISTERED[index - 36][3]
    group = f"{semester}th Sems. Elect. Course {'I' * (index % 3 + 1)} (MT)" if elective else None
    credit = 2.0 if index % 11 == 0 else 3.0 if index % 3 else 4.0
    open_slot = elective and not met
    term = _TERMS[min(index // 6, 7)]
    return {
        "dersPlaniDersId": 77000 + index, "donemNo": semester,
        "bransKodu": "" if open_slot else code,
        "dersAdi": group if open_slot else name,
        "dersAdiEN": group if open_slot else name,
        "kredisi": f"{credit:g}", "kredisiDec": credit, "akts": 5.0,
        "sayilanKredi": credit if met else None,
        "harfNotu": ("AA", "BA", "BB", "CB", "CC", "DC")[index % 6] if met else None,
        "donem": term[0] if met else None,
        "donemAdi": term[1] if met else None,
        "donemAdiEN": term[2] if met else None,
        "crn": 20000 + index if met else None,
        "grupName": group, "grupId": 500 + index if elective else None,
        "dersTipiAdiTR": "Seçmeli" if elective else "Zorunlu",
        "dersTipiAdiEN": "Elective" if elective else "Compulsory",
        "isMet": met, "isIngilizce": True, "esdegerDersMi": False, "aciklama": None,
    }


def graduation() -> dict[str, Any]:
    rows = [_plan_row(index) for index in range(45)]
    unused = [
        {
            "bransKodu": f"SNT {100 + index * 7}E", "dersAdi": f"Sentetik Ders {index + 1}",
            "dersAdiEN": f"Synthetic Course {index + 1}", "harfNotu": "FF" if index % 2 else "VF",
            "kredisi": "3", "kredisiDec": 3.0, "akts": 5.0, "donem": _TERMS[index % 4][0],
            "donemAdi": _TERMS[index % 4][1], "donemAdiEN": _TERMS[index % 4][2],
            "crn": 19000 + index, "isValidAndUnused": False, "aciklama": None,
        }
        for index in (3, 7, 12, 16)
    ]
    unused.append({
        "bransKodu": "SNT 999E", "dersAdi": "Sentetik Başarısız", "dersAdiEN": "Synthetic Failed",
        "harfNotu": "FF", "kredisi": "3", "kredisiDec": 3.0, "akts": 5.0, "donem": GRADED_TERM,
        "donemAdi": _TERMS[5][1], "donemAdiEN": _TERMS[5][2], "crn": 20901,
        "isValidAndUnused": False, "aciklama": None,
    })
    earned = sum(row["kredisiDec"] for row in rows if row["isMet"])
    return {**ENVELOPE, "mezuniyetimeNeKaldiBilgi": {
        "gpa": 2.87, "metKrediTotal": earned, "metIngKrediTotal": earned,
        "toplamDersSayisi": 45, "tamamlananDersSayisi": 36, "ogrenciTamamlananStaj": 20,
        "dersPlaniVM": {
            "akademikProgramAdiTR": "Sentetik Mühendisliği (%100 İngilizce)",
            "akademikProgramAdiEN": "Synthetic Engineering (100% English)",
            "dersPlaniBaslik": "2021-2022 Güz Dönemi Sonrası",
            "gerekliMezuniyetKredisi": 136.0, "gerekliMinGPA": 2.0, "gerekliStajGunu": 40,
            "gerekliIngilizceKredi": 136.0, "planTipiKodu": "LS",
        },
        "checkMetMezuniyetList": rows,
        "unusedSinifOgrenciList": unused,
    }}


def academic_status() -> dict[str, Any]:
    return {**ENVELOPE, "akademikDurum": {
        "genelNotOrtalamasi": 2.87, "tamamlananKredi": 108.0, "sinifSeviye": "4. Sınıf",
        "durumAdiTR": "Başarılı", "durumAdiEN": "Successful",
    }}


def debts() -> dict[str, Any]:
    return {**ENVELOPE, "borcBilgiList": []}


def registration_status() -> dict[str, Any]:
    terms = [
        {
            "akademikDonemKodu": code, "akademikDonemAdi": name, "akademikDonemAdiEN": name_en,
            "sinifSeviye": f"{min(index // 2 + 1, 4)}. Sınıf", "sinifSeviyeEN": f"{min(index // 2 + 1, 4)}. Class",
            "donemlikNotOrtalamasi": 0.0 if code == CURRENT_TERM else round(2.4 + index * 0.1, 2),
            "genelNotOrtalamasi": round(2.5 + index * 0.05, 2),
            "verilenKredi": 0.0 if code == CURRENT_TERM else 18.0, "alinanKredi": 18.0,
            "durum": "Aktif", "durumEN": "Active",
        }
        for index, (code, name, name_en) in enumerate(_TERMS)
    ]
    return {**ENVELOPE, "kayitDurumuList": [{
        "akademikProgramAdi": "Sentetik Mühendisliği (%100 İngilizce)",
        "akademikProgramAdiEN": "Synthetic Engineering (100% English)",
        "akademikBolumAdi": "Sentetik Mühendisliği", "akademikBolumAdiEN": "Synthetic Engineering",
        "fakulteAdi": "Sentetik Fakültesi", "fakulteAdiEN": "Faculty of Synthetics",
        "durum": "Aktif", "durumKodu": "AKT", "kayitDurumuDonemList": terms[::-1],
    }]}


def lesson_registration_status() -> dict[str, Any]:
    names = [("202710", "2026-2027 Güz Dönemi", "2026-2027 Fall Semester"),
             ("202630", "2025-2026 Yaz Öğretimi", "2025-2026 Summer School"), *_TERMS[::-1]]
    return {**ENVELOPE, "dersKayitDurumuList": [{
        "akademikProgramAdi": "Sentetik Mühendisliği (%100 İngilizce)",
        "akademikProgramAdiEN": "Synthetic Engineering (100% English)",
        "akademikBolumAdi": "Sentetik Mühendisliği", "akademikBolumAdiEN": "Synthetic Engineering",
        "fakulteAdi": "Sentetik Fakültesi", "fakulteAdiEN": "Faculty of Synthetics",
        "durum": "Aktif", "durumKodu": "AKT",
        "dersKayitDurumuDonemList": [
            {"akademikDonemKodu": code, "akademikDonemAdi": name, "akademikDonemAdiEN": name_en,
             "dersKayitDurumu": "Kayıt Olabilir", "dersKayitDurumuEN": "Can Register", "aciklama": None}
            for code, name, name_en in names
        ],
    }]}


def attendance() -> dict[str, Any]:
    weeks = [
        {"haftaNo": week, "yoklamaSaatListesi": [
            {"saatNo": hour, "katildiMi": (week + hour) % 5 != 0, "tarih": f"2026-03-{week:02d}T00:00:00"}
            for hour in (1, 2, 3)
        ]}
        for week in range(1, 11)
    ]
    return {**ENVELOPE, "sinifOgrenciYoklama": {
        "genelKatilim": "26/30", "yoklamaHaftaListe": weeks, "yoklamaSinifZamanListe": [],
    }}


class FakeObs:
    """The reads of ``ObsClient`` the measured tools use, answered from this module."""

    def list_semesters(self) -> dict[str, Any]:
        return semesters()

    def resolve_semester(self, semester: Any = None) -> dict[str, Any]:
        items = semesters()["ogrenciDonemListesi"]
        if semester is None:
            return items[-1]
        for item in items:
            if str(semester) in (str(item["akademikDonemId"]), item["donemKodu"]):
                return item
        raise ValueError(f"unknown synthetic term: {semester}")

    def list_programs(self) -> dict[str, Any]:
        return programs()

    def default_program_id(self) -> int:
        return 4242

    def list_registered_courses(self, semester_id: Any) -> dict[str, Any]:
        code = _TERMS[int(semester_id) - 100][0]
        return registered_courses(code if code == GRADED_TERM else CURRENT_TERM)

    def get_schedule(self, _semester_id: Any) -> dict[str, Any]:
        return schedule()

    def get_final_calendar(self, _semester_id: Any) -> dict[str, Any]:
        return final_calendar()

    def get_graduation_remaining(self, _program_id: Any) -> dict[str, Any]:
        return graduation()

    def get_academic_status(self, _program_id: Any) -> dict[str, Any]:
        return academic_status()

    def get_debts(self, _program_id: Any) -> dict[str, Any]:
        return debts()

    def get_registration_status(self) -> dict[str, Any]:
        return registration_status()

    def get_lesson_registration_status(self) -> dict[str, Any]:
        return lesson_registration_status()

    def get_attendance(self, _class_id: Any) -> dict[str, Any]:
        return attendance()

    def ensure_ready(self) -> dict[str, Any]:
        return {"obs_base_url": "https://obs.itu.edu.tr", "jwt_present": True, "login_method": "requests"}


PREREQUISITE_TREE = {"type": "and", "operands": [
    {"type": "course", "code": "SNT 107E", "min_grade": "DD"},
    {"type": "or", "operands": [
        {"type": "course", "code": "SNT 114E", "min_grade": "DD"},
        {"type": "course", "code": "MAT 281E", "min_grade": "DD"},
    ]},
    {"type": "course", "code": "SNT 121E", "min_grade": "BB"},
]}


class FakeObsPublic:
    """Public OBS reads: the prerequisite table of one branch and an empty calendar."""

    def get_branch_prerequisites(self, branch: str) -> dict[str, Any]:
        return {
            "url": f"https://obs.itu.edu.tr/public/DersOnsart/{branch}", "table_parsed": True,
            "rules": {"SNT 499E": {
                "requirement_tree": copy.deepcopy(PREREQUISITE_TREE),
                "minimum_grades": {"SNT 107E": "DD", "SNT 114E": "DD", "MAT 281E": "DD", "SNT 121E": "BB"},
                "credit_requirement": 100,
            }},
        }

    def get_academic_calendar(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"events": [], "coverage": {"status": "complete"}, "parse_complete": True}


class FakeCrossCheck:
    def get_course_prerequisite_tree(self, _code: str) -> None:
        return None


class FakeArchive:
    """An archive that knows no course, so nothing is looked up in it."""

    def get_course_history(self, _branch: str) -> dict[str, Any]:
        from ninova_mcp.archive_client import ItuArchiveError

        raise ItuArchiveError("Sentetik arşivde bu ders yok.")


_NAV = [
    ("Ana Sayfa", "/Kampus1"), ("Derslerim", "/Kampus1/Derslerim"), ("Duyurular", "/Kampus1/Duyurular"),
    ("Ödevler", "/Kampus1/Odevler"), ("Mesajlar", "/Kampus1/Mesajlar"), ("Takvim", "/Kampus1/Takvim"),
    ("Profil", "/Kampus1/Profil"), ("Ayarlar", "/Kampus1/Ayarlar"), ("Yardım", "/Yardim"),
    ("Sık Sorulan Sorular", "/Yardim/SSS"), ("İletişim", "/Iletisim"), ("English", "/Dil/EN"),
    ("Türkçe", "/Dil/TR"), ("Çıkış", "/Cikis"), ("İTÜ Ana Sayfa", "https://www.itu.edu.tr"),
    ("OBS", "https://obs.itu.edu.tr"), ("Kütüphane", "https://kutuphane.itu.edu.tr"),
    ("Webmail", "https://webmail.itu.edu.tr"), ("Yardım Masası", "https://yardim.itu.edu.tr"),
    ("Ders Programları", "https://obs.itu.edu.tr/public/DersProgram"),
]
_DASHBOARD_COURSES = [(f"SNT {300 + index * 11}E", f"Sentetik Konu {index + 1}", 31001 + index) for index in range(7)]


def dashboard_html() -> str:
    """A Ninova home page with 7 courses, the three recent tables and the site navigation."""
    parts = ["<html><head><title>Ninova - Sentetik Kampüs</title></head><body>", "<div id='menu'><ul>"]
    parts += [f"<li><a href='{href}'>{text}</a></li>" for text, href in _NAV]
    parts.append("</ul></div><h2>Derslerim</h2><ul class='menuErisimAgaci'>")
    for index, (code, title, crn) in enumerate(_DASHBOARD_COURSES):
        parts.append(
            f"<li><span>{code}</span><ul><li>"
            f"<a href='/Sinif/{41001 + index}.{500101 + index}'><span>CRN: {crn}</span></a>"
            "<script>var body = ''; body += '<span style=\"font-weight:bold;\">"
            f"{title}</span><br style=\"clear:both;\" />';</script></li></ul></li>"
        )
    parts.append("</ul>")
    for heading, first, second in (
        ("Son Duyurular", "Duyuru", "Tarih"), ("Son Ödevler", "Ödev", "Teslim Tarihi"), ("Son Mesajlar", "Mesaj", "Tarih"),
    ):
        parts.append(f"<h2>{heading}</h2><table><tr><th>{first}</th><th>{second}</th></tr>")
        for index, (code, title, crn) in enumerate(_DASHBOARD_COURSES[:5]):
            parts.append(
                f"<tr><td><a href='/Sinif/{41001 + index}.{500101 + index}/Kayit/{index}'>"
                f"{first} {index + 1}</a> {code} - {title} / CRN: {crn}</td>"
                f"<td>{10 + index} Mart 2026 23:59</td></tr>"
            )
        parts.append("</table>")
    parts.append("<div id='footer'>")
    parts += [f"<a href='/Alt/{index}'>Alt bağlantı {index}</a>" for index in range(12)]
    parts.append("</div></body></html>")
    return "".join(parts)


class FakeNinovaClient:
    """Serves the synthetic home page; anything else would be a real request, so it fails."""

    base_url = "https://ninova.itu.edu.tr"

    def get_html(self, path: str) -> tuple[str, Any]:
        if path != "/Kampus1":
            raise AssertionError(f"unexpected Ninova read in a size check: {path}")
        return dashboard_html(), SimpleNamespace(url=self.base_url + "/Kampus1")

    def ensure_logged_in(self, verify: bool = False) -> dict[str, Any]:
        del verify
        return {
            "base_url": self.base_url, "username": "sentetik", "last_login_at": "2026-03-01T09:00:00+03:00",
            "login_method": "requests",
            "cookie_names": sorted([".ASPXAUTH", "ASP.NET_SessionId", "BIGipServerninova", "ITUSSO",
                                    "__RequestVerificationToken"]),
            "request_delay_ms": 0, "session_persist": False, "session_path": None,
        }


def build_app(state_dir: str) -> Any:
    """An app whose clients are all fakes. Nothing in it can log in or reach a host."""
    import os
    from unittest.mock import patch

    from ninova_mcp.server import NinovaMcpApp

    with patch.dict(os.environ, {
        "NINOVA_USERNAME": "dummy", "NINOVA_PASSWORD": "dummy", "NINOVA_STATE_DIR": state_dir,
    }, clear=False):
        app = NinovaMcpApp()
    app._client = FakeNinovaClient()  # type: ignore[assignment]
    app._obs = FakeObs()  # type: ignore[assignment]
    app._obs_public = FakeObsPublic()  # type: ignore[assignment]
    app._prereq_crosscheck = FakeCrossCheck()  # type: ignore[assignment]
    app._archive = FakeArchive()  # type: ignore[assignment]
    return app


def measured_calls(app: Any) -> dict[str, Any]:
    """The default call of every tool whose result size is tracked."""
    return {
        "obs_get_graduation_remaining": lambda: app.obs_get_graduation_remaining(),
        "obs_calculate_gpa": lambda: app.obs_calculate_gpa(semester=GRADED_TERM),
        "explain_course_eligibility": lambda: app.explain_course_eligibility("SNT 499E", use_obs_history=True),
        "get_dashboard": lambda: app.get_dashboard(),
        "auth_status": lambda: app.auth_status(),
        "get_personal_exam_calendar": lambda: app.get_personal_exam_calendar(),
        "obs_list_registered_courses": lambda: app.obs_list_registered_courses(),
        "obs_get_schedule": lambda: app.obs_get_schedule(),
        "obs_get_registration_status": lambda: app.obs_get_registration_status(),
        "obs_get_attendance": lambda: app.obs_get_attendance(course="SNT 322E"),
        "obs_list_semesters": lambda: app.obs_list_semesters(),
        "obs_list_programs": lambda: app.obs_list_programs(),
        "plan_remaining_courses": lambda: app.plan_remaining_courses(),
    }
