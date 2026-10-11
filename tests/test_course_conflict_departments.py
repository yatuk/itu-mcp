"""``check_course_conflicts`` finds the departments of the given CRNs itself.

Every source is a fake and every code, CRN and name is invented. No client
that could log in or send a request is ever built: the real client classes
are replaced with ones that fail the test when constructed.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from mcp.server.fastmcp import FastMCP

from ninova_mcp.archive_client import ItuArchiveError
from ninova_mcp.obs_client import ObsError
from ninova_mcp.server import REMOTE_TOOL_NAMES, TOOLS, NinovaMcpApp, register_tools

TERM = "2031-2032 Güz Dönemi"
NAME = "check_course_conflicts"


def section(crn: str, code: str, day: str, time: str) -> dict[str, Any]:
    return {"crn": crn, "code": code, "name": f"{code} dersi",
            "sessions": [{"day": day, "time": time, "room": "Z1"}]}


class FakeSchedules:
    def __init__(self) -> None:
        self.term: str | None = TERM
        self.branches: dict[str, list[dict[str, Any]]] = {
            "QAB": [section("51001", "QAB 101E", "Pazartesi", "08:30/10:29"),
                    section("51002", "QAB 205", "Salı", "08:30/10:29")],
            "ZED": [section("52001", "ZED 110", "Pazartesi", "09:30/11:29")],
            "WYX": [section("53001", "WYX 120E", "Çarşamba", "13:30/15:29")],
            "BLG": [section("59001", "BLG 999", "Cuma", "08:30/10:29")],
        }
        self.calls: list[tuple[str, str]] = []

    def get_active_semester(self, program_type: str) -> str:
        if self.term is None:
            raise ObsError("dönem okunamadı")
        return self.term

    def get_course_schedule(self, program_type: str, department: str) -> dict[str, Any]:
        self.calls.append((program_type, department))
        if department not in self.branches:
            raise ObsError(f"Bölüm kodu bulunamadı: {department!r}")
        return {"courses": self.branches[department], "department_code": department, "semester": self.term}


class FakeArchive:
    def __init__(self, rows: list[list[Any]] | None, term: str = TERM) -> None:
        self.rows = rows
        self.term = term
        self.fail = False

    def get_index(self) -> dict[str, Any]:
        if self.fail:
            raise ItuArchiveError("arşiv kapalı")
        return {"currentSlug": "2031-2032-guz", "currentTerm": self.term}

    def get_term_search(self, slug: str) -> list[list[Any]] | None:
        return self.rows


ARCHIVE_ROWS = [
    ["51001", "QAB 101E", "Gizmos", "QAB", "A", "", 80, 80, "LS", "", []],
    ["52001", "ZED 110", "Sprockets", "ZED", "B", "", 60, 10, "LS", "", []],
    ["short"],
]
REGISTERED = {"statusCode": 0, "kayitSinifResultList": [
    {"crn": 51001, "bransKodu": "QAB", "dersKodu": "101E"},
    {"crn": "53001", "bransKodu": "wyx", "dersKodu": "120E"},
    {"crn": "53002", "bransKodu": None, "dersKodu": "121E"},
    "junk",
]}


class ConflictDepartmentsTestCase(unittest.TestCase):
    credentials = True

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ, {"NINOVA_STATE_DIR": self._tmp.name})
        env.start()
        self.addCleanup(env.stop)
        if self.credentials:
            os.environ.update({"NINOVA_USERNAME": "dummy", "NINOVA_PASSWORD": "dummy"})
        else:
            os.environ.pop("NINOVA_USERNAME", None)
            os.environ.pop("NINOVA_PASSWORD", None)
        # A real client must never be built here: it would try to log in.
        for client in ("NinovaClient", "ObsClient"):
            patcher = patch(f"ninova_mcp.server.{client}", side_effect=AssertionError(f"{client} was built"))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = NinovaMcpApp()
        self.schedules = FakeSchedules()
        self.app._obs_public = self.schedules  # type: ignore[assignment]
        self.archive = FakeArchive(ARCHIVE_ROWS)
        self.app._archive = self.archive  # type: ignore[assignment]
        self.obs = MagicMock()
        self.obs.resolve_semester.return_value = {"akademikDonemId": 91, "donemKodu": "203210"}
        self.obs.list_registered_courses.return_value = REGISTERED
        self.app._obs = self.obs

    def scanned(self) -> list[str]:
        return [department for _, department in self.schedules.calls]


class ResolutionTests(ConflictDepartmentsTestCase):
    def test_departments_come_from_the_archive_index_without_login(self) -> None:
        result = self.app.check_course_conflicts(["51001", "52001"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["departments_scanned"], ["QAB", "ZED"])
        self.assertEqual(result["department_codes"], ["QAB", "ZED"])
        self.assertEqual(result["departments_resolved_by"], ["archive_search_index"])
        self.assertEqual(result["missing_crns"], [])
        self.assertEqual((result["course_count"], result["conflict_count"]), (2, 1))
        self.assertFalse(result["ok"])
        self.assertNotIn("BLG", self.scanned())
        self.obs.resolve_semester.assert_not_called()
        self.obs.list_registered_courses.assert_not_called()

    def test_registered_courses_supply_what_the_archive_lacks(self) -> None:
        result = self.app.check_course_conflicts(["51001", "53001"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["departments_scanned"], ["QAB", "WYX"])
        self.assertEqual(result["departments_resolved_by"], ["archive_search_index", "obs_registered_courses"])
        self.assertTrue(result["ok"])
        self.obs.list_registered_courses.assert_called_once_with(91)

    def test_registered_courses_alone_when_the_archive_is_down(self) -> None:
        self.archive.fail = True
        result = self.app.check_course_conflicts([" 51001 ", 53001])  # type: ignore[list-item]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["departments_resolved_by"], ["obs_registered_courses"])
        self.assertEqual(result["departments_scanned"], ["QAB", "WYX"])
        self.assertTrue(any("arşiv" in note.lower() for note in result["resolution_notes"]))

    def test_archive_of_another_term_is_not_trusted(self) -> None:
        self.archive.term = "2030-2031 Bahar Dönemi"
        result = self.app.check_course_conflicts(["51001", "52001"])
        # Only 51001 is among the registered courses.
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["departments_scanned"], ["QAB"])
        self.assertEqual(result["missing_crns"], ["52001"])
        self.assertIn("department_codes", result["hint"])

    def test_obs_failure_degrades_to_a_structured_answer(self) -> None:
        for error in (ObsError("OBS kapalı"), RuntimeError("beklenmeyen"), KeyError("akademikDonemId")):
            with self.subTest(error=type(error).__name__):
                self.schedules.calls.clear()
                self.obs.resolve_semester.side_effect = error
                result = self.app.check_course_conflicts(["53001", "54001"])
                self.assertEqual(result["status"], "departments_required")
                self.assertEqual(result["missing_crns"], ["53001", "54001"])
                self.assertEqual(result["departments_scanned"], [])
                self.assertIn("department_codes", result["hint"])
                self.assertTrue(result["resolution_notes"])
                self.assertEqual(self.schedules.calls, [])
                for key in ("error", "ok", "conflicts", "conflict_count"):
                    self.assertNotIn(key, result)

    def test_unusable_registered_payload_is_not_an_exception(self) -> None:
        for payload in (None, [], {"statusCode": 1}, {"kayitSinifResultList": "x"}):
            with self.subTest(payload=payload):
                self.obs.list_registered_courses.return_value = payload
                result = self.app.check_course_conflicts(["53001"])
                self.assertEqual(result["status"], "departments_required")

    def test_resolved_department_that_obs_does_not_list_is_reported(self) -> None:
        self.archive.rows = [["55001", "NOP 101", "Nopes", "NOP", "A", "", 1, 1, "LS", "", []], *ARCHIVE_ROWS]
        result = self.app.check_course_conflicts(["51001", "55001"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["missing_crns"], ["55001"])
        self.assertEqual(result["departments_scanned"], ["QAB"])
        self.assertTrue(any("NOP" in note for note in result["resolution_notes"]))


class NoCredentialsTests(ConflictDepartmentsTestCase):
    credentials = False

    def setUp(self) -> None:
        super().setUp()
        self.app._obs = None  # nothing logged in yet in this process

    def test_archive_route_needs_no_login(self) -> None:
        result = self.app.check_course_conflicts(["51001", "52001"])
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(self.app._obs)
        self.assertIsNone(self.app._client)

    def test_unresolved_crns_do_not_trigger_a_login(self) -> None:
        result = self.app.check_course_conflicts(["53001"])
        self.assertEqual(result["status"], "departments_required")
        self.assertIsNone(self.app._obs)
        self.assertIsNone(self.app._client)
        self.assertTrue(any("giriş" in note for note in result["resolution_notes"]))

    def test_explicit_departments_never_touch_archive_or_obs(self) -> None:
        self.archive.fail = True
        result = self.app.check_course_conflicts(["51001", "52001"], department_codes=["qab", "ZED", "QAB"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["departments_resolved_by"], ["parameter"])
        self.assertEqual(self.scanned(), ["QAB", "ZED"])
        self.assertNotIn("resolution_notes", result)


class ExplicitDepartmentTests(ConflictDepartmentsTestCase):
    def test_not_found_is_a_structured_status_without_a_crn_dump(self) -> None:
        result = self.app.check_course_conflicts(["51001", "52001"], department_code="BLG")
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["missing_crns"], ["51001", "52001"])
        self.assertEqual(result["departments_scanned"], ["BLG"])
        self.assertIn("BLG", result["hint"])
        self.assertNotIn("error", result)
        self.assertNotIn("59001", str(result))
        self.obs.list_registered_courses.assert_not_called()

    def test_some_missing_crns_are_still_reported(self) -> None:
        result = self.app.check_course_conflicts(["51001", "51002", "52001"], department_code="qab")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["missing_crns"], ["52001"])
        self.assertEqual(result["course_count"], 2)
        self.assertEqual(result["program_type"], "LS")
        self.assertIn("hint", result)

    def test_argument_errors(self) -> None:
        with self.assertRaises(ObsError):
            self.app.check_course_conflicts([])
        with self.assertRaises(ObsError):
            self.app.check_course_conflicts(["  "])
        with self.assertRaises(ObsError):
            self.app.check_course_conflicts(["51001"], department_codes=[f"A{chr(65 + i)}C" for i in range(26)])
        with self.assertRaises(ObsError):
            self.app.check_course_conflicts(["51001"], department_code="NOP")

    def test_metadata_no_longer_defaults_to_one_department(self) -> None:
        declared = next(tool for tool in TOOLS if tool["name"] == NAME)
        properties = declared["inputSchema"]["properties"]
        self.assertNotIn("default", properties["department_code"])
        self.assertIn(NAME, REMOTE_TOOL_NAMES)
        self.assertIn("department", declared["description"].lower())
        server = FastMCP("conflict-test")
        register_tools(server, self.app, [NAME])
        tool = asyncio.run(server.list_tools())[0]
        self.assertEqual(tool.inputSchema["required"], ["crns"])
        self.assertEqual(set(tool.inputSchema["properties"]), set(properties))
        self.assertIsNone(tool.inputSchema["properties"]["department_code"].get("default"))
        _, structured = asyncio.run(server.call_tool(NAME, {"crns": ["51001", "52001"]}))
        self.assertEqual(structured["result"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
