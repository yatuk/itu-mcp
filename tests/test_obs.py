from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ninova_mcp.obs_client import ObsClient, ObsError, redact_obs_profile
from ninova_mcp.server import NinovaMcpApp, LOCAL_TOOL_NAMES


class ObsRedactTests(unittest.TestCase):
    def test_redact_profile(self) -> None:
        payload = {
            "kisiselBilgiler": {
                "adSoyad": "Test User",
                "kimlikNo": "12345678901",
                "bolumAdiTR": "Bilgisayar",
            }
        }
        redacted = redact_obs_profile(payload)
        self.assertEqual(redacted["kisiselBilgiler"]["kimlikNo"], "***REDACTED***")
        self.assertEqual(redacted["kisiselBilgiler"]["adSoyad"], "Test User")


class ObsClientUnitTests(unittest.TestCase):
    def test_resolve_semester_latest(self) -> None:
        client = ObsClient(ninova_client=MagicMock())
        with patch.object(
            client,
            "list_semesters",
            return_value={
                "ogrenciDonemListesi": [
                    {"akademikDonemId": 1, "donemKodu": "202410", "akademikDonemAdi": "Güz"},
                    {"akademikDonemId": 2, "donemKodu": "202420", "akademikDonemAdi": "Bahar"},
                ]
            },
        ):
            latest = client.resolve_semester(None)
            by_code = client.resolve_semester("202410")
            by_name = client.resolve_semester("bahar")
        self.assertEqual(latest["akademikDonemId"], 2)
        self.assertEqual(by_code["akademikDonemId"], 1)
        self.assertEqual(by_name["akademikDonemId"], 2)


class ObsToolsRegisteredTests(unittest.TestCase):
    def test_obs_tools_present(self) -> None:
        for name in (
            "obs_auth_status",
            "obs_get_profile",
            "obs_list_registered_courses",
            "obs_get_course_grades",
            "obs_download_transcript",
        ):
            self.assertIn(name, LOCAL_TOOL_NAMES)
            self.assertTrue(hasattr(NinovaMcpApp, name))


class ObsAttendanceSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = NinovaMcpApp()

    def _mock_attendance_payload(self) -> dict:
        return {
            "sinifOgrenciYoklama": {
                "katilim": "30 / 42 (%71.4)",
                "genelKatilim": "%71.4",
                "yoklamaHaftaListe": [
                    {
                        "yoklamaSinifZamanListe": [
                            {
                                "yoklamaSaatListesi": [
                                    {"katildiMi": True},
                                    {"katildiMi": True},
                                ]
                            },
                            {
                                "yoklamaSaatListesi": [
                                    {"katildiMi": False},
                                    {"katildiMi": True},
                                ]
                            },
                        ]
                    }
                ],
            }
        }

    def test_attendance_with_summary(self) -> None:
        with patch.object(self.app.obs, "get_attendance", return_value=self._mock_attendance_payload()):
            with patch.object(self.app.obs, "resolve_semester", return_value={"akademikDonemId": 1, "akademikDonemAdi": "Test"}):
                with patch.object(self.app.obs, "list_registered_courses", return_value={
                    "kayitSinifResultList": [
                        {"sinifId": 12345, "bransKodu": "BBF", "dersKodu": "201E", "dersAdiTR": "Olasılık", "crn": "23980"},
                    ]
                }):
                    result = self.app.obs_get_attendance(class_id=12345, include_summary=True)
        self.assertIn("summary", result)
        self.assertTrue(result["summary"]["available"])
        self.assertIn("risk", result["summary"])
        self.assertIn("summary_tr", result["summary"])
        self.assertEqual(result["summary"]["present"], 30)
        self.assertEqual(result["summary"]["total_sessions"], 42)

    def test_attendance_without_summary(self) -> None:
        with patch.object(self.app.obs, "get_attendance", return_value=self._mock_attendance_payload()):
            with patch.object(self.app.obs, "resolve_semester", return_value={"akademikDonemId": 1, "akademikDonemAdi": "Test"}):
                with patch.object(self.app.obs, "list_registered_courses", return_value={
                    "kayitSinifResultList": [
                        {"sinifId": 12345, "bransKodu": "BBF", "dersKodu": "201E", "dersAdiTR": "Olasılık", "crn": "23980"},
                    ]
                }):
                    result = self.app.obs_get_attendance(class_id=12345, include_summary=False)
        self.assertNotIn("summary", result)
        self.assertIn("attendance", result)

    def test_attendance_error_handling(self) -> None:
        with patch.object(self.app.obs, "get_attendance", side_effect=ObsError("No attendance data")):
            with patch.object(self.app.obs, "resolve_semester", return_value={"akademikDonemId": 1, "akademikDonemAdi": "Test"}):
                with patch.object(self.app.obs, "list_registered_courses", return_value={
                    "kayitSinifResultList": [
                        {"sinifId": 12345, "bransKodu": "BBF", "dersKodu": "201E", "dersAdiTR": "Olasılık", "crn": "23980"},
                    ]
                }):
                    result = self.app.obs_get_attendance(class_id=12345, include_summary=True)
        self.assertEqual(result["attendance"], None)
        self.assertIsNotNone(result["error"])
        self.assertFalse(result["summary"]["available"])

    def test_attendance_with_custom_absence_ratio(self) -> None:
        with patch.object(self.app.obs, "get_attendance", return_value=self._mock_attendance_payload()):
            with patch.object(self.app.obs, "resolve_semester", return_value={"akademikDonemId": 1, "akademikDonemAdi": "Test"}):
                with patch.object(self.app.obs, "list_registered_courses", return_value={
                    "kayitSinifResultList": [
                        {"sinifId": 12345, "bransKodu": "BBF", "dersKodu": "201E", "dersAdiTR": "Olasılık", "crn": "23980"},
                    ]
                }):
                    result = self.app.obs_get_attendance(class_id=12345, max_absence_ratio=0.20)
        self.assertEqual(result["summary"]["max_absence_ratio_assumed"], 0.20)


SAMPLE_OBS_SELECT_HTML = """
<html>
  <body>
    <select id="DersBransKoduId">
      <option value="">Seçiniz</option>
      <option value="304">BBF - Bilgisayar Bilimleri ve Mühendisliği</option>
      <option value="301">MAT - Matematik Mühendisliği</option>
    </select>
  </body>
</html>
"""

SAMPLE_OBS_SEARCH_HTML = """
<html>
  <body>
    <table>
      <tr><th>Ders Kodu</th><th>Ders Adı</th><th>Kredi</th></tr>
      <tr>
        <td><a href="/public/DersBilgi/304/BBF201E">BBF 201E</a></td>
        <td>Olasılık ve İstatistik</td>
        <td>3</td>
      </tr>
      <tr>
        <td><a href="/public/DersBilgi/304/BBF101E">BBF 101E</a></td>
        <td>Programlamaya Giriş</td>
        <td>3</td>
      </tr>
    </table>
  </body>
</html>
"""

SAMPLE_PREREQ_HTML = """
<html>
  <body>
    <h1>Önşart Listesi</h1>
    <table>
      <tr><th>Ders Kodu</th><th>Ders Adı</th><th>Grup No</th><th>Tip</th></tr>
      <tr><td>BBF 101E</td><td>Programlamaya Giriş</td><td>1</td><td>Zorunlu</td></tr>
    </table>
  </body>
</html>
"""


