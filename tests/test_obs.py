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


if __name__ == "__main__":
    unittest.main()
