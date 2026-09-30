"""Course discovery must preserve full sections, meetings and source warnings."""
from __future__ import annotations

import asyncio
import copy
from pathlib import Path
import unittest
from unittest.mock import Mock

from ninova_mcp.obs_client import ObsError
from ninova_mcp.parsing import extract_course_schedule_table
from ninova_mcp.planning import filter_course_schedule
from ninova_mcp.server import NinovaMcpApp, TOOLS, register_tools


class CourseScheduleQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        html = (Path(__file__).parent / "fixtures" / "ders_program_result.html").read_text(encoding="utf-8")
        self.schedule = extract_course_schedule_table(html, "https://obs.itu.edu.tr/test", "https://obs.itu.edu.tr")
        self.schedule.update({"semester": "2025-2026 Yaz", "department_code": "BLG"})
        self.before = copy.deepcopy(self.schedule)
        self.app = NinovaMcpApp.__new__(NinovaMcpApp)
        self.public = Mock()
        self.public.get_course_schedule.return_value = self.schedule
        self.app._obs_public = self.public

    def test_legacy_calls_preserve_full_response_and_crn_route(self) -> None:
        result = self.app.get_public_course_schedule("LS", "BLG")
        self.assertEqual(result, self.before)
        self.public.get_course_schedule.assert_called_once_with("LS", "BLG")
        crn_result = {**self.schedule, "count": 1, "courses": self.schedule["courses"][:1], "filtered_by_crn": "30334"}
        self.public.get_course_schedule_by_crn.return_value = crn_result
        self.assertEqual(self.app.get_public_course_schedule("LS", "BLG", "30334"), crn_result)
        self.public.get_course_schedule_by_crn.assert_called_once_with("LS", "BLG", "30334")

    def test_full_section_keeps_all_meetings_and_does_not_modify_cache(self) -> None:
        result = self.app.get_public_course_schedule("LS", "BLG", query="blg223e")
        self.assertEqual(result["count"], 1)
        course = result["courses"][0]
        self.assertEqual(course["capacity"], course["enrolled"])
        self.assertEqual(len(course["sessions"]), 4)
        self.assertEqual(course, next(row for row in self.before["courses"] if row["code"] == "BLG 223E"))
        self.assertEqual(result["semester"], self.before["semester"])
        self.assertEqual(result["url"], self.before["url"])
        self.assertEqual(self.schedule, self.before)
        self.assertEqual(self.app.get_public_course_schedule("LS", "BLG"), self.before)

    def test_name_matching_turkish_accents_english_case_and_whitespace(self) -> None:
        schedule = {"courses": [
            {"code": "MAT 201E", "name": "Differential Equations", "capacity": 30, "enrolled": 30},
            {"code": "MAT 201", "name": "DİFERANSİYEL DENKLEMLER"},
            {"code": "UZB 301E", "name": "Measurement Techniques"},
            {"code": None, "name": None},
        ]}
        for query, code in (("  differential   equations ", "MAT 201E"),
                            ("diferansiyel", "MAT 201"), ("MEASUREMENT", "UZB 301E")):
            with self.subTest(query=query):
                result = filter_course_schedule(schedule, query=query)
                self.assertEqual([row["code"] for row in result["courses"]], [code])

    def test_crn_and_query_intersect_and_missing_crn_keeps_error(self) -> None:
        one = {**self.schedule, "count": 1, "courses": self.schedule["courses"][:1], "filtered_by_crn": "30334"}
        self.public.get_course_schedule_by_crn.return_value = one
        result = self.app.get_public_course_schedule("LS", "BLG", crn="30334", query="unrelated")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["filtered_by_crn"], "30334")
        self.public.get_course_schedule_by_crn.side_effect = ObsError("CRN not found")
        with self.assertRaisesRegex(ObsError, "CRN not found"):
            self.app.get_public_course_schedule("LS", "BLG", crn="missing", query="BLG")

    def test_paging_has_no_missing_or_repeated_sections(self) -> None:
        schedule = {"courses": [{"crn": str(i), "code": "MAT 201E", "name": "Differential Equations"} for i in range(105)]}
        first = filter_course_schedule(schedule, query="Differential")
        second = filter_course_schedule(schedule, query="Differential", offset=first["next_offset"])
        third = filter_course_schedule(schedule, query="Differential", offset=second["next_offset"])
        self.assertEqual([page["count"] for page in (first, second, third)], [50, 50, 5])
        self.assertIsNone(third["next_offset"])
        self.assertEqual(first["matched_count"], 105)
        self.assertEqual(first["total_course_count"], 105)
        self.assertEqual([row["crn"] for page in (first, second, third) for row in page["courses"]], [str(i) for i in range(105)])
        self.assertEqual(filter_course_schedule(schedule, limit=100)["next_offset"], 100)
        self.assertEqual(filter_course_schedule(schedule, offset=200)["count"], 0)

    def test_unknown_or_empty_source_keeps_warning_and_metadata(self) -> None:
        for extra in ({"parse_warning": "Source could not be parsed"}, {"message": "No schedule published"}):
            source = {"courses": [], "count": 0, "url": "https://obs.itu.edu.tr/test", **extra}
            result = filter_course_schedule(source, query="differential")
            for key, value in source.items():
                self.assertEqual(result[key], value)
            self.assertEqual(result["matched_count"], 0)
            self.assertIsNone(result["next_offset"])
        result = filter_course_schedule(self.schedule, query="unrelated")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["total_course_count"], len(self.schedule["courses"]))

    def test_invalid_filters_fail_before_network(self) -> None:
        for kwargs in ({"query": ""}, {"query": "   "}, {"limit": 0}, {"limit": 101},
                       {"limit": True}, {"offset": -1}, {"offset": 0.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.app.get_public_course_schedule("LS", "BLG", **kwargs)
        self.public.get_course_schedule.assert_not_called()
        self.public.get_course_schedule_by_crn.assert_not_called()

    def test_mcp_exposes_and_executes_query(self) -> None:
        from mcp.server.fastmcp import FastMCP

        async def check() -> None:
            mcp = FastMCP("schedule-test")
            register_tools(mcp, self.app, ["get_public_course_schedule"])
            tool = (await mcp.list_tools())[0]
            self.assertTrue({"query", "limit", "offset"} <= tool.inputSchema["properties"].keys())
            self.assertTrue(tool.annotations.readOnlyHint)
            _content, data = await mcp.call_tool("get_public_course_schedule", {"program_type": "LS", "department_code": "BLG", "query": "BLG223E", "limit": 1})
            data = data.get("result", data)
            self.assertEqual(data["count"], 1)
            self.assertEqual(data["courses"][0]["code"], "BLG 223E")
            self.assertTrue(data["untrusted_external_content"])

        asyncio.run(check())
        meta = next(tool for tool in TOOLS if tool["name"] == "get_public_course_schedule")
        self.assertEqual(meta["inputSchema"]["properties"]["limit"]["maximum"], 100)


if __name__ == "__main__":
    unittest.main()
