"""Tolerant, deterministic course lookup in the Ninova and OBS tools (synthetic data)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import Mock

from mcp.server.fastmcp import FastMCP

from ninova_mcp.client import NinovaError
from ninova_mcp.obs_client import ObsError
from ninova_mcp.server import NinovaMcpApp, register_tools

BASE = "https://ninova.itu.edu.tr"


def ninova_course(code: str | None, title: str, section_id: int, *, crn: str | None = None, section: str | None = None) -> dict:
    label = section or (f"CRN: {crn}" if crn else None)
    return {
        "code": code, "title": title, "url": f"{BASE}/Sinif/7.{section_id}",
        "context": f"{code or ''} {label or ''}".strip(), "section": label, "crn": crn,
    }


WRITING = ninova_course("QQQ 201", "Sentetik Yazma Becerileri", 1, crn="30303")
ARCHITECTURE = ninova_course("ZZT 322E", "Sentetik Mimari", 2, crn="30101")
PHYSICS = ninova_course("ZZF 102E", "Sentetik Fizik II (Elektrik Fiziği)", 3, crn="30202")
INTERNSHIP_A = ninova_course("ZZT 500", "Sentetik Staj", 4, section="2031 Exemption and Abroad")
INTERNSHIP_B = ninova_course("ZZT 500", "Sentetik Staj", 5, section="ZZT 2030 Summer Internship")


class NinovaCourseLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = NinovaMcpApp.__new__(NinovaMcpApp)
        self.app.list_courses = Mock(return_value={
            "courses": [WRITING, ARCHITECTURE, PHYSICS, INTERNSHIP_A, INTERNSHIP_B],
        })

    def test_exact_code_returns_the_course_unmarked(self) -> None:
        for reference in ("ZZT 322E", "zzt322e", " ZZT  322 E "):
            with self.subTest(reference=reference):
                self.assertEqual(self.app._resolve_course(reference), ARCHITECTURE)

    def test_code_with_a_suffix_the_dashboard_lacks_resolves_and_says_so(self) -> None:
        resolved = self.app._resolve_course("QQQ 201A")
        self.assertEqual(resolved["url"], WRITING["url"])
        self.assertEqual(resolved["matched_by"], "code_without_suffix")
        self.assertEqual(resolved["requested"], "QQQ 201A")

    def test_code_without_the_suffix_the_dashboard_has_resolves_and_says_so(self) -> None:
        resolved = self.app._resolve_course("ZZT 322")
        self.assertEqual(resolved["url"], ARCHITECTURE["url"])
        self.assertEqual(resolved["matched_by"], "code_without_suffix")

    def test_two_different_suffixes_never_match_each_other(self) -> None:
        with self.assertRaisesRegex(NinovaError, "not found"):
            self.app._resolve_course("ZZT 322A")

    def test_suffix_tolerance_does_not_pick_between_two_candidates(self) -> None:
        other = ninova_course("ZZT 322A", "Sentetik Mimari Uygulama", 6, crn="30111")
        self.app.list_courses.return_value = {"courses": [ARCHITECTURE, other]}
        with self.assertRaises(NinovaError) as caught:
            self.app._resolve_course("ZZT 322")
        message = str(caught.exception)
        self.assertIn("Ambiguous", message)
        self.assertIn("CRN 30101", message)
        self.assertIn("CRN 30111", message)

    def test_crn_resolves_and_says_so(self) -> None:
        for reference in ("30202", "CRN: 30202", "crn 30202"):
            with self.subTest(reference=reference):
                resolved = self.app._resolve_course(reference)
                self.assertEqual(resolved["url"], PHYSICS["url"])
                self.assertEqual(resolved["matched_by"], "crn")

    def test_full_name_ignores_case_and_accents(self) -> None:
        for reference in ("sentetik mimari", "SENTETİK MİMARİ", "Sentetık Mımarı"):
            with self.subTest(reference=reference):
                self.assertEqual(self.app._resolve_course(reference), ARCHITECTURE)

    def test_name_words_and_numerals_resolve_and_say_so(self) -> None:
        for reference in ("fizik 2", "elektrik fizigi", "Fizik II"):
            with self.subTest(reference=reference):
                resolved = self.app._resolve_course(reference)
                self.assertEqual(resolved["url"], PHYSICS["url"])
                self.assertEqual(resolved["matched_by"], "name_words")

    def test_two_sections_of_one_course_are_listed_with_their_labels(self) -> None:
        with self.assertRaises(NinovaError) as caught:
            self.app._resolve_course("ZZT 500")
        message = str(caught.exception)
        self.assertIn("Ambiguous", message)
        self.assertIn("2031 Exemption and Abroad", message)
        self.assertIn("ZZT 2030 Summer Internship", message)
        self.assertIn(INTERNSHIP_A["url"], message)
        self.assertIn(INTERNSHIP_B["url"], message)

    def test_a_section_label_picks_one_of_two_sections(self) -> None:
        resolved = self.app._resolve_course("2031 Exemption")
        self.assertEqual(resolved["url"], INTERNSHIP_A["url"])
        self.assertEqual(resolved["matched_by"], "partial_text")

    def test_ambiguous_name_is_an_error_listing_the_candidates(self) -> None:
        with self.assertRaises(NinovaError) as caught:
            self.app._resolve_course("sentetik")
        self.assertIn("QQQ 201", str(caught.exception))
        self.assertIn("ZZF 102E", str(caught.exception))

    def test_unknown_course_lists_what_is_available(self) -> None:
        with self.assertRaisesRegex(NinovaError, "Course not found: YYY 999.*QQQ 201"):
            self.app._resolve_course("YYY 999")

    def test_tool_result_shows_which_course_a_tolerant_match_used(self) -> None:
        self.app._client = Mock(base_url=BASE)
        self.app._client.get_html.return_value = ("<html><body></body></html>", Mock(url=WRITING["url"]))
        result = self.app.get_course_sections("QQQ 201A")
        self.assertEqual(result["course"]["code"], "QQQ 201")
        self.assertEqual(result["course"]["matched_by"], "code_without_suffix")


def obs_row(class_id: int, branch: str, number: str, name: str, crn: str, name_en: str = "") -> dict:
    return {"sinifId": class_id, "bransKodu": branch, "dersKodu": number, "dersAdiTR": name,
            "dersAdiEN": name_en, "crn": crn}


OBS_PHYSICS_1 = obs_row(1, "ZZF", "101E", "Sentetik Fizik I (Mekanik)", "30201", "Synthetic Physics I")
OBS_PHYSICS_2 = obs_row(2, "ZZF", "102E", "Sentetik Fizik II (Elektrik Fiziği)", "30202", "Synthetic Physics II")
OBS_WRITING = obs_row(3, "QQQ", "201A", "Sentetik Yazma Becerileri", "30303", "Synthetic Writing Skills")
OBS_DESIGN_4 = obs_row(4, "ZZT", "404", "Sentetik Tasarım IV", "30404", "Synthetic Design IV")


class ObsCourseLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = NinovaMcpApp.__new__(NinovaMcpApp)
        self.app._obs = Mock()
        self.app._obs.resolve_semester.return_value = {"akademikDonemId": 77}
        self.app._obs.list_registered_courses.return_value = {
            "kayitSinifResultList": [OBS_PHYSICS_1, OBS_PHYSICS_2, OBS_WRITING, OBS_DESIGN_4],
        }

    def resolve(self, reference: str) -> dict:
        return self.app._resolve_obs_class(class_id=None, semester=None, course=reference)

    def test_arabic_numeral_matches_a_roman_numeral_in_the_name(self) -> None:
        resolved = self.resolve("fizik 2")
        self.assertEqual(resolved["sinifId"], 2)
        self.assertEqual(resolved["matched_by"], "name_words")
        self.assertEqual(resolved["requested"], "fizik 2")
        self.assertEqual(self.resolve("Fizik 1")["sinifId"], 1)
        self.assertEqual(self.resolve("tasarim 4")["sinifId"], 4)
        self.assertEqual(self.resolve("physics ii")["sinifId"], 2)

    def test_name_lookup_ignores_case_and_accents(self) -> None:
        self.assertEqual(self.resolve("ELEKTRIK FIZIGI")["sinifId"], 2)
        self.assertEqual(self.resolve("sentetik tasarım ıv")["sinifId"], 4)

    def test_full_name_is_an_exact_match(self) -> None:
        resolved = self.resolve("sentetik yazma becerileri")
        self.assertEqual(resolved["sinifId"], 3)
        self.assertEqual(resolved["matched_by"], "name")

    def test_code_lookup_ignores_spacing_and_case(self) -> None:
        for reference in ("ZZF 102E", "zzf102e"):
            with self.subTest(reference=reference):
                resolved = self.resolve(reference)
                self.assertEqual(resolved["sinifId"], 2)
                self.assertEqual(resolved["matched_by"], "code")

    def test_code_without_the_suffix_resolves_and_says_so(self) -> None:
        resolved = self.resolve("QQQ 201")
        self.assertEqual(resolved["sinifId"], 3)
        self.assertEqual(resolved["matched_by"], "code_without_suffix")

    def test_crn_resolves(self) -> None:
        resolved = self.resolve("30404")
        self.assertEqual(resolved["sinifId"], 4)
        self.assertEqual(resolved["matched_by"], "crn")

    def test_ambiguous_name_is_an_error_listing_candidates_with_crn(self) -> None:
        with self.assertRaises(ObsError) as caught:
            self.resolve("sentetik fizik")
        message = str(caught.exception)
        self.assertIn("Ambiguous", message)
        self.assertIn("ZZF 101E", message)
        self.assertIn("ZZF 102E", message)
        self.assertIn("CRN 30201", message)
        self.assertIn("CRN 30202", message)
        self.assertNotIn("QQQ", message)

    def test_unknown_course_lists_what_is_available(self) -> None:
        with self.assertRaisesRegex(ObsError, "OBS course not found: fizik 3.*ZZF 102E"):
            self.resolve("fizik 3")

    def test_resolved_class_keeps_its_existing_fields(self) -> None:
        resolved = self.resolve("ZZF 102E")
        self.assertEqual(resolved["source"], "course_lookup")
        self.assertEqual(resolved["semester"], {"akademikDonemId": 77})
        self.assertEqual(resolved["crn"], "30202")

    def test_class_id_bypasses_the_lookup(self) -> None:
        resolved = self.app._resolve_obs_class(class_id=9, semester=None, course=None)
        self.assertEqual(resolved, {"sinifId": 9, "source": "class_id"})

    def test_compact_lower_case_code_resolves(self) -> None:
        resolved = self.resolve("zzt404")
        self.assertEqual(resolved["sinifId"], 4)
        self.assertEqual(resolved["matched_by"], "code")

    def test_not_found_error_names_the_term_that_was_searched(self) -> None:
        self.app._obs.resolve_semester.return_value = {"akademikDonemId": 77, "akademikDonemAdi": "2031-2032 Güz"}
        with self.assertRaisesRegex(ObsError, r"term searched: 2031-2032 Güz"):
            self.resolve("YYY 999")

    def test_exam_calendar_filter_accepts_a_compact_code(self) -> None:
        self.app._obs.get_final_calendar.return_value = {"finalTakvimi": []}
        result = self.app.get_personal_exam_calendar(course="zzf102e")
        self.assertEqual([item["sinifId"] for item in result["matched_courses"]], [2])
        result = self.app.get_personal_exam_calendar(course="fizik")
        self.assertEqual([item["sinifId"] for item in result["matched_courses"]], [1, 2])

    def test_exam_calendar_error_lists_the_registered_courses(self) -> None:
        self.app._obs.resolve_semester.return_value = {"akademikDonemId": 77, "akademikDonemAdi": "2031-2032 Güz"}
        self.app._obs.get_final_calendar.return_value = {"finalTakvimi": []}
        with self.assertRaises(ObsError) as caught:
            self.app.get_personal_exam_calendar(course="yyy999")
        message = str(caught.exception)
        self.assertIn("yyy999", message)
        self.assertIn("ZZF 102E", message)
        self.assertIn("QQQ 201A", message)
        self.assertIn("2031-2032 Güz", message)


class CourseArgumentTypeTests(unittest.TestCase):
    """A CRN arrives as a JSON number from some clients."""

    NAMES = (
        "get_course_announcements", "get_course_assignments", "get_course_info", "get_course_sections",
        "get_course_grades", "get_course_message_board", "get_course_attendance",
        "get_course_remote_learning", "get_course_class_files", "get_course_lesson_files",
        "get_course_overview", "get_assignment_upload_slots", "get_updates",
    )

    def test_course_accepts_a_number_on_the_ninova_course_tools(self) -> None:
        server = FastMCP("course-types")
        register_tools(server, NinovaMcpApp.__new__(NinovaMcpApp), list(self.NAMES))
        tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        self.assertEqual(set(tools), set(self.NAMES))
        for name, tool in tools.items():
            with self.subTest(tool=name):
                options = tool.inputSchema["properties"]["course"].get("anyOf", [])
                self.assertLessEqual({"string", "integer"}, {option.get("type") for option in options})

    def test_numeric_crn_resolves_like_its_string(self) -> None:
        app = NinovaMcpApp.__new__(NinovaMcpApp)
        app.list_courses = Mock(return_value={"courses": [WRITING, PHYSICS]})
        resolved = app._resolve_course(30202)
        self.assertEqual(resolved["url"], PHYSICS["url"])
        self.assertEqual(resolved["matched_by"], "crn")


class UploadTargetLookupTests(unittest.TestCase):
    def test_course_url_of_one_of_two_same_code_sections_reaches_its_assignments(self) -> None:
        app = NinovaMcpApp.__new__(NinovaMcpApp)
        app._client = Mock(base_url=BASE)
        app.list_courses = Mock(return_value={"courses": [INTERNSHIP_A, INTERNSHIP_B]})
        app._require_upload_page_url = Mock()  # the page checks have their own tests
        upload = INTERNSHIP_B["url"] + "/Odev/11/OdevGonder"
        app.get_course_assignments = Mock(return_value={"assignments": [
            {"title": "Sentetik Belge", "url": INTERNSHIP_B["url"] + "/Odev/11", "upload_url": upload},
        ]})
        resolved = app._resolve_assignment_upload_url(
            course=INTERNSHIP_B["url"], assignment="Sentetik Belge", upload_url=None,
        )
        self.assertEqual(resolved["upload_url"], upload)
        self.assertEqual(resolved["course"]["url"], INTERNSHIP_B["url"])
        self.assertEqual(app.get_course_assignments.call_args.args[0], INTERNSHIP_B["url"])


if __name__ == "__main__":
    unittest.main()