class ObsPublicClientTests(unittest.TestCase):
    def test_extract_course_select_options(self) -> None:
        from ninova_mcp.parsing import extract_course_select_options

        options = extract_course_select_options(SAMPLE_OBS_SELECT_HTML, "https://obs.itu.edu.tr/test")
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]["value"], "304")
        self.assertEqual(options[0]["text"], "BBF - Bilgisayar Bilimleri ve Mühendisliği")
        self.assertEqual(options[1]["value"], "301")

    def test_extract_course_search_results(self) -> None:
        from ninova_mcp.parsing import extract_course_search_results

        results = extract_course_search_results(SAMPLE_OBS_SEARCH_HTML, "https://obs.itu.edu.tr/test")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["code"], "BBF 201E")
        self.assertEqual(results[1]["code"], "BBF 101E")

    def test_extract_prerequisite_list(self) -> None:
        from ninova_mcp.parsing import extract_prerequisite_list

        data = extract_prerequisite_list(
            SAMPLE_PREREQ_HTML,
            "https://obs.itu.edu.tr/test",
            "https://obs.itu.edu.tr",
        )
        self.assertEqual(len(data["prerequisites"]), 1)
        self.assertEqual(data["prerequisites"][0]["code"], "BBF 101E")
        self.assertEqual(data["prerequisites"][0]["type"], "Zorunlu")
        self.assertIn("raw_tables", data)

    def test_build_course_index(self) -> None:
        from ninova_mcp.obs_client import ObsPublicClient

        client = ObsPublicClient()
        with patch.object(client, "_get_html", return_value=(SAMPLE_OBS_SELECT_HTML, "https://obs.itu.edu.tr/test")):
            index = client._build_course_index()
        self.assertIn("bbf", index)
        self.assertEqual(index["bbf"], 304)

    def test_resolve_course_code(self) -> None:
        from ninova_mcp.obs_client import ObsPublicClient

        client = ObsPublicClient()
        with patch.object(client, "_get_html", return_value=(SAMPLE_OBS_SELECT_HTML, "https://obs.itu.edu.tr/test")):
            result = client.resolve_course_code("BBF")
        self.assertEqual(result["brans_kodu_id"], 304)
        self.assertEqual(result["source"], "dept_index")

    def test_get_prerequisites_with_mock(self) -> None:
        from ninova_mcp.obs_client import ObsPublicClient

        client = ObsPublicClient()
        with patch.object(client, "_get_html", return_value=(SAMPLE_PREREQ_HTML, "https://obs.itu.edu.tr/test")):
            result = client.get_prerequisites(304)
        self.assertTrue(result["available"])
        self.assertEqual(len(result["prerequisites"]), 1)
        self.assertEqual(result["prerequisites"][0]["code"], "BBF 101E")


class ObsPublicToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = NinovaMcpApp()

    def test_tools_registered(self) -> None:
        self.assertIn("obs_search_courses", LOCAL_TOOL_NAMES)
        self.assertIn("obs_get_course_prerequisites", LOCAL_TOOL_NAMES)
        self.assertTrue(hasattr(NinovaMcpApp, "obs_search_courses"))
        self.assertTrue(hasattr(NinovaMcpApp, "obs_get_course_prerequisites"))

    def test_search_courses(self) -> None:
        from ninova_mcp.obs_client import ObsPublicClient

        client = ObsPublicClient()
        with patch.object(client, "_get_html", return_value=(SAMPLE_OBS_SEARCH_HTML, "https://obs.itu.edu.tr/test")):
            self.app._obs_public = client
            result = self.app.obs_search_courses("BBF 201E")
        self.assertEqual(result["query"], "BBF 201E")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["courses"][0]["code"], "BBF 201E")

    def test_get_course_prerequisites(self) -> None:
        from ninova_mcp.obs_client import ObsPublicClient

        client = ObsPublicClient()
        with patch.object(client, "_get_html", return_value=(SAMPLE_OBS_SELECT_HTML, "https://obs.itu.edu.tr/test")):
            with patch.object(client, "get_prerequisites", return_value={
                "course_id": "304",
                "available": True,
                "prerequisites": [{"code": "BBF 101E", "name": "Programlamaya Giriş", "type": "Zorunlu"}],
            }):
                with patch.object(client, "get_postrequisites", return_value={
                    "course_id": "304",
                    "available": True,
                    "postrequisites": [],
                }):
                    self.app._obs_public = client
                    result = self.app.obs_get_course_prerequisites("BBF 201E", direction="both")
        self.assertEqual(result["course"]["brans_kodu_id"], 304)
        self.assertEqual(len(result["prerequisites"]), 1)
        self.assertEqual(result["prerequisites"][0]["code"], "BBF 101E")
        self.assertEqual(len(result["postrequisites"]), 0)

    def test_get_course_prerequisites_bad_direction(self) -> None:
        from ninova_mcp.obs_client import ObsError

        with self.assertRaises(ObsError):
            self.app.obs_get_course_prerequisites("BBF 201E", direction="invalid")


