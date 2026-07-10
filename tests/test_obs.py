from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ninova_mcp.obs_client import ObsClient, redact_obs_profile
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


if __name__ == "__main__":
    unittest.main()
