from __future__ import annotations

import unittest
from unittest.mock import patch

from ninova_mcp.elective_groups import (
    ELECTIVE_GROUP_PATH,
    enrich_elective_group,
    extract_elective_group,
    fetch_elective_group,
)
from ninova_mcp.obs_client import ObsError, ObsPublicClient


GROUP_URL = "https://obs.itu.edu.tr/public/DersPlan/_DersGrupSearch?grupId=9086"
# The structure follows the public group page observed on 2026-09-13.
GROUP_HTML = """
<div class="content-area"><h3>7th Semester Elective Course II (TM)</h3>
<table class="datalist"><thead><tr><th>Course</th><th>Language</th>
<th>Credit</th><th>ECTS</th><th>Theory</th><th>Practice</th><th>Lab</th></tr></thead>
<tbody><tr><td><a href="/public/DersBilgi?bransKodu=UCK&amp;dersNo=358">UCK 358E</a>
<br>Introduction to <span>Artificial Intelligence</span></td><td>English</td>
<td>3</td><td>5</td><td>3</td><td>0</td><td>0</td></tr>
<tr><td><a href="/public/DersBilgi?bransKodu=UZB&amp;dersNo=438">UZB 438E</a>
<br>Robotic Control Systems</td><td>English</td><td>3</td><td>5</td><td>3</td><td>0</td><td>0</td></tr>
<tr><td><a href="/public/DersBilgi?bransKodu=MAK&amp;dersNo=4070">MAK 4070E</a>
<br>Internal Combustion Engines</td><td>English</td><td>2,5</td><td>5,5</td><td>2</td><td>1</td><td>0</td></tr>
</tbody></table></div>
"""


class ElectiveGroupParserTests(unittest.TestCase):
    def test_membership_preserves_language_suffix_and_decimal_credit(self) -> None:
        result = extract_elective_group(GROUP_HTML, GROUP_URL, 9086)
        self.assertTrue(result["complete"])
        self.assertEqual(result["group_name"], "7th Semester Elective Course II (TM)")
        self.assertEqual(result["count"], 3)
        first = result["courses"][0]
        self.assertEqual(first["course_code"], "UCK 358E")
        self.assertEqual(first["course_name"], "Introduction to Artificial Intelligence")
        self.assertEqual(first["course_url"], "https://obs.itu.edu.tr/public/DersBilgi?bransKodu=UCK&dersNo=358")
        self.assertEqual(result["courses"][2]["credit"], 2.5)
        self.assertEqual(result["courses"][2]["ects"], 5.5)

    def test_missing_group_and_unrecognized_html_are_incomplete(self) -> None:
        for html in ("<div class='content-area'><h3></h3><span>No courses</span></div>", "<html>Sign in</html>"):
            with self.subTest(html=html):
                result = extract_elective_group(html, GROUP_URL, 9086)
                self.assertFalse(result["available"])
                self.assertFalse(result["complete"])
                self.assertTrue(result["warnings"])

    def test_one_malformed_row_does_not_claim_complete_membership(self) -> None:
        html = GROUP_HTML.replace("</tbody>", "<tr><td>Unreadable course</td></tr></tbody>")
        result = extract_elective_group(html, GROUP_URL, 9086)
        self.assertEqual(result["count"], 3)
        self.assertFalse(result["complete"])
        self.assertTrue(result["warnings"])


class ElectiveGroupClientTests(unittest.TestCase):
    def test_observed_endpoint_and_cache_are_used(self) -> None:
        client = ObsPublicClient()
        with patch.object(client, "_get_html", return_value=(GROUP_HTML, GROUP_URL)) as fetch:
            result = fetch_elective_group(client, "009086")
            result["courses"][0]["sections"] = [{"crn": "12345"}]
            cached = fetch_elective_group(client, 9086)
        fetch.assert_called_once_with(ELECTIVE_GROUP_PATH, params={"grupId": "9086"})
        self.assertNotIn("sections", cached["courses"][0])

    def test_invalid_ids_do_not_make_requests(self) -> None:
        client = ObsPublicClient()
        with patch.object(client, "_get_html") as fetch:
            for group_id in (0, -1, True, "1.0", "9086&x=y", "１２"):
                with self.subTest(group_id=group_id), self.assertRaises(ObsError):
                    fetch_elective_group(client, group_id)
        fetch.assert_not_called()

    def test_failed_parse_does_not_poison_cache(self) -> None:
        client = ObsPublicClient()
        with patch.object(client, "_get_html", side_effect=[("<html>Error</html>", GROUP_URL), (GROUP_HTML, GROUP_URL)]) as fetch:
            with self.assertRaises(ObsError):
                fetch_elective_group(client, 9086)
            self.assertEqual(fetch_elective_group(client, 9086)["count"], 3)
        self.assertEqual(fetch.call_count, 2)


class ElectiveGroupScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.group = extract_elective_group(GROUP_HTML, GROUP_URL, 9086)

    def test_joins_exact_course_codes_and_keeps_all_section_rules(self) -> None:
        sections = [
            {"code": "UCK 358", "crn": "11111"},
            {"code": "UCK358E", "crn": "22222", "eligible_programs": ["UZBE"], "sessions": [{"day": "Monday", "time": "10:30/12:29"}], "credit_prerequisite": "90"},
            {"code": "UCK 358E", "crn": "33333", "eligible_programs": ["UCKE"]},
        ]
        result = enrich_elective_group(self.group, {
            "UCK": {"semester": "2026-2027 Fall", "courses": sections},
            "UZB": {"semester": "2026-2027 Fall", "courses": [{"code": "UZB 318E", "crn": "44444"}]},
        })
        first, second, third = result["courses"]
        self.assertTrue(first["offered_this_term"])
        self.assertEqual(first["crns"], ["22222", "33333"])
        self.assertEqual(first["sections"][0]["credit_prerequisite"], "90")
        self.assertFalse(second["offered_this_term"])
        self.assertIsNone(third["offered_this_term"])
        self.assertNotIn("sections", self.group["courses"][0])
        self.assertEqual(result["offered_course_count"], 1)

    def test_unpublished_failed_or_undated_schedule_stays_unknown(self) -> None:
        for schedule in (
            {"semester": "2026-2027 Fall", "courses": [], "message": "Not published"},
            {"semester": "2026-2027 Fall", "courses": [], "headers": ["CRN"]},
            {"semester": "2026-2027 Fall", "courses": [], "headers": ["CRN"], "parse_warning": "Unrecognized rows"},
            {"courses": [{"code": "UCK 358E", "crn": "22222"}]},
            {"semester": "Unknown", "courses": [{"code": "UCK 358E", "crn": "22222"}]},
            {"semester": "2026-2027 Fall", "courses": [{"code": "UCK 358E"}]},
            {"semester": "2026-2027 Fall", "courses": [{"code": "UCK 358E", "crn": "22222"}, {"code": "Unreadable row"}]},
        ):
            with self.subTest(schedule=schedule):
                result = enrich_elective_group(self.group, {"UCK": schedule})
                self.assertIsNone(result["courses"][0]["offered_this_term"])
                self.assertEqual(result["courses"][0]["schedule_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