SAMPLE_SCHEDULE_DEPT_JSON = (
    '[{"bransKoduId": 3, "dersBransKodu": "BLG"},'
    '{"bransKoduId": 310, "dersBransKodu": "BBF"}]'
)


class ObsPublicScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = NinovaMcpApp()

    def _load_fixture(self, name: str) -> str:
        from pathlib import Path

        fixture_path = Path(__file__).parent / "fixtures" / name
        return fixture_path.read_text(encoding="utf-8")

    def test_extract_schedule_table_from_fixture(self) -> None:
        from ninova_mcp.parsing import extract_course_schedule_table

        html = self._load_fixture("ders_program_result.html")
        result = extract_course_schedule_table(html, "https://obs.itu.edu.tr/test", "https://obs.itu.edu.tr")
        self.assertGreaterEqual(result["count"], 1)
        self.assertIn("courses", result)
        course = result["courses"][0]
        self.assertIn("crn", course)
        self.assertIn("code", course)
        self.assertIn("sessions", course)
        self.assertIsInstance(course["sessions"], list)
        self.assertIn("capacity", course)
        self.assertIn("enrolled", course)

    def test_extract_schedule_parses_multi_session(self) -> None:
        from ninova_mcp.parsing import extract_course_schedule_table

        html = self._load_fixture("ders_program_result.html")
        result = extract_course_schedule_table(html, "https://obs.itu.edu.tr/test", "https://obs.itu.edu.tr")
        blg223 = next((c for c in result["courses"] if c.get("code") == "BLG 223E"), None)
        self.assertIsNotNone(blg223)
        # BLG 223E has 4 sessions (4x <br> in each of Bina/Gün/Saat/Derslik)
        self.assertEqual(len(blg223["sessions"]), 4)
        self.assertEqual(blg223["sessions"][0]["day"], "Çarşamba")
        self.assertEqual(blg223["sessions"][0]["time"], "09:30/12:29")
        self.assertEqual(blg223["capacity"], 60)
        self.assertEqual(blg223["enrolled"], 60)

    def test_extract_schedule_detects_prerequisites(self) -> None:
        from ninova_mcp.parsing import extract_course_schedule_table

        html = self._load_fixture("ders_program_result.html")
        result = extract_course_schedule_table(html, "https://obs.itu.edu.tr/test", "https://obs.itu.edu.tr")
        blg223 = next((c for c in result["courses"] if c.get("code") == "BLG 223E"), None)
        self.assertTrue(blg223["has_prerequisites"])
        self.assertIsNotNone(blg223["prerequisite_detail_url"])

        blg470 = next((c for c in result["courses"] if c.get("code") == "BLG 470E"), None)
        self.assertFalse(blg470["has_prerequisites"])

    def test_list_departments(self) -> None:
        from ninova_mcp.obs_client import ObsPublicClient

        client = ObsPublicClient()
        with patch.object(client, "_get_html", return_value=(SAMPLE_SCHEDULE_DEPT_JSON, "https://obs.itu.edu.tr/test")):
            depts = client.list_departments("LS")
        self.assertEqual(len(depts), 2)
        self.assertEqual(depts[0]["code"], "BLG")
        self.assertEqual(depts[1]["code"], "BBF")

    def test_normalize_program_type(self) -> None:
        from ninova_mcp.obs_client import ObsPublicClient

        self.assertEqual(ObsPublicClient._normalize_program_type("Lisans"), "LS")
        self.assertEqual(ObsPublicClient._normalize_program_type("LS"), "LS")
        self.assertEqual(ObsPublicClient._normalize_program_type("lisansüstü"), "LU")
        self.assertEqual(ObsPublicClient._normalize_program_type("Önlisans"), "ÖL")

        with self.assertRaises(ObsError):
            ObsPublicClient._normalize_program_type("Doktora")

    def test_get_course_schedule_mocked(self) -> None:
        from ninova_mcp.obs_client import ObsPublicClient

        client = ObsPublicClient()
        schedule_html = self._load_fixture("ders_program_result.html")

        def fake_get_html(path, params=None):
            if "SearchBransKodu" in path:
                return (SAMPLE_SCHEDULE_DEPT_JSON, "https://obs.itu.edu.tr/test")
            if "GetAktifDonem" in path:
                return ('{"aktifDonem": "2025-2026 Yaz"}', "https://obs.itu.edu.tr/test")
            if "DersProgramSearch" in path:
                return (schedule_html, "https://obs.itu.edu.tr/test")
            return ("{}", "https://obs.itu.edu.tr/test")

        with patch.object(client, "_get_html", side_effect=fake_get_html):
            result = client.get_course_schedule("LS", "BLG")
        self.assertEqual(result["program_type"], "LS")
        self.assertEqual(result["department_code"], "BLG")
        self.assertEqual(result["semester"], "2025-2026 Yaz")
        self.assertGreaterEqual(result["count"], 1)

    def test_get_schedule_by_crn(self) -> None:
        from ninova_mcp.obs_client import ObsPublicClient

        client = ObsPublicClient()
        schedule_html = self._load_fixture("ders_program_result.html")

        def fake_get_html(path, params=None):
            if "SearchBransKodu" in path:
                return (SAMPLE_SCHEDULE_DEPT_JSON, "https://obs.itu.edu.tr/test")
            if "GetAktifDonem" in path:
                return ('{"aktifDonem": "2025-2026 Yaz"}', "https://obs.itu.edu.tr/test")
            if "DersProgramSearch" in path:
                return (schedule_html, "https://obs.itu.edu.tr/test")
            return ("{}", "https://obs.itu.edu.tr/test")

        with patch.object(client, "_get_html", side_effect=fake_get_html):
            result = client.get_course_schedule_by_crn("LS", "BLG", "30334")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["courses"][0]["crn"], "30334")
        self.assertEqual(result["courses"][0]["code"], "BLG 223E")

    def test_extract_schedule_building_no_html(self) -> None:
        from ninova_mcp.parsing import extract_course_schedule_table

        html = self._load_fixture("ders_program_result.html")
        result = extract_course_schedule_table(html, "https://obs.itu.edu.tr/test", "https://obs.itu.edu.tr")
        for course in result["courses"]:
            for session in course.get("sessions") or []:
                bldg = session.get("building") or ""
                # Must not contain HTML tags
                self.assertNotIn("<", bldg)
                self.assertNotIn("href", bldg)

    def test_schedule_crn_not_found(self) -> None:
        from ninova_mcp.obs_client import ObsPublicClient

        client = ObsPublicClient()
        schedule_html = self._load_fixture("ders_program_result.html")

        def fake_get_html(path, params=None):
            if "SearchBransKodu" in path:
                return (SAMPLE_SCHEDULE_DEPT_JSON, "https://obs.itu.edu.tr/test")
            if "GetAktifDonem" in path:
                return ('{"aktifDonem": "2025-2026 Yaz"}', "https://obs.itu.edu.tr/test")
            if "DersProgramSearch" in path:
                return (schedule_html, "https://obs.itu.edu.tr/test")
            return ("{}", "https://obs.itu.edu.tr/test")

        with patch.object(client, "_get_html", side_effect=fake_get_html):
            with self.assertRaises(ObsError):
                client.get_course_schedule_by_crn("LS", "BLG", "99999")

    def test_schedule_tools_registered(self) -> None:
        self.assertIn("get_public_course_schedule", LOCAL_TOOL_NAMES)
        self.assertIn("get_public_course_prerequisites", LOCAL_TOOL_NAMES)
        self.assertTrue(hasattr(NinovaMcpApp, "get_public_course_schedule"))
        self.assertTrue(hasattr(NinovaMcpApp, "get_public_course_prerequisites"))


