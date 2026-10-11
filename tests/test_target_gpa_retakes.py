"""Target GPA with retakes: ``calculate_target_gpa`` and the simulation's target mode.

Every number, code and CRN below is invented. No request leaves the process.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from ninova_mcp.gpa import calculate_target_gpa
from ninova_mcp.gpa_simulation import simulate_cumulative_gpa
from ninova_mcp.server import TOOLS, NinovaMcpApp, register_tools

# 101 credits at 1.89 is 190.89 points. 18 credits are planned, 12 of them retakes.
STUDENT = dict(current_gpa=1.89, current_credits=101, future_credits=18)
RETAKES = [
    {"credit": 3, "old_grade": "FF"}, {"credit": 3, "old_grade": "FF"},
    {"credit": 3, "old_grade": "ff"}, {"credit": 3, "old_grade": "DD"},
]


class TargetGpaWithRetakesTests(unittest.TestCase):
    def test_retakes_do_not_grow_the_denominator(self) -> None:
        result = calculate_target_gpa(**STUDENT, target_gpa=2.4, retakes=RETAKES)
        # Old points 3 leave: 187.89. Denominator 101 + 6 new credits = 107.
        self.assertTrue(result["feasible_on_4_scale"])
        self.assertEqual(result["maximum_possible_gpa"], 2.43)  # (187.89 + 72) / 107
        self.assertEqual(result["required_future_average"], 3.83)  # (256.8 - 187.89) / 18
        self.assertEqual(result["required_future_points"], 68.91)
        self.assertTrue(result["retakes_considered"])
        self.assertEqual((result["retake_credits"], result["retake_old_points"]), (12.0, 3.0))
        self.assertEqual((result["new_credits"], result["resulting_credits"]), (6.0, 107.0))
        self.assertNotIn("assumption", result)
        json.dumps(result, allow_nan=False)

    def test_without_retakes_the_verdict_names_its_assumption(self) -> None:
        result = calculate_target_gpa(**STUDENT, target_gpa=2.4)
        self.assertFalse(result["feasible_on_4_scale"])
        self.assertEqual(result["maximum_possible_gpa"], 2.21)
        self.assertFalse(result["retakes_considered"])
        self.assertIn("obs_simulate_cumulative_gpa", result["assumption"])
        self.assertIn("retakes", result["assumption"])
        self.assertEqual(result["resulting_credits"], 119.0)

    def test_existing_fields_keep_their_values_without_retakes(self) -> None:
        result = calculate_target_gpa(current_gpa=3.0, current_credits=60, target_gpa=3.2, future_credits=30)
        self.assertEqual(result["required_future_average"], 3.6)
        self.assertEqual(result["required_future_points"], 108.0)
        self.assertTrue(result["feasible_on_4_scale"])
        self.assertEqual(result["maximum_possible_gpa"], 3.33)
        self.assertEqual(calculate_target_gpa(
            current_gpa=3.0, current_credits=60, target_gpa=3.2, future_credits=30, retakes=[],
        )["required_future_average"], 3.6)

    def test_invalid_retakes_are_rejected(self) -> None:
        bad: list[Any] = [
            "x", [3], [{"credit": 3}], [{"old_grade": "FF"}], [{"credit": 0, "old_grade": "FF"}],
            [{"credit": -3, "old_grade": "FF"}], [{"credit": True, "old_grade": "FF"}],
            [{"credit": float("nan"), "old_grade": "FF"}], [{"credit": 3, "old_grade": "ZZ"}],
            [{"credit": 3, "old_grade": "BL"}], [{"credit": 3, "old_grade": 2}],
            [{"credit": 3, "old_grade": "FF", "code": "ABC 101"}],
            [{"credit": 19, "old_grade": "FF"}],  # more than future_credits
        ]
        for retakes in bad:
            with self.subTest(retakes=retakes), self.assertRaises(ValueError):
                calculate_target_gpa(**STUDENT, target_gpa=2.4, retakes=retakes)
        with self.assertRaisesRegex(ValueError, "more points than"):
            calculate_target_gpa(current_gpa=1.0, current_credits=6, target_gpa=2, future_credits=6,
                                 retakes=[{"credit": 3, "old_grade": "AA"}])
        with self.assertRaisesRegex(ValueError, "current_credits"):
            calculate_target_gpa(current_gpa=4.0, current_credits=3, target_gpa=2, future_credits=6,
                                 retakes=[{"credit": 4, "old_grade": "AA"}])


def met(code: str, name: str, credit: Any, grade: str, term: str) -> dict[str, Any]:
    return {"isMet": True, "bransKodu": code, "dersAdi": name, "harfNotu": grade, "donem": term,
            "kredisi": str(credit), "kredisiDec": credit, "sayilanKredi": credit, "grupName": ""}


def unmet(code: str, name: str, credit: Any) -> dict[str, Any]:
    return {"isMet": False, "bransKodu": code, "dersAdi": name, "harfNotu": "", "donem": None,
            "kredisi": str(credit), "kredisiDec": credit, "sayilanKredi": 0, "grupName": ""}


def unused(code: str, name: str, credit: Any, grade: str, term: str) -> dict[str, Any]:
    return {"bransKodu": code, "dersAdi": name, "kredisi": credit, "harfNotu": grade,
            "donem": term, "isValidAndUnused": False}


def registered(branch: str, number: str, crn: str, name: str) -> dict[str, Any]:
    return {"bransKodu": branch, "dersKodu": number, "crn": crn, "dersAdiEN": name,
            "dersAdiTR": name, "kredi": None, "harfNotu": None}


# 202510: QAB 101E 4 CC, QAB 103E 3 FF, ZED 105 3 FF, QAB 107E 4 DD: 12 / 14 = 0.86.
# Registered in 202520: QAB 103E (retake), WYX 105 (ZED 105 under a new code),
# QAB 107E (retake of a passed course), QAB 201E (new, 4 credits).
GRADUATION = {"mezuniyetimeNeKaldiBilgi": {
    "gpa": 0.86,
    "dersPlaniVM": {"gerekliMinGPA": 2},
    "checkMetMezuniyetList": [
        met("QAB 101E", "Intro to Gizmos", 4, "CC", "202510"),
        met("QAB 107E", "Gizmo Workshop", 4, "DD", "202510"),
        unmet("QAB 103E", "Gizmo Lab", 3),
        unmet("WYX 105", "Sprocket Theory", 3),
        unmet("QAB 201E", "Gizmo Systems", 4),
    ],
    "unusedSinifOgrenciList": [
        unused("QAB 103E", "Gizmo Lab", 3, "FF", "202510"),
        unused("ZED 105", "Sprocket Theory", 3, "FF", "202510"),
    ],
}}
STATUS = {"statusCode": 0, "kayitDurumuList": [{"kayitDurumuDonemList": [
    {"akademikDonemKodu": "202510", "donemlikNotOrtalamasi": 0.86, "genelNotOrtalamasi": 0.86, "verilenKredi": 8},
]}]}
REGISTERED = {"statusCode": 0, "kayitSinifResultList": [
    registered("QAB", "103E", "41001", "Gizmo Lab"),
    registered("WYX", "105", "41002", "Sprocket Theory"),
    registered("QAB", "107E", "41003", "Gizmo Workshop"),
    registered("QAB", "201E", "41004", "Gizmo Systems"),
]}


def simulate(**arguments: Any) -> dict[str, Any]:
    return simulate_cumulative_gpa(GRADUATION, STATUS, REGISTERED, term_code="202520", **arguments)


class SimulationTargetModeTests(unittest.TestCase):
    """Uniform grade g over 14 term credits on an 18 credit record: (8 + 14 g) / 18."""

    def test_lowest_uniform_grade_that_reaches_the_target(self) -> None:
        target = simulate(target_gpa=2.5)["target"]
        # CB (2.5) gives 43 / 18 = 2.39, CB+ (2.75) gives 46.5 / 18 = 2.58.
        self.assertEqual(target["status"], "reachable")
        self.assertEqual(target["minimum_uniform_grade"], "CB+")
        self.assertEqual(target["gpa_with_minimum_grade"], 2.58)
        self.assertEqual(target["maximum_reachable_gpa"], 3.56)  # 64 / 18
        self.assertEqual(target["target_gpa"], 2.5)
        self.assertEqual(target["varied_courses"], ["QAB 103E", "WYX 105", "QAB 107E", "QAB 201E"])
        self.assertEqual(target["retakes_among_varied"], 3)
        self.assertNotIn("fixed_grades", target)

    def test_retakes_make_a_target_reachable_that_plain_arithmetic_rejects(self) -> None:
        # Without the record: (12 + 4 * 14) / 28 = 2.43, so 3.0 looks impossible.
        plain = calculate_target_gpa(current_gpa=0.86, current_credits=14, target_gpa=3.0, future_credits=14)
        self.assertFalse(plain["feasible_on_4_scale"])
        target = simulate(target_gpa=3.0)["target"]
        self.assertEqual((target["status"], target["minimum_uniform_grade"]), ("reachable", "BA"))

    def test_unreachable_target_reports_the_maximum(self) -> None:
        target = simulate(target_gpa=3.8)["target"]
        self.assertEqual(target["status"], "unreachable")
        self.assertIsNone(target["minimum_uniform_grade"])
        self.assertEqual(target["maximum_reachable_gpa"], 3.56)

    def test_named_grades_stay_fixed_and_default_grade_is_not_the_answer(self) -> None:
        result = simulate(target_gpa=2.5, projected_grades={"41004": "FF"}, default_grade="DD")
        target = result["target"]
        # QAB 201E fixed at FF: (8 + 10 g) / 18 needs g >= 3.7, so BA+ (45.5 / 18 = 2.53).
        self.assertEqual(target["minimum_uniform_grade"], "BA+")
        self.assertEqual(target["gpa_with_minimum_grade"], 2.53)
        self.assertEqual(target["fixed_grades"], {"QAB 201E": "FF"})
        self.assertEqual(len(target["varied_courses"]), 3)
        # The projection itself still follows the given grades.
        self.assertEqual(result["projected"]["gpa"], 1.0)  # (8 + 10) / 18

    def test_target_already_met_with_the_lowest_grade(self) -> None:
        target = simulate(target_gpa=0.4)["target"]
        self.assertEqual((target["status"], target["minimum_uniform_grade"]), ("reachable", "FF"))
        self.assertEqual(target["gpa_with_minimum_grade"], 0.44)

    def test_no_course_left_to_vary(self) -> None:
        grades = {crn: "CC" for crn in ("41001", "41002", "41003", "41004")}
        target = simulate(target_gpa=2.5, projected_grades=grades)["target"]
        self.assertEqual(target["status"], "no_courses_to_vary")
        self.assertEqual(target["maximum_reachable_gpa"], 2.0)

    def test_target_alone_does_not_warn_about_missing_grades(self) -> None:
        result = simulate(target_gpa=2.5)
        self.assertFalse(any(w.startswith("no_grades_given") for w in result["warnings"]))
        self.assertNotIn("target", simulate(default_grade="BB"))

    def test_bad_target_is_rejected(self) -> None:
        for value in (True, "x", -0.1, 4.01, float("nan"), [2]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                simulate(target_gpa=value)


class TargetToolTests(unittest.TestCase):
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
            ("resolve_semester", {"akademikDonemId": 88, "donemKodu": "202520", "akademikDonemAdi": "T"}),
            ("list_registered_courses", REGISTERED),
            ("default_program_id", 5),
            ("get_graduation_remaining", GRADUATION),
            ("get_registration_status", STATUS),
        ):
            patcher = patch.object(self.app.obs, method, return_value=value)
            self.mocks[method] = patcher.start()
            self.addCleanup(patcher.stop)

    def server(self, name: str) -> FastMCP:
        server = FastMCP("target-gpa-test")
        register_tools(server, self.app, [name])
        return server

    def test_simulation_tool_takes_a_target(self) -> None:
        server = self.server("obs_simulate_cumulative_gpa")
        _, structured = asyncio.run(server.call_tool("obs_simulate_cumulative_gpa", {"target_gpa": 2.5}))
        self.assertEqual(structured["target"]["minimum_uniform_grade"], "CB+")
        schema = asyncio.run(server.list_tools())[0].inputSchema["properties"]["target_gpa"]
        self.assertEqual({option["type"] for option in schema["anyOf"]}, {"number", "null"})
        self.assertIn("e.g.", schema["description"])
        with self.assertRaises(ToolError):
            asyncio.run(server.call_tool("obs_simulate_cumulative_gpa", {"target_gpa": 5}))

    def test_bad_target_is_rejected_before_obs_is_contacted(self) -> None:
        with self.assertRaises(ValueError):
            self.app.obs_simulate_cumulative_gpa(target_gpa=9)
        for mock in self.mocks.values():
            mock.assert_not_called()

    def test_target_tool_publishes_typed_described_parameters(self) -> None:
        server = self.server("calculate_target_gpa")
        tool = asyncio.run(server.list_tools())[0]
        schema = tool.inputSchema
        declared = next(item for item in TOOLS if item["name"] == "calculate_target_gpa")
        self.assertEqual(set(schema["properties"]), set(declared["inputSchema"]["properties"]))
        self.assertEqual(set(schema["required"]), set(declared["inputSchema"]["required"]))
        for name, item in schema["properties"].items():
            with self.subTest(parameter=name):
                self.assertTrue(item["description"])
                self.assertEqual(item["description"], declared["inputSchema"]["properties"][name]["description"])
                self.assertTrue(item.get("type") or item.get("anyOf"))
        retake = schema["$defs"]["Retake"]
        self.assertEqual(set(retake["required"]), {"credit", "old_grade"})
        self.assertFalse(retake["additionalProperties"])
        self.assertIn("obs_simulate_cumulative_gpa", declared["description"])
        simulation = next(item for item in TOOLS if item["name"] == "obs_simulate_cumulative_gpa")
        self.assertIn("target_gpa", simulation["description"])

    def test_target_tool_call_through_the_sdk(self) -> None:
        server = self.server("calculate_target_gpa")
        arguments = {"current_gpa": 1.89, "current_credits": 101, "target_gpa": 2.4, "future_credits": 18}
        _, wrapped = asyncio.run(server.call_tool("calculate_target_gpa", arguments))
        plain = wrapped["result"]
        self.assertFalse(plain["feasible_on_4_scale"])
        self.assertIn("assumption", plain)
        _, informed = asyncio.run(server.call_tool("calculate_target_gpa", {**arguments, "retakes": RETAKES}))
        self.assertTrue(informed["result"]["feasible_on_4_scale"])
        for bad in ({"retakes": [{"credit": 3}]}, {"retakes": [{"credit": 3, "old_grade": "FF", "x": 1}]}):
            with self.subTest(bad=bad), self.assertRaises(ToolError):
                asyncio.run(server.call_tool("calculate_target_gpa", {**arguments, **bad}))
        for mock in self.mocks.values():
            mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
