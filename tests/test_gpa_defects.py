"""Regression coverage for obs_calculate_gpa defects seen in real use.

Every course code, CRN, id, term code and grade below is invented. Only the
field names and the shape of the payloads follow OBS.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import MagicMock, Mock

from mcp.server.fastmcp import FastMCP

from ninova_mcp.gpa_reference import official_term_reference
from ninova_mcp.obs_client import ObsClient, ObsError
from ninova_mcp.server import NinovaMcpApp, register_tools

TERM = "203120"
SEMESTERS = {"ogrenciDonemListesi": [
    {"akademikDonemId": 11, "donemKodu": "203110", "akademikDonemAdi": "2030-2031 Güz Dönemi"},
    {"akademikDonemId": 12, "donemKodu": "203120", "akademikDonemAdi": "2030-2031 Bahar Dönemi"},
    {"akademikDonemId": 13, "donemKodu": "203130", "akademikDonemAdi": "2030-2031 Yaz Dönemi"},
]}


def reg(branch: str, number: str, crn: str, **extra: Any) -> dict[str, Any]:
    return {"bransKodu": branch, "dersKodu": number, "crn": crn, "sinifId": int(crn) + 900000,
            "kredi": None, "harfNotu": None, "dersAdiTR": f"{branch} {number}", **extra}


def met(code: str, grade: str, credit: Any, term: str = TERM, **extra: Any) -> dict[str, Any]:
    return {"isMet": True, "bransKodu": code, "harfNotu": grade, "kredisi": str(credit),
            "kredisiDec": credit, "sayilanKredi": credit, "donem": term, "grupName": "",
            "grupId": None, "dersAdi": f"Course {code}", **extra}


def unmet(code: str, credit: Any, **extra: Any) -> dict[str, Any]:
    return {"isMet": False, "bransKodu": code, "harfNotu": "", "kredisi": str(credit),
            "kredisiDec": credit, "donem": None, "grupName": "", "grupId": None,
            "dersAdi": f"Course {code}", **extra}


def unused(code: str, grade: str, credit: Any, term: str, **extra: Any) -> dict[str, Any]:
    return {"bransKodu": code, "harfNotu": grade, "kredisi": credit, "donem": term,
            "isValidAndUnused": False, "dersAdi": f"Course {code}", **extra}


def official(gpa: Any, credits: Any, term: str = TERM) -> dict[str, Any]:
    return {"statusCode": 0, "kayitDurumuList": [{"akademikProgramAdi": "Sample Program",
            "kayitDurumuDonemList": [
                {"akademikDonemKodu": "203010", "verilenKredi": 9, "donemlikNotOrtalamasi": 3.1,
                 "genelNotOrtalamasi": 3.1},
                {"akademikDonemKodu": term, "verilenKredi": credits, "donemlikNotOrtalamasi": gpa,
                 "genelNotOrtalamasi": 2.4},
            ]}]}


def make_app(registered: list[dict[str, Any]], *, met_rows: list[dict[str, Any]] | None = None,
             unused_rows: list[dict[str, Any]] | None = None,
             status: dict[str, Any] | None = None) -> NinovaMcpApp:
    """An app whose OBS client never leaves the process."""
    app = NinovaMcpApp.__new__(NinovaMcpApp)
    obs = ObsClient(ninova_client=MagicMock())
    obs.list_semesters = Mock(return_value=SEMESTERS)  # type: ignore[method-assign]
    obs.list_registered_courses = Mock(  # type: ignore[method-assign]
        return_value={"statusCode": 0, "kayitSinifResultList": registered})
    obs.default_program_id = Mock(return_value=1)  # type: ignore[method-assign]
    obs.get_graduation_remaining = Mock(return_value={"mezuniyetimeNeKaldiBilgi": {  # type: ignore[method-assign]
        "checkMetMezuniyetList": met_rows or [], "unusedSinifOgrenciList": unused_rows or []}})
    obs.get_registration_status = Mock(  # type: ignore[method-assign]
        return_value=status if status is not None else {})
    app._obs = obs
    return app


def by_code(result: dict[str, Any], code: str) -> dict[str, Any]:
    for bucket in ("courses", "ungraded"):
        for course in result[bucket]:
            if course["code"] == code:
                return course
    raise AssertionError(f"{code} is not in the result")


class SemesterArgumentTests(unittest.TestCase):
    """Defect 1: a term code sent as a JSON number was rejected before the tool ran."""

    def _server(self, app: NinovaMcpApp) -> FastMCP:
        server = FastMCP("gpa-defects")
        register_tools(server, app, ["obs_calculate_gpa"])
        return server

    def test_numeric_term_code_passes_mcp_argument_validation(self) -> None:
        for value in (203120, "203120", ' "203120" ', 203120.0):
            with self.subTest(value=value):
                app = make_app([reg("ZZT", "301E", "40001", kredi="3", harfNotu="BB")])
                asyncio.run(self._server(app).call_tool("obs_calculate_gpa", {"semester": value}))
                app.obs.list_registered_courses.assert_called_once_with(12)

    def test_published_schema_types_and_describes_the_arguments(self) -> None:
        tools = asyncio.run(self._server(make_app([])).list_tools())
        schema = tools[0].inputSchema["properties"]
        semester_types = {option.get("type") for option in schema["semester"]["anyOf"]}
        self.assertLessEqual({"string", "integer", "null"}, semester_types)
        self.assertIn("202410", schema["semester"]["description"])
        self.assertIn("CRN", schema["projected_grades"]["description"])
        self.assertIn("{", schema["projected_grades"]["description"])
        self.assertNotIn("required", tools[0].inputSchema)
        self.assertEqual(tools[0].outputSchema["properties"]["result"]["type"], "object")

    def test_resolve_semester_accepts_code_id_and_name_forms(self) -> None:
        obs = ObsClient(ninova_client=MagicMock())
        obs.list_semesters = Mock(return_value=SEMESTERS)  # type: ignore[method-assign]
        for value in (203120, "203120", " 203120 ", '"203120"', "'203120'", 203120.0, "203120.0",
                      12, "12", "2030-2031 bahar", "2030-2031 Bahar Dönemi"):
            with self.subTest(value=value):
                self.assertEqual(obs.resolve_semester(value)["akademikDonemId"], 12)
        self.assertEqual(obs.resolve_semester(None)["akademikDonemId"], 13)
        for value in (True, 999999, "209920"):
            with self.subTest(value=value), self.assertRaises(ObsError):
                obs.resolve_semester(value)

    def test_exact_id_wins_over_a_name_fragment_of_an_earlier_term(self) -> None:
        obs = ObsClient(ninova_client=MagicMock())
        obs.list_semesters = Mock(return_value={"ogrenciDonemListesi": [  # type: ignore[method-assign]
            {"akademikDonemId": 1, "donemKodu": "203010", "akademikDonemAdi": "Hazırlık 77 Güz"},
            {"akademikDonemId": 77, "donemKodu": "203020", "akademikDonemAdi": "Bahar"},
        ]})
        self.assertEqual(obs.resolve_semester(77)["donemKodu"], "203020")


class ProjectedGradeKeyTests(unittest.TestCase):
    """Defect 2: a CRN key was dropped into unused_projected_courses without a word."""

    def setUp(self) -> None:
        self.registered = [reg("ZZT", "411E", "40011", kredi="3"), reg("ZZT", "412E", "40012", kredi="3")]

    def test_crn_key_is_applied(self) -> None:
        result = make_app(self.registered).obs_calculate_gpa(
            semester=TERM, projected_grades={"40011": "BB", "40012": "AA"}, include_official=False)
        self.assertEqual(result["gpa"], 3.5)
        self.assertEqual(result["applied_projection_count"], 2)
        self.assertEqual(result["unused_projected_courses"], [])
        self.assertEqual(result["projection_status"], "applied")
        self.assertNotIn("unmatched_projected_keys", result)

    def test_course_code_key_ignores_spacing_and_case(self) -> None:
        for key in ("zzt411e", "ZZT 411E", " zzt  411e ", "Zzt411E"):
            with self.subTest(key=key):
                result = make_app(self.registered).obs_calculate_gpa(
                    semester=TERM, projected_grades={key: "BB", "40012": "BB"}, include_official=False)
                self.assertEqual(result["gpa"], 3.0)
                self.assertEqual(result["projection_status"], "applied")

    def test_unmatched_keys_are_named_with_the_valid_keys(self) -> None:
        result = make_app(self.registered).obs_calculate_gpa(
            semester=TERM, projected_grades={"49999": "AA", "40012": "BB"}, include_official=False)
        self.assertEqual(result["unmatched_projected_keys"], ["49999"])
        self.assertEqual(result["valid_projected_keys"], [
            {"code": "ZZT 411E", "crn": "40011"}, {"code": "ZZT 412E", "crn": "40012"}])
        self.assertEqual(result["projection_status"], "partially_applied")
        self.assertIn("49999", result["projection_warning"])
        self.assertFalse(result["projection_complete"])
        self.assertIsNone(result["preferred_term_gpa"])
        self.assertEqual(result["comparison_status"], "projection_incomplete")
        # The existing field keeps listing the key.
        self.assertEqual(result["unused_projected_courses"], ["49999"])

    def test_nothing_matched_is_not_reported_as_a_projection(self) -> None:
        result = make_app(self.registered).obs_calculate_gpa(
            semester=TERM, projected_grades={"QQQ 999": "AA"}, include_official=False)
        self.assertEqual(result["projection_status"], "not_applied")
        self.assertEqual(result["unmatched_projected_keys"], ["QQQ 999"])
        self.assertIsNone(result["preferred_term_gpa"])

    def test_crn_and_code_for_one_course_must_agree(self) -> None:
        app = make_app(self.registered)
        with self.assertRaises(ValueError):
            app.obs_calculate_gpa(semester=TERM, projected_grades={"40011": "AA", "ZZT 411E": "BB"},
                                  include_official=False)
        result = app.obs_calculate_gpa(semester=TERM, projected_grades={"40011": "AA", "zzt411e": "AA"},
                                       include_official=False)
        self.assertEqual(result["applied_projection_count"], 1)


class PlanCreditFallbackTests(unittest.TestCase):
    """Defect 3: the credit lookup saw only rows of the requested term."""

    def test_credit_comes_from_an_earlier_failed_attempt(self) -> None:
        app = make_app([reg("ZZT", "210E", "40021")],
                       unused_rows=[unused("ZZT 210E", "FF", 4, "203010")])
        result = app.obs_calculate_gpa(semester=TERM, include_official=False)
        self.assertNotIn("credits_unresolved", result)
        course = by_code(result, "ZZT 210E")
        self.assertEqual(course["credit"], 4)
        self.assertEqual(course["credit_source"], "degree_plan_other_term")
        self.assertEqual(result["credit_fallback_courses"], ["ZZT 210E"])

    def test_credit_comes_from_an_earlier_passed_attempt_of_a_retake(self) -> None:
        app = make_app([reg("ZZT", "220E", "40022")],
                       met_rows=[met("ZZT 220E", "DD", "3,5", term="203010", kredisiDec=3.5)])
        result = app.obs_calculate_gpa(semester=TERM, projected_grades={"40022": "BB"},
                                       include_official=False)
        self.assertEqual(result["total_credits"], 3.5)
        self.assertEqual(result["gpa"], 3.0)

    def test_credit_comes_from_an_unmet_plan_row(self) -> None:
        app = make_app([reg("ZZT", "230E", "40023")], met_rows=[unmet("ZZT 230E", 2)])
        result = app.obs_calculate_gpa(semester=TERM, include_official=False)
        self.assertEqual(by_code(result, "ZZT 230E")["credit"], 2)
        self.assertEqual(by_code(result, "ZZT 230E")["credit_source"], "degree_plan")

    def test_same_term_row_is_preferred_over_another_attempt(self) -> None:
        app = make_app([reg("ZZT", "240E", "40024")], met_rows=[met("ZZT 240E", "CC", 3)],
                       unused_rows=[unused("ZZT 240E", "FF", 4, "203010")])
        result = app.obs_calculate_gpa(semester=TERM, include_official=False)
        self.assertEqual(by_code(result, "ZZT 240E")["credit"], 3)
        self.assertEqual(by_code(result, "ZZT 240E")["credit_source"], "degree_plan")

    def test_conflicting_credits_across_other_attempts_stay_unresolved(self) -> None:
        app = make_app([reg("ZZT", "250E", "40025")], unused_rows=[
            unused("ZZT 250E", "FF", 3, "203010"), unused("ZZT 250E", "FF", 4, "203020")])
        result = app.obs_calculate_gpa(semester=TERM, include_official=False)
        self.assertEqual(result["credits_unresolved"], ["ZZT 250E"])

    def test_grade_of_another_attempt_is_still_not_borrowed(self) -> None:
        app = make_app([reg("ZZT", "210E", "40021")],
                       unused_rows=[unused("ZZT 210E", "FF", 4, "203010")])
        result = app.obs_calculate_gpa(semester=TERM, include_official=False)
        self.assertEqual(result["ungraded_course_count"], 1)
        self.assertIsNone(result["gpa"])
        self.assertNotIn("grade_fallback_courses", result)


class ElectiveSlotCreditTests(unittest.TestCase):
    """Defect 4: kredisi and sayilanKredi disagree on a met elective row."""

    REGISTERED = [reg("ZZT", "301E", "40031"), reg("ZZT", "302E", "40032"), reg("ZZT", "455E", "40033")]

    def rows(self) -> list[dict[str, Any]]:
        return [
            met("ZZT 301E", "BB", 4), met("ZZT 302E", "CC", 3),
            met("ZZT 455E", "DD", 2, sayilanKredi=3,
                grupName="6th Sems. Elect. Course I (XT)", grupId=7001),
        ]

    def test_credit_that_reproduces_the_official_average_is_used(self) -> None:
        # 4*3.0 + 3*2.0 + 1.0*credit: 2 credits gives 2.22, 3 credits gives 2.10.
        result = make_app(self.REGISTERED, met_rows=self.rows(), status=official(2.1, 10)).obs_calculate_gpa(TERM)
        self.assertEqual(result["gpa"], 2.1)
        self.assertEqual(result["comparison_status"], "match")
        course = by_code(result, "ZZT 455E")
        self.assertEqual(course["credit"], 3)
        self.assertEqual(course["credit_candidates"], [2, 3])
        self.assertEqual(result["credit_ambiguity"]["status"], "resolved_by_official_average")
        self.assertEqual(result["credit_ambiguity"]["courses"], [
            {"code": "ZZT 455E", "kredisi": 2, "sayilanKredi": 3, "used_credit": 3}])

    def test_default_is_kept_and_course_named_when_no_candidate_matches(self) -> None:
        result = make_app(self.REGISTERED, met_rows=self.rows(), status=official(2.9, 10)).obs_calculate_gpa(TERM)
        self.assertEqual(result["gpa"], 2.22)
        self.assertEqual(result["comparison_status"], "mismatch")
        self.assertEqual(result["credit_ambiguity"]["status"], "undetermined")
        self.assertEqual(result["credit_ambiguity"]["courses"][0]["used_credit"], 2)
        self.assertEqual(result["mismatch_ambiguous_credit_courses"], ["ZZT 455E"])
        self.assertIn("ZZT 455E (2 or 3)", result["calculation_warning"])

    def test_default_is_kept_when_both_candidates_match(self) -> None:
        rows = [met("ZZT 301E", "CC", 4), met("ZZT 302E", "CC", 3),
                met("ZZT 455E", "CC", 2, sayilanKredi=3, grupName="Elective", grupId=7001)]
        result = make_app(self.REGISTERED, met_rows=rows, status=official(2.0, 10)).obs_calculate_gpa(TERM)
        self.assertEqual(result["comparison_status"], "match")
        self.assertEqual(by_code(result, "ZZT 455E")["credit"], 2)
        self.assertEqual(result["credit_ambiguity"]["status"], "undetermined")
        self.assertEqual(result["credit_ambiguity"]["courses"][0]["sayilanKredi"], 3)

    def test_default_is_kept_and_course_named_without_an_official_average(self) -> None:
        result = make_app(self.REGISTERED, met_rows=self.rows()).obs_calculate_gpa(TERM, include_official=False)
        self.assertEqual(result["gpa"], 2.22)
        self.assertEqual(result["credit_ambiguity"]["status"], "no_official_average")
        self.assertEqual(result["credit_ambiguity"]["courses"][0]["code"], "ZZT 455E")

    def test_agreeing_rows_are_never_candidates(self) -> None:
        rows = [met("ZZT 301E", "BB", 4), met("ZZT 302E", "CC", 3),
                met("ZZT 455E", "DD", 2, grupName="Elective", grupId=7001)]
        # 3 credits for the elective would reproduce 2.10, but the row does not carry a 3.
        result = make_app(self.REGISTERED, met_rows=rows, status=official(2.1, 10)).obs_calculate_gpa(TERM)
        self.assertEqual(result["gpa"], 2.22)
        self.assertEqual(result["comparison_status"], "mismatch")
        self.assertNotIn("credit_ambiguity", result)
        self.assertEqual(result["mismatch_ambiguous_credit_courses"], [])
        self.assertNotIn("credit_candidates", by_code(result, "ZZT 455E"))

    def test_projection_uses_the_credit_confirmed_by_actual_grades(self) -> None:
        result = make_app(self.REGISTERED, met_rows=self.rows(), status=official(2.1, 10)).obs_calculate_gpa(
            TERM, projected_grades={"40033": "AA"})
        # 12 + 6 + 4.0*3 over 10 credits.
        self.assertEqual(result["gpa"], 3.0)
        self.assertEqual(result["total_credits"], 10)


class AttemptNoteTests(unittest.TestCase):
    """Defect 5: retakes were invisible in the per-course result."""

    def test_earlier_and_later_attempts_are_listed_with_term_and_grade(self) -> None:
        app = make_app([reg("ZZT", "150E", "40041"), reg("ZZT", "160E", "40042")],
                       met_rows=[met("ZZT 150E", "CB", 3, term="203130"), met("ZZT 160E", "BB", 3)],
                       unused_rows=[unused("ZZT 150E", "FF", 3, "203110"), unused("ZZT 150E", "VF", 3, TERM)])
        result = app.obs_calculate_gpa(semester=TERM, include_official=False)
        self.assertEqual(by_code(result, "ZZT 150E")["grade"], "VF")
        self.assertEqual(by_code(result, "ZZT 150E")["attempts"], {
            "earlier": [{"term": "203110", "grade": "FF"}],
            "later": [{"term": "203130", "grade": "CB", "counted": True}],
        })
        self.assertNotIn("attempts", by_code(result, "ZZT 160E"))
        self.assertEqual(result["attempts_status"], "from_graduation_plan")

    def test_failed_attempt_under_another_code_is_only_a_likely_equivalent(self) -> None:
        app = make_app([reg("ZZT", "170E", "40043")], met_rows=[
            met("ZZT 170E", "CC", 3, dersAdi="Intro to  Widgets"),
        ], unused_rows=[
            unused("OLD 170E", "FF", 3, "203010", dersAdi="INTRO TO WIDGETS"),
            unused("OLD 171E", "FF", 4, "203010", dersAdi="Intro to Widgets"),   # other credit
            unused("OLD 172E", "CC", 3, "203010", dersAdi="Intro to Widgets"),   # not failing
            unused("OLD 173E", "FF", 3, "203010", dersAdi="Intro to Gadgets"),   # other name
        ])
        result = app.obs_calculate_gpa(semester=TERM, include_official=False)
        self.assertEqual(by_code(result, "ZZT 170E")["attempts"], {"likely_equivalent": [
            {"code": "OLD 170E", "term": "203010", "grade": "FF",
             "evidence": ["same_name", "same_credit"], "certain": False}]})

    def test_missing_graduation_payload_is_reported_not_guessed(self) -> None:
        app = make_app([reg("ZZT", "150E", "40041", kredi="3", harfNotu="BB")])
        app.obs.get_graduation_remaining.side_effect = ObsError("down")
        result = app.obs_calculate_gpa(semester=TERM, include_official=False)
        self.assertEqual(result["attempts_status"], "unavailable")
        self.assertEqual(result["gpa"], 3.0)


class AllFailedTermTests(unittest.TestCase):
    """Item 6: a term with only failed courses has a real official average of zero."""

    def test_official_zero_is_found_when_every_course_of_the_term_is_graded(self) -> None:
        app = make_app([reg("ZZT", "180E", "40051"), reg("ZZT", "190E", "40052")],
                       unused_rows=[unused("ZZT 180E", "FF", 3, "203130"), unused("ZZT 190E", "VF", 4, "203130")],
                       status=official(0, 0, term="203130"))
        result = app.obs_calculate_gpa(semester=203130)
        self.assertEqual(result["gpa"], 0.0)
        self.assertEqual(result["total_credits"], 7)
        self.assertEqual([c["code"] for c in result["ff_risk"]], ["ZZT 180E", "ZZT 190E"])
        self.assertEqual(result["official_reference"]["status"], "reported")
        self.assertEqual(result["official_term_gpa"], 0)
        self.assertEqual(result["official_term_credits"], 0)
        self.assertEqual(result["comparison_status"], "match")
        self.assertEqual(result["preferred_term_gpa_source"], "official_obs")

    def test_official_zero_of_an_ungraded_term_is_still_not_an_average(self) -> None:
        app = make_app([reg("ZZT", "180E", "40051", kredi="3")], status=official(0, 0))
        result = app.obs_calculate_gpa(semester=TERM)
        self.assertEqual(result["official_reference"]["status"], "unavailable")
        self.assertIsNone(result["official_term_gpa"])
        self.assertIsNone(official_term_reference(official(0, 0), TERM)["gpa"])
        self.assertEqual(official_term_reference(official(0, 0), TERM, zero_average_is_final=True)["gpa"], 0)


class CreditReconciliationEdgeTests(unittest.TestCase):
    """The official average may only choose between values a plan row carries."""

    REGISTERED = ElectiveSlotCreditTests.REGISTERED

    def test_default_credit_confirmed_by_the_official_average(self) -> None:
        rows = [met("ZZT 301E", "BB", 4), met("ZZT 302E", "CC", 3),
                met("ZZT 455E", "DD", 2, sayilanKredi=3, grupName="Elective", grupId=7001)]
        result = make_app(self.REGISTERED, met_rows=rows, status=official(2.22, 9)).obs_calculate_gpa(TERM)
        self.assertEqual(result["comparison_status"], "match")
        self.assertEqual(by_code(result, "ZZT 455E")["credit"], 2)
        self.assertEqual(result["credit_ambiguity"]["status"], "confirmed_by_official_average")
        self.assertNotIn("mismatch_ambiguous_credit_courses", result)

    def test_two_ambiguous_rows_are_resolved_together_or_not_at_all(self) -> None:
        rows = [met("ZZT 301E", "AA", 4),
                met("ZZT 302E", "CC", 2, sayilanKredi=3, grupName="Elective A", grupId=7001),
                met("ZZT 455E", "DD", 2, sayilanKredi=3, grupName="Elective B", grupId=7002)]
        # Weights (3, 2): (16 + 6 + 2) / 9 = 2.67. No other pair of carried values gives it.
        result = make_app(self.REGISTERED, met_rows=rows, status=official(2.67, 9)).obs_calculate_gpa(TERM)
        self.assertEqual(result["credit_ambiguity"]["status"], "resolved_by_official_average")
        self.assertEqual(by_code(result, "ZZT 302E")["credit"], 3)
        self.assertEqual(by_code(result, "ZZT 455E")["credit"], 2)
        self.assertEqual(result["comparison_status"], "match")

    def test_unfinished_term_is_not_fitted(self) -> None:
        rows = [met("ZZT 301E", "BB", 4),
                met("ZZT 455E", "DD", 2, sayilanKredi=3, grupName="Elective", grupId=7001)]
        result = make_app(self.REGISTERED, met_rows=rows, status=official(2.1, 10)).obs_calculate_gpa(TERM)
        self.assertEqual(result["credit_ambiguity"]["status"], "not_comparable")
        self.assertEqual(by_code(result, "ZZT 455E")["credit"], 2)

    def test_malformed_plan_rows_are_ignored(self) -> None:
        app = make_app([reg("ZZT", "301E", "40031", kredi="3", harfNotu="BB")])
        app.obs.get_graduation_remaining.return_value = {"mezuniyetimeNeKaldiBilgi": {
            "checkMetMezuniyetList": [42, None, {"bransKodu": None}, {"bransKodu": "", "dersAdi": "Slot"}],
            "unusedSinifOgrenciList": "invalid"}}
        result = app.obs_calculate_gpa(semester=TERM, include_official=False)
        self.assertEqual(result["gpa"], 3.0)
        self.assertNotIn("attempts", by_code(result, "ZZT 301E"))


if __name__ == "__main__":
    unittest.main()


class OtherSemesterToolsTests(unittest.TestCase):
    """The term code arrives as a JSON number from some clients."""

    NAMES = (
        "obs_list_registered_courses",
        "obs_get_course_grades",
        "obs_get_attendance",
        "obs_get_schedule",
        "get_personal_exam_calendar",
    )

    def test_semester_accepts_a_number_on_every_obs_tool(self) -> None:
        server = FastMCP("semester-types")
        register_tools(server, make_app([]), list(self.NAMES))
        tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        self.assertEqual(set(tools), set(self.NAMES))
        for name, tool in tools.items():
            with self.subTest(tool=name):
                options = tool.inputSchema["properties"]["semester"].get("anyOf", [])
                self.assertLessEqual({"string", "integer"}, {option.get("type") for option in options})