SAMPLE_PORTAL_HTML = """<html><body>
<div id="bildirimBilgisi">
<div class="panel" data-panel="notification">
  <ul data-placement="notification-list">
    <li class="notification__list-item notification__list-item--unread"><a href="javascript:void(0);" data-notification-id="91871033"><span class="pull-left">Ağ Altyapı Çalışması</span><span class="pull-right">2 s </span></a></li>
    <li class="notification__list-item"><a href="javascript:void(0);" data-notification-id="91728156"><span class="pull-left">Yardım Biletiniz Cevaplandı</span><span class="pull-right">4 g </span></a></li>
  </ul>
</div>
</div>
<div id="yardimBilgisi">
<div class="panel" data-panel="yardim">
  <ul data-placement="yardim-list">
    <li class="help__list-item"><a href="http://yardim.itu.edu.tr/itubilet.aspx?id=1005121" target="_blank"><span class="pull-left">Fizik II Eşdeğerlik Onayı Talebi<span class="panel-red">Arşiv</span></span><span class="pull-right">4 g </span></a></li>
  </ul>
</div>
</div>
<div id="cloudBilgisi">
<div class="panel" data-panel="quota1">
  <div data-panel="quota"><p data-placement="quota">%1.1</p><p data-placement="description">Kota Kullanımınız<br>0,17/16</p></div>
  <div data-panel="quotaEski"><p data-placement="quotaEski">%0.2</p><p data-placement="descriptionEski">Kota Kullanımınız<br>12 MB/8 GB</p></div>
</div>
</div>
<div id="kartBakiyeBilgisi">
<div class="panel" data-panel="card-balance">
  <div class="panel-heading">Kart Bakiyeniz</div>
  <div class="panel-body">
    <div class="card-deposit__number" data-placement="balance">₺ 42</div>
    <ul data-placement="transitions">
      <li class="card-deposit__list-item"><span class="amount pull-right">Bakiye</span><span class="amount pull-right">Tutar</span></li>
      <li class="card-deposit__list-item"><span class="icon-itu-card-spending text-danger"></span><span>Harcama</span><span class="amount pull-right">₺ 42</span><span class="amount pull-right text-danger"> ₺ -47,5</span></li>
      <li class="card-deposit__list-item"><span class="icon-itu-card-charge text-success"></span><span>Yükleme</span><span class="amount pull-right">₺ 89,5</span><span class="amount pull-right text-success"> ₺ 50</span></li>
    </ul>
  </div>
</div>
</div>
<div id="yemekMenuBilgisi">
<div class="panel" data-panel="food">
  <div class="panel-heading"><span data-placement="food-title">Akşam Yemeği Menüsü</span></div>
  <div class="panel-body">
    <div data-placement="food-form">
      <input type="text" id="food-date" value="10.07.2026">
      <input type="radio" id="radio-ogle" value="itu-ogle-yemegi-genel">
      <input type="radio" id="radio-aksam" value="itu-aksam-yemegi-genel" checked>
      <input type="checkbox" id="checkbox-vejeteryan-vegan">
    </div>
    <ul data-placement="food-list">
      <li class="lunch-menu__list-item">Alaca Çorbası&nbsp;<i class="icon-warning" data-food-id="24"></i></li>
      <li class="lunch-menu__list-item">Etli Türlü&nbsp;<i class="icon-warning" data-food-id="414"></i></li>
      <li class="lunch-menu__list-item">Bulgur Pilavı</li>
      <li><b>Seçmeli 4. Çeşit</b><ul class="secmeli-yemek" style="list-style:none"><li class="lunch-menu__list-item">Sütlü İrmik Tatlısı&nbsp;<i class="icon-warning" data-food-id="2304"></i></li><li class="lunch-menu__list-item">Kuru Börülce Salatası</li></ul></li>
    </ul>
  </div>
</div>
</div></body></html>"""


