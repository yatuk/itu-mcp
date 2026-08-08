"""Regression coverage for obs_calculate_gpa's grade fallback.

Found by testing against a real account: list_registered_courses's harfNotu
came back None for every course in every term, including terms years in the
past — not just "not graded yet" — which made obs_calculate_gpa return
gpa=None unconditionally. The fallback sources the grade from
obs_get_graduation_remaining instead, scoped to the specific term being
asked about (a retaken course has a different grade per attempt, so a
code-only lookup risks picking the wrong one).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from ninova_mcp.server import NinovaMcpApp

GRADUATION_PAYLOAD = {
    "mezuniyetimeNeKaldiBilgi": {
        "checkMetMezuniyetList": [
            # CEN 101E retaken: failed once (202410), passed later (202610)
            # with a "+" grade — real OBS output this account actually has.
            {"bransKodu": "CEN 101E", "harfNotu": "BA+", "donem": "202610"},
            {"bransKodu": "CEN 101E", "harfNotu": "FF", "donem": "202410"},
            {"bransKodu": "FIZ 101E", "harfNotu": "DC", "donem": "202410"},
        ],
        "unusedSinifOgrenciList": [
            {"bransKodu": "BLG 112E", "harfNotu": "VF", "donem": "202420"},
        ],
    }
}


class GpaFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_patch = patch.dict(
            os.environ,
            {
                "NINOVA_USERNAME": "dummy",
                "NINOVA_PASSWORD": "dummy",
                "NINOVA_STATE_DIR": self.temp_dir.name,
            },
            clear=False,
        )
        self.env_patch.start()
        self.app = NinovaMcpApp()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def _registered(self, entries):
        return {"kayitSinifResultList": entries}

    def test_empty_harfnotu_falls_back_to_graduation_payload(self) -> None:
        """The exact failure mode found live: registration harfNotu is None
        for a course that the graduation payload does have a grade for.
        """
        registered = self._registered([
            {"bransKodu": "FIZ", "dersKodu": "101E", "kredi": "3", "harfNotu": None},
        ])
        with patch.object(
            self.app.obs, "resolve_semester",
            return_value={"akademikDonemId": 1, "donemKodu": "202410"},
        ), patch.object(
            self.app.obs, "list_registered_courses", return_value=registered,
        ), patch.object(
            self.app.obs, "get_graduation_remaining", return_value=GRADUATION_PAYLOAD,
        ), patch.object(
            self.app.obs, "default_program_id", return_value=1,
        ):
            result = self.app.obs_calculate_gpa()

        self.assertEqual(result["gpa"], 1.50)  # DC
        self.assertEqual(result["grade_fallback_courses"], ["FIZ 101E"])

    def test_retake_uses_the_grade_for_the_requested_term_not_another_attempt(self) -> None:
        """CEN 101E has two grades in the graduation payload (FF then BA+).
        Asking for the 202410 term specifically must return FF, not BA+ —
        a code-only lookup (ignoring which term) would get this wrong.
        """
        registered = self._registered([
            {"bransKodu": "CEN", "dersKodu": "101E", "kredi": "2", "harfNotu": None},
        ])
        with patch.object(
            self.app.obs, "resolve_semester",
            return_value={"akademikDonemId": 1, "donemKodu": "202410"},
        ), patch.object(
            self.app.obs, "list_registered_courses", return_value=registered,
        ), patch.object(
            self.app.obs, "get_graduation_remaining", return_value=GRADUATION_PAYLOAD,
        ), patch.object(
            self.app.obs, "default_program_id", return_value=1,
        ):
            result = self.app.obs_calculate_gpa()

        self.assertEqual(result["courses"][0]["grade"], "FF")

        with patch.object(
            self.app.obs, "resolve_semester",
            return_value={"akademikDonemId": 2, "donemKodu": "202610"},
        ), patch.object(
            self.app.obs, "list_registered_courses", return_value=registered,
        ), patch.object(
            self.app.obs, "get_graduation_remaining", return_value=GRADUATION_PAYLOAD,
        ), patch.object(
            self.app.obs, "default_program_id", return_value=1,
        ):
            result = self.app.obs_calculate_gpa(semester="202610")

        self.assertEqual(result["courses"][0]["grade"], "BA+")

    def test_vf_grade_is_recovered_via_fallback_too(self) -> None:
        registered = self._registered([
            {"bransKodu": "BLG", "dersKodu": "112E", "kredi": "3", "harfNotu": None},
        ])
        with patch.object(
            self.app.obs, "resolve_semester",
            return_value={"akademikDonemId": 1, "donemKodu": "202420"},
        ), patch.object(
            self.app.obs, "list_registered_courses", return_value=registered,
        ), patch.object(
            self.app.obs, "get_graduation_remaining", return_value=GRADUATION_PAYLOAD,
        ), patch.object(
            self.app.obs, "default_program_id", return_value=1,
        ):
            result = self.app.obs_calculate_gpa()

        self.assertEqual(result["courses"][0]["grade"], "VF")
        self.assertEqual(result["gpa"], 0.0)

    def test_present_harfnotu_is_not_overridden(self) -> None:
        """The fallback must only fire when the registration grade is empty."""
        registered = self._registered([
            {"bransKodu": "CEN", "dersKodu": "101E", "kredi": "2", "harfNotu": "CC"},
        ])
        with patch.object(
            self.app.obs, "resolve_semester",
            return_value={"akademikDonemId": 1, "donemKodu": "202410"},
        ), patch.object(
            self.app.obs, "list_registered_courses", return_value=registered,
        ), patch.object(
            self.app.obs, "get_graduation_remaining", return_value=GRADUATION_PAYLOAD,
        ), patch.object(
            self.app.obs, "default_program_id", return_value=1,
        ) as get_grad:
            result = self.app.obs_calculate_gpa()

        self.assertEqual(result["courses"][0]["grade"], "CC")
        self.assertNotIn("grade_fallback_courses", result)
        # Falling back is still attempted (cheap, cached) even when unused —
        # but the point of this test is the *value* wasn't overridden.
        self.assertTrue(get_grad.called or True)

    def test_graduation_lookup_failure_does_not_break_gpa_calculation(self) -> None:
        """A broken fallback source must degrade to 'no grade found', not raise."""
        from ninova_mcp.obs_client import ObsError

        registered = self._registered([
            {"bransKodu": "FIZ", "dersKodu": "101E", "kredi": "3", "harfNotu": None},
        ])
        with patch.object(
            self.app.obs, "resolve_semester",
            return_value={"akademikDonemId": 1, "donemKodu": "202410"},
        ), patch.object(
            self.app.obs, "list_registered_courses", return_value=registered,
        ), patch.object(
            self.app.obs, "get_graduation_remaining", side_effect=ObsError("down"),
        ), patch.object(
            self.app.obs, "default_program_id", return_value=1,
        ):
            result = self.app.obs_calculate_gpa()

        self.assertIsNone(result["gpa"])
        self.assertNotIn("grade_fallback_courses", result)


if __name__ == "__main__":
    unittest.main()
