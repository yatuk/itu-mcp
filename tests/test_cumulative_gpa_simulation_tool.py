"""``obs_simulate_cumulative_gpa`` as a registered tool, with a fake OBS client.

No request leaves the process: every ``ObsClient`` method the tool uses is
patched. All record values are invented.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from ninova_mcp.client import NinovaError
from ninova_mcp.obs_client import ObsError
from ninova_mcp.server import (
    DESTRUCTIVE_TOOL_NAMES,
    REMOTE_TOOL_NAMES,
    STATEFUL_TOOL_NAMES,
    TOOLS,
    NinovaMcpApp,
    register_tools,
)
from ninova_mcp.toolsets import TOOL_TO_TOOLSET

NAME = "obs_simulate_cumulative_gpa"
SEMESTER = {"akademikDonemId": 77, "donemKodu": "202520", "akademikDonemAdi": "2024-2025 Bahar"}
GRADUATION = {"mezuniyetimeNeKaldiBilgi": {
    "gpa": 2.0,
    "dersPlaniVM": {"gerekliMinGPA": 2},
    "checkMetMezuniyetList": [
        {"isMet": True, "bransKodu": "ABC 101E", "dersAdi": "Intro to Widgets", "harfNotu": "CC",
         "donem": "202510", "kredisi": "4", "kredisiDec": 4, "sayilanKredi": 4, "grupName": ""},
        {"isMet": False, "bransKodu": "ABC 205", "dersAdi": "Widget Ethics", "harfNotu": "",
         "donem": None, "kredisi": "2", "kredisiDec": 2, "sayilanKredi": 0, "grupName": ""},
    ],
    "unusedSinifOgrenciList": [],
}}
STATUS = {"statusCode": 0, "kayitDurumuList": [{"kayitDurumuDonemList": [
    {"akademikDonemKodu": "202510", "donemlikNotOrtalamasi": 2.0, "genelNotOrtalamasi": 2.0, "verilenKredi": 4},
]}]}
REGISTERED = {"statusCode": 0, "kayitSinifResultList": [
    {"bransKodu": "ABC", "dersKodu": "205", "crn": "31001", "dersAdiEN": "Widget Ethics",
     "dersAdiTR": "Aygıt Etiği", "kredi": None, "harfNotu": None},
]}


class SimulationToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        env = patch.dict(os.environ, {
            "NINOVA_USERNAME": "dummy", "NINOVA_PASSWORD": "dummy", "NINOVA_STATE_DIR": self.temp_dir.name,
        })
        env.start()
        self.addCleanup(env.stop)
        self.app = NinovaMcpApp()
        self.mocks: dict[str, Any] = {}
        for method, value in (
            ("resolve_semester", SEMESTER),
            ("list_registered_courses", REGISTERED),
            ("default_program_id", 5),
            ("get_graduation_remaining", GRADUATION),
            ("get_registration_status", STATUS),
        ):
            patcher = patch.object(self.app.obs, method, return_value=value)
            self.mocks[method] = patcher.start()
            self.addCleanup(patcher.stop)

    def test_one_call_answers_the_question(self) -> None:
        result = self.app.obs_simulate_cumulative_gpa(default_grade="AA")
        self.assertEqual(result["semester"], {"id": 77, "code": "202520", "name": "2024-2025 Bahar"})
        self.assertEqual(result["current"]["gpa"], 2.0)
        self.assertEqual(result["current"]["model_status"], "reproduced")
        self.assertEqual(result["projected"]["gpa"], 2.67)  # (8 + 8) / 6
        self.assertEqual(result["courses"][0]["effect"], "new")
        self.mocks["resolve_semester"].assert_called_once_with(None)
        self.mocks["list_registered_courses"].assert_called_once_with(77)
        self.mocks["get_graduation_remaining"].assert_called_once_with(5)

    def test_semester_number_is_passed_on_as_text(self) -> None:
        self.app.obs_simulate_cumulative_gpa(semester=202520)
        self.mocks["resolve_semester"].assert_called_once_with("202520")
        self.mocks["resolve_semester"].reset_mock()
        self.app.obs_simulate_cumulative_gpa(semester="  2024-2025 Bahar ")
        self.mocks["resolve_semester"].assert_called_once_with("2024-2025 Bahar")
        with self.assertRaisesRegex(ValueError, "semester must be"):
            self.app.obs_simulate_cumulative_gpa(semester=True)

    def test_bad_arguments_are_rejected_before_obs_is_contacted(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown letter grade"):
            self.app.obs_simulate_cumulative_gpa(default_grade="B+")
        for mock in self.mocks.values():
            mock.assert_not_called()

    def test_missing_official_series_is_a_warning_not_a_failure(self) -> None:
        self.mocks["get_registration_status"].side_effect = ObsError("down")
        result = self.app.obs_simulate_cumulative_gpa(default_grade="AA")
        self.assertEqual(result["projected"]["gpa"], 2.67)
        self.assertTrue(result["current"]["reproduces_official"])
        self.assertTrue(any(w.startswith("official_series_unavailable") for w in result["warnings"]))

    def test_unreadable_record_stops_the_simulation(self) -> None:
        self.mocks["list_registered_courses"].return_value = {"statusCode": 1}
        with self.assertRaisesRegex(NinovaError, "Registered courses could not be read"):
            self.app.obs_simulate_cumulative_gpa()
        self.mocks["list_registered_courses"].return_value = REGISTERED
        self.mocks["get_graduation_remaining"].return_value = {"statusCode": 1}
        with self.assertRaisesRegex(NinovaError, "graduation record could not be read"):
            self.app.obs_simulate_cumulative_gpa()

    def test_registration_metadata(self) -> None:
        self.assertEqual(sum(1 for tool in TOOLS if tool["name"] == NAME), 1)
        self.assertEqual(TOOL_TO_TOOLSET[NAME], "planning")
        self.assertIn(NAME, REMOTE_TOOL_NAMES)
        self.assertNotIn(NAME, STATEFUL_TOOL_NAMES | DESTRUCTIVE_TOOL_NAMES)

    def server(self) -> FastMCP:
        server = FastMCP("gpa-simulation-test")
        register_tools(server, self.app, [NAME])
        return server

    def test_published_schema_types_and_describes_every_parameter(self) -> None:
        tool = asyncio.run(self.server().list_tools())[0]
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertFalse(tool.annotations.destructiveHint)
        self.assertTrue(tool.annotations.idempotentHint)
        schema = tool.inputSchema
        self.assertNotIn("required", schema)
        declared = next(item for item in TOOLS if item["name"] == NAME)["inputSchema"]["properties"]
        self.assertEqual(set(schema["properties"]), set(declared))
        expected = {
            "projected_grades": {"object", "null"},
            "default_grade": {"string", "null"},
            "extra_courses": {"array", "null"},
            "semester": {"string", "integer", "null"},
        }
        for name, types in expected.items():
            with self.subTest(parameter=name):
                published = schema["properties"][name]
                self.assertEqual({option["type"] for option in published["anyOf"]}, types)
                self.assertEqual(published["description"], declared[name]["description"])
                self.assertIn("e.g.", published["description"])
        grades = schema["properties"]["projected_grades"]["anyOf"][0]
        self.assertEqual(grades["additionalProperties"], {"type": "string"})
        extra = schema["$defs"]["ExtraCourse"]
        self.assertEqual(extra["required"], ["code", "grade"])
        self.assertFalse(extra["additionalProperties"])
        self.assertEqual(extra["properties"]["credit"]["type"], "number")
        for name, item in extra["properties"].items():
            with self.subTest(extra_field=name):
                self.assertTrue(item["description"])

    def test_call_through_the_sdk(self) -> None:
        server = self.server()
        _, structured = asyncio.run(server.call_tool(NAME, {
            "projected_grades": {"31001": "BB"},
            "extra_courses": [{"code": "FUT 400", "grade": "AA", "credit": 3}],
            "semester": 202520,
        }))
        self.assertEqual(structured["projected"]["points"], 26.0)  # 8 + 6 + 12
        self.assertEqual(structured["projected"]["credits"], 9.0)
        self.assertTrue(structured["untrusted_external_content"])
        for arguments in (
            {"default_grade": 3},
            {"extra_courses": [{"code": "FUT 400", "grade": "AA", "ects": 5}]},
            {"extra_courses": [{"code": "FUT 400"}]},
            {"projected_grades": {"31001": "ZZ"}},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolError):
                    asyncio.run(server.call_tool(NAME, arguments))


if __name__ == "__main__":
    unittest.main()