class PortalParsingTests(unittest.TestCase):
    def test_extract_campus_card_from_portal(self) -> None:
        from ninova_mcp.parsing import extract_campus_card_info

        result = extract_campus_card_info(SAMPLE_PORTAL_HTML, "https://portal.itu.edu.tr/apps/default/", "https://portal.itu.edu.tr")
        self.assertEqual(result["balance"], "₺ 42")
        self.assertEqual(result["transaction_count"], 2)
        self.assertEqual(result["transactions"][0]["type"], "Harcama")
        self.assertEqual(result["transactions"][1]["type"], "Yükleme")

    def test_extract_cafeteria_menu_from_portal(self) -> None:
        from ninova_mcp.parsing import extract_cafeteria_menu

        result = extract_cafeteria_menu(SAMPLE_PORTAL_HTML, "https://portal.itu.edu.tr/apps/default/")
        self.assertEqual(result["title"], "Akşam Yemeği Menüsü")
        self.assertGreaterEqual(result["item_count"], 4)
        self.assertEqual(result["items"][0]["name"], "Alaca Çorbası")
        self.assertTrue(result["items"][0]["has_allergen_info"])
        self.assertEqual(result["items"][0]["food_id"], "24")
        # Check optional 4th dish
        selectable = [i for i in result["items"] if i.get("is_selectable_group")]
        self.assertEqual(len(selectable), 1)
        self.assertEqual(selectable[0]["name"], "Seçmeli 4. Çeşit")
        self.assertEqual(len(selectable[0]["options"]), 2)
        # Vegetarian
        self.assertTrue(result["vegetarian_available"])

    def test_menu_item_without_allergen(self) -> None:
        from ninova_mcp.parsing import extract_cafeteria_menu

        result = extract_cafeteria_menu(SAMPLE_PORTAL_HTML, "https://portal.itu.edu.tr/apps/default/")
        bulgur = next((i for i in result["items"] if i["name"] == "Bulgur Pilavı"), None)
        self.assertIsNotNone(bulgur)
        self.assertFalse(bulgur["has_allergen_info"])


class PortalWidgetTests(unittest.TestCase):
    def test_extract_notifications(self) -> None:
        from ninova_mcp.parsing import extract_notifications

        result = extract_notifications(SAMPLE_PORTAL_HTML, "https://portal.itu.edu.tr/apps/default/")
        self.assertGreaterEqual(result["count"], 1)
        notif = result["notifications"][0]
        self.assertIn("title", notif)
        self.assertIn("unread", notif)

    def test_extract_help_tickets(self) -> None:
        from ninova_mcp.parsing import extract_help_tickets

        result = extract_help_tickets(SAMPLE_PORTAL_HTML, "https://portal.itu.edu.tr/apps/default/")
        self.assertGreaterEqual(result["count"], 1)
        ticket = result["tickets"][0]
        self.assertIn("title", ticket)
        self.assertIn("url", ticket)

    def test_extract_cloud_quota(self) -> None:
        from ninova_mcp.parsing import extract_cloud_quota

        result = extract_cloud_quota(SAMPLE_PORTAL_HTML, "https://portal.itu.edu.tr/apps/default/")
        self.assertIsNotNone(result["mail"]["usage_percent"])
        self.assertIsNotNone(result["cloud"]["usage_percent"])


if __name__ == "__main__":
    unittest.main()
