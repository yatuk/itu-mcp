"""Cumulative GPA simulation: the pure model in ``gpa_simulation``.

Every course, grade, term and id below is invented. The hand-computed
numbers of the base student:

- 202410: ABC 101E 4 BB, ABC 103E 3 FF, XYZ 105 3 FF, ABC 100 0 BL
  term 12 / 10 = 1.20, cumulative 1.20
- 202420: ABC 103E 3 CC (retake), ABC 102E 3.5 DC, QRS 105 3 CB (XYZ 105
  under a new code), term 18.75 / 9.5 = 1.97,
  cumulative 30.75 / 13.5 = 2.28 (1.86 if XYZ 105 still counted)
- 202510: ELC 201E AA (kredisi 2, sayilanKredi 3), ABC 201E 4 DD,
  ABC 205 2 FF, term 16 / 9 = 1.78 (1.50 with 2 credits),
  cumulative 46.75 / 22.5 = 2.08
"""

from __future__ import annotations

import copy
import unittest
from typing import Any

from ninova_mcp.gpa_simulation import normalize_arguments, simulate_cumulative_gpa

TERM = "202520"


def met(code: str, name: str, credit: Any, grade: str, term: str, **extra: Any) -> dict[str, Any]:
    row = {
        "isMet": True, "bransKodu": code, "dersAdi": name, "harfNotu": grade, "donem": term,
        "kredisi": str(credit), "kredisiDec": credit, "sayilanKredi": credit, "grupName": "",
    }
    row.update(extra)
    return row


def unmet(code: str, name: str, credit: Any) -> dict[str, Any]:
    return {
        "isMet": False, "bransKodu": code, "dersAdi": name, "harfNotu": "", "donem": None,
        "kredisi": str(credit), "kredisiDec": credit, "sayilanKredi": 0, "grupName": "",
    }


def unused(code: str, name: str, credit: Any, grade: str, term: str) -> dict[str, Any]:
    return {
        "bransKodu": code, "dersAdi": name, "kredisi": credit, "harfNotu": grade,
        "donem": term, "isValidAndUnused": False,
    }


def registered_row(branch: str, number: str, crn: str, name_en: str, name_tr: str = "") -> dict[str, Any]:
    return {"bransKodu": branch, "dersKodu": number, "crn": crn, "dersAdiEN": name_en,
            "dersAdiTR": name_tr or name_en, "kredi": None, "harfNotu": None}


def status(*rows: tuple[str, Any, Any]) -> dict[str, Any]:
    return {"statusCode": 0, "kayitDurumuList": [{"kayitDurumuDonemList": [
        {"akademikDonemKodu": term, "donemlikNotOrtalamasi": term_gpa,
         "genelNotOrtalamasi": cumulative, "verilenKredi": 9}
        for term, term_gpa, cumulative in rows
    ]}]}


def base_graduation() -> dict[str, Any]:
    decimal_comma = met("ABC 102E", "Widgets II", 3.5, "DC", "202420", kredisi="3,5")
    del decimal_comma["kredisiDec"], decimal_comma["sayilanKredi"]
    return {"mezuniyetimeNeKaldiBilgi": {
        "gpa": 2.08,
        "dersPlaniVM": {"gerekliMinGPA": 2},
        "checkMetMezuniyetList": [
            met("ABC 101E", "Intro to Widgets", 4, "BB", "202410"),
            met("ABC 100", "Orientation", 0, "BL", "202410"),
            met("ABC 103E", "Widget Lab", 3, "CC", "202420"),
            decimal_comma,
            met("QRS 105", "Gadget Theory", 3, "CB", "202420"),
            met("ELC 201E", "Elective Sprockets", 2, "AA", "202510",
                sayilanKredi=3, grupName="3rd Sem. Elective (TM)"),
            met("ABC 201E", "Widget Systems", 4, "DD", "202510"),
            unmet("NEW 301E", "Advanced Doohickeys", 3),
            unmet("ABC 205", "Widget Ethics", 2),
            {"isMet": False, "bransKodu": "", "dersAdi": "5th Sem. Elective (TM)", "harfNotu": "",
             "donem": None, "kredisi": "3", "kredisiDec": 3, "sayilanKredi": 0, "grupName": "5th Sem. Elective (TM)"},
        ],
        "unusedSinifOgrenciList": [
            unused("ABC 103E", "Widget Lab", 3, "FF", "202410"),
            unused("XYZ 105", "Gadget Theory", 3, "FF", "202410"),
            unused("ABC 205", "Widget Ethics", 2, "FF", "202510"),
        ],
    }}


BASE_STATUS = status(("202410", 1.2, 1.2), ("202420", 1.97, 2.28), ("202510", 1.78, 2.08), (TERM, 0, 0))
BASE_REGISTERED = {"statusCode": 0, "kayitSinifResultList": [
    registered_row("ABC", "205", "31001", "Widget Ethics", "Aygıt Etiği"),
    registered_row("ABC", "201E", "31002", "Widget Systems"),
    registered_row("NEW", "301E", "31003", "Advanced Doohickeys"),
]}


def simulate(graduation: Any = None, official: Any = BASE_STATUS, registered: Any = BASE_REGISTERED,
             **arguments: Any) -> dict[str, Any]:
    return simulate_cumulative_gpa(
        base_graduation() if graduation is None else graduation,
        copy.deepcopy(official), copy.deepcopy(registered), term_code=TERM, **arguments,
    )


def course(result: dict[str, Any], code: str) -> dict[str, Any]:
    return next(row for row in result["courses"] if row["code"] == code)


class ReconstructionTests(unittest.TestCase):
    def test_reproduces_the_official_series_term_by_term(self) -> None:
        current = simulate()["current"]
        self.assertEqual(current["model_status"], "reproduced")
        self.assertEqual((current["terms_checked"], current["terms_matched"]), (3, 3))
        self.assertEqual(current["term_mismatches"], [])
        self.assertTrue(current["reproduces_official"])
        self.assertEqual(current["gpa"], 2.08)
        self.assertEqual((current["credits"], current["points"]), (22.5, 46.75))
        self.assertAlmostEqual(current["gpa_unrounded"], 46.75 / 22.5, places=5)

    def test_result_states_that_it_is_an_estimate(self) -> None:
        result = simulate()
        self.assertIn("not an official result", result["note"])
        self.assertEqual(result["official"], {"cumulative_gpa": 2.08, "required_min_gpa": 2.0})
        self.assertNotIn("untrusted_external_content", result)

    def test_zero_credit_and_ungraded_pass_fail_rows_are_ignored(self) -> None:
        graduation = base_graduation()
        graduation["mezuniyetimeNeKaldiBilgi"]["checkMetMezuniyetList"].append(
            met("ABC 290", "Summer Practice", 5, "BL", "202510"))
        current = simulate(graduation)["current"]
        self.assertEqual((current["credits"], current["points"]), (22.5, 46.75))
        self.assertEqual(current["model_status"], "reproduced")

    def test_decimal_comma_credit_is_read(self) -> None:
        # ABC 102E carries only kredisi "3,5". Without it the total would be 19.
        self.assertEqual(simulate()["current"]["credits"], 22.5)

    def test_mismatch_is_reported_first_and_loudly(self) -> None:
        result = simulate(official=status(("202410", 1.2, 1.2), ("202420", 1.97, 3.1), ("202510", 1.78, 2.08)))
        self.assertEqual(result["current"]["model_status"], "mismatch")
        self.assertEqual(result["current"]["terms_matched"], 2)
        self.assertEqual(result["current"]["term_mismatches"][0]["term"], "202420")
        self.assertTrue(result["warnings"][0].startswith("model_mismatch: THE MODEL DOES NOT REPRODUCE"))

    def test_term_whose_grades_are_not_final_is_not_checked(self) -> None:
        graduation = base_graduation()
        info = graduation["mezuniyetimeNeKaldiBilgi"]
        info["checkMetMezuniyetList"].append(met("ABC 207", "Early Grade", 2, "AA", TERM))
        del info["gpa"]
        official = copy.deepcopy(BASE_STATUS)
        official["kayitDurumuList"][0]["kayitDurumuDonemList"][3]["verilenKredi"] = 0
        current = simulate(graduation, official)["current"]
        self.assertEqual((current["terms_checked"], current["terms_matched"]), (3, 3))
        self.assertEqual(current["model_status"], "reproduced")
        self.assertEqual(current["credits"], 24.5)

    def test_official_zero_after_failing_everything_is_a_real_value(self) -> None:
        graduation = {"mezuniyetimeNeKaldiBilgi": {
            "gpa": 0, "checkMetMezuniyetList": [],
            "unusedSinifOgrenciList": [unused("ABC 101E", "Intro to Widgets", 4, "FF", "202410")],
        }}
        official = status(("202410", 0, 0))
        official["kayitDurumuList"][0]["kayitDurumuDonemList"][0]["verilenKredi"] = 0
        result = simulate(graduation, official, {"kayitSinifResultList": [
            registered_row("ABC", "101E", "40001", "Intro to Widgets")]}, default_grade="CC")
        self.assertEqual(result["official"]["cumulative_gpa"], 0.0)
        self.assertEqual(result["current"]["model_status"], "reproduced")
        self.assertEqual((result["current"]["terms_checked"], result["current"]["gpa"]), (1, 0.0))
        self.assertEqual(result["projected"]["gpa"], 2.0)
        self.assertIsNone(result["projected"]["meets_required_min_gpa"])

    def test_first_term_student_has_nothing_to_validate(self) -> None:
        graduation = {"mezuniyetimeNeKaldiBilgi": {
            "gpa": 0, "dersPlaniVM": {"gerekliMinGPA": 2},
            "checkMetMezuniyetList": [unmet("ABC 101E", "Intro to Widgets", 4), unmet("ABC 103E", "Widget Lab", 3)],
            "unusedSinifOgrenciList": [],
        }}
        registered = {"kayitSinifResultList": [
            registered_row("ABC", "101E", "40001", "Intro to Widgets"),
            registered_row("ABC", "103E", "40002", "Widget Lab"),
        ]}
        result = simulate(graduation, status((TERM, 0, 0)), registered,
                          projected_grades={"ABC 101E": "AA"}, default_grade="CC")
        self.assertIsNone(result["official"]["cumulative_gpa"])
        self.assertEqual(result["current"]["model_status"], "not_validated")
        self.assertIsNone(result["current"]["gpa"])
        self.assertIsNone(result["current"]["reproduces_official"])
        self.assertEqual(result["current"]["terms_checked"], 0)
        self.assertEqual(result["projected"]["gpa"], 3.14)  # (16 + 6) / 7
        self.assertEqual(result["projected"]["term_gpa"], 3.14)
        self.assertEqual({row["effect"] for row in result["courses"]}, {"new"})
        self.assertEqual(result["warnings"], [])
        self.assertTrue(result["projected"]["meets_required_min_gpa"])


class ProjectionTests(unittest.TestCase):
    def test_bb_in_everything(self) -> None:
        result = simulate(default_grade="BB")
        projected = result["projected"]
        # +6 (FF to BB, 2 cr), +8 (DD to BB, 4 cr), +9 new on 3 new credits.
        self.assertEqual((projected["credits"], projected["points"]), (25.5, 69.75))
        self.assertEqual(projected["gpa"], 2.74)
        self.assertEqual((projected["term_gpa"], projected["term_credits"], projected["term_points"]), (3.0, 9.0, 27.0))
        self.assertTrue(projected["complete"])
        self.assertTrue(projected["meets_required_min_gpa"])
        self.assertEqual(result["not_projected"], [])
        self.assertEqual(result["still_failing"], [])

    def test_retake_of_a_failed_course_replaces_it(self) -> None:
        row = course(simulate(default_grade="BB"), "ABC 205")
        self.assertEqual(row["effect"], "replaces ABC 205 FF from 202510")
        self.assertEqual((row["credit"], row["credit_delta"], row["points_delta"]), (2.0, 0.0, 6.0))

    def test_retake_of_a_passed_course_replaces_it(self) -> None:
        row = course(simulate(default_grade="BB"), "ABC 201E")
        self.assertEqual(row["effect"], "replaces ABC 201E DD from 202510")
        self.assertEqual((row["credit_delta"], row["points_delta"]), (0.0, 8.0))

    def test_new_course_adds_credit_from_the_plan_row(self) -> None:
        row = course(simulate(default_grade="BB"), "NEW 301E")
        self.assertEqual((row["effect"], row["credit"], row["credit_delta"], row["points_delta"]),
                         ("new", 3.0, 3.0, 9.0))
        self.assertEqual(row["crn"], "31003")

    def test_code_and_crn_keys_and_courses_without_a_grade(self) -> None:
        result = simulate(projected_grades={"abc205": "AA", "31003": "FF"})
        self.assertEqual(course(result, "ABC 205")["grade"], "AA")
        self.assertEqual(course(result, "NEW 301E")["grade"], "FF")
        self.assertEqual([row["code"] for row in result["not_projected"]], ["ABC 201E"])
        self.assertEqual((result["projected"]["credits"], result["projected"]["points"]), (25.5, 54.75))
        self.assertTrue(result["projected"]["complete"])

    def test_projected_grades_win_over_default_grade(self) -> None:
        result = simulate(projected_grades={"NEW 301E": "AA"}, default_grade="CC")
        self.assertEqual(course(result, "NEW 301E")["grade"], "AA")
        self.assertEqual(course(result, "ABC 205")["grade"], "CC")

    def test_courses_still_failing_outside_the_projection_are_listed(self) -> None:
        result = simulate(projected_grades={"NEW 301E": "BB"})
        self.assertEqual(result["still_failing"], [
            {"code": "ABC 205", "name": "Widget Ethics", "credit": 2.0, "term": "202510", "grade": "FF"},
        ])

    def test_no_grades_given_leaves_the_gpa_unchanged_and_says_so(self) -> None:
        result = simulate()
        self.assertEqual(result["projected"]["gpa"], result["current"]["gpa"])
        self.assertEqual(len(result["not_projected"]), 3)
        self.assertTrue(any(w.startswith("no_grades_given") for w in result["warnings"]))

    def test_projection_below_the_required_minimum(self) -> None:
        result = simulate(default_grade="FF")
        self.assertEqual(result["projected"]["gpa"], 1.68)  # (46.75 - 4) / 25.5
        self.assertFalse(result["projected"]["meets_required_min_gpa"])

    def test_grade_without_coefficient_changes_nothing(self) -> None:
        result = simulate(projected_grades={"ABC 205": "BL"})
        row = course(result, "ABC 205")
        self.assertIn("not counted", row["effect"])
        self.assertEqual(result["projected"]["points"], 46.75)

    def test_unresolved_credit_makes_the_projection_incomplete(self) -> None:
        registered = copy.deepcopy(BASE_REGISTERED)
        registered["kayitSinifResultList"].append(registered_row("MYS", "999", "31004", "Mystery Course"))
        result = simulate(registered=registered, default_grade="BB")
        self.assertEqual([row["code"] for row in result["unresolved_credit"]], ["MYS 999"])
        self.assertFalse(result["projected"]["complete"])
        self.assertEqual(result["projected"]["credits"], 25.5)  # not guessed, left out
        self.assertNotIn("MYS 999", [row["code"] for row in result["courses"]])
        self.assertTrue(any(w.startswith("unresolved_credit") and "MYS 999" in w for w in result["warnings"]))

    def test_extra_courses_entry_supplies_the_credit_of_a_registered_course(self) -> None:
        registered = copy.deepcopy(BASE_REGISTERED)
        registered["kayitSinifResultList"].append(registered_row("MYS", "999", "31004", "Mystery Course"))
        result = simulate(registered=registered, default_grade="BB",
                          extra_courses=[{"code": "MYS 999", "grade": "AA", "credit": 2}])
        row = course(result, "MYS 999")
        self.assertEqual((row["grade"], row["credit"], row["effect"]), ("AA", 2.0, "new"))
        self.assertEqual(result["unresolved_credit"], [])
        self.assertNotIn("extra_courses", result)

    def test_unmatched_keys_are_reported_with_the_valid_ones(self) -> None:
        result = simulate(projected_grades={"NOP 123": "AA", "99999": "BB", "ABC 205": "CC"})
        unmatched = result["unmatched_projected_grades"]
        self.assertEqual(unmatched["keys"], ["NOP 123", "99999"])
        self.assertEqual([item["code"] for item in unmatched["valid_keys"]], ["ABC 205", "ABC 201E", "NEW 301E"])
        self.assertEqual(unmatched["valid_keys"][0]["crn"], "31001")
        self.assertFalse(result["projected"]["complete"])
        warning = next(w for w in result["warnings"] if w.startswith("unmatched_projected_grades"))
        self.assertIn("NOP 123", warning)
        self.assertIn("ABC 205 (CRN 31001)", warning)

    def test_extra_courses(self) -> None:
        result = simulate(extra_courses=[
            {"code": "FUT 400", "grade": "AA", "credit": 3},
            {"code": "ABC 101E", "grade": "AA"},
            {"code": "UNK 100", "grade": "BB"},
        ])
        extras = {row["code"]: row for row in result["extra_courses"]}
        self.assertEqual((extras["FUT 400"]["effect"], extras["FUT 400"]["credit_delta"],
                          extras["FUT 400"]["points_delta"]), ("new", 3.0, 12.0))
        self.assertEqual(extras["ABC 101E"]["effect"], "replaces ABC 101E BB from 202410")
        self.assertEqual((extras["ABC 101E"]["credit_delta"], extras["ABC 101E"]["points_delta"]), (0.0, 4.0))
        self.assertEqual([row["code"] for row in result["unresolved_credit"]], ["UNK 100"])
        self.assertEqual((result["projected"]["credits"], result["projected"]["points"]), (25.5, 62.75))
        self.assertEqual(result["projected"]["term_credits"], 0.0)  # extras are not part of the term

    def test_extra_course_can_name_the_course_it_replaces(self) -> None:
        result = simulate(extra_courses=[{"code": "ZZZ 205E", "grade": "CC", "replaces": "ABC 205"}])
        row = result["extra_courses"][0]
        self.assertEqual(row["effect"], "replaces ABC 205 FF from 202510")
        self.assertEqual((row["credit"], row["credit_delta"], row["points_delta"]), (2.0, 0.0, 4.0))
        self.assertEqual(result["equivalences"][-1]["basis"], "stated_by_caller")
        self.assertEqual(result["still_failing"], [])

    def test_projecting_a_term_that_already_has_grades_overrides_them(self) -> None:
        result = simulate_cumulative_gpa(
            base_graduation(), BASE_STATUS,
            {"kayitSinifResultList": [registered_row("ABC", "201E", "20001", "Widget Systems")]},
            term_code="202510", default_grade="AA",
        )
        row = result["courses"][0]
        self.assertEqual(row["effect"], "replaces ABC 201E DD from 202510")
        self.assertEqual(row["points_delta"], 12.0)

    def test_grade_of_an_earlier_term_is_superseded_by_a_later_attempt(self) -> None:
        result = simulate_cumulative_gpa(
            base_graduation(), BASE_STATUS,
            {"kayitSinifResultList": [registered_row("ABC", "103E", "10001", "Widget Lab")]},
            term_code="202410", default_grade="AA",
        )
        row = result["courses"][0]
        self.assertEqual(row["effect"], "superseded by ABC 103E CC from 202420")
        self.assertEqual(result["projected"]["points"], 46.75)


class EquivalenceTests(unittest.TestCase):
    def test_other_code_is_accepted_when_the_series_needs_it(self) -> None:
        result = simulate()
        self.assertEqual(len(result["equivalences"]), 1)
        item = result["equivalences"][0]
        self.assertEqual((item["old_code"], item["old_grade"], item["old_term"]), ("XYZ 105", "FF", "202410"))
        self.assertEqual((item["new_code"], item["new_term"], item["credit"]), ("QRS 105", "202420", 3.0))
        self.assertTrue(item["verified_by_official_series"])
        self.assertIn("reproduced only when", item["evidence"])
        self.assertNotIn("equivalence_candidates_not_applied", result)
        self.assertEqual(result["still_failing"][0]["code"], "ABC 205")
        self.assertEqual(len(result["still_failing"]), 1)

    def test_other_code_is_rejected_when_the_series_matches_without_it(self) -> None:
        graduation = base_graduation()
        graduation["mezuniyetimeNeKaldiBilgi"]["gpa"] = 1.83
        result = simulate(graduation, status(("202410", 1.2, 1.2), ("202420", 1.97, 1.86), ("202510", 1.78, 1.83)))
        self.assertEqual(result["equivalences"], [])
        rejected = result["equivalence_candidates_not_applied"]
        self.assertEqual((rejected[0]["old_code"], rejected[0]["new_code"]), ("XYZ 105", "QRS 105"))
        self.assertEqual(rejected[0]["reason"], "the official record is reproduced without it")
        self.assertEqual(result["current"]["model_status"], "reproduced")
        self.assertEqual((result["current"]["credits"], result["current"]["gpa"]), (25.5, 1.83))
        self.assertEqual([row["code"] for row in result["still_failing"]], ["XYZ 105", "ABC 205"])

    def test_names_are_compared_without_case_punctuation_or_spacing(self) -> None:
        graduation = base_graduation()
        graduation["mezuniyetimeNeKaldiBilgi"]["unusedSinifOgrenciList"][1]["dersAdi"] = "  GADGET   theory. "
        self.assertEqual(len(simulate(graduation)["equivalences"]), 1)

    def test_same_name_with_another_credit_is_not_a_candidate(self) -> None:
        graduation = base_graduation()
        graduation["mezuniyetimeNeKaldiBilgi"]["unusedSinifOgrenciList"][1]["kredisi"] = 4
        result = simulate(graduation)
        self.assertEqual(result["equivalences"], [])
        self.assertNotIn("equivalence_candidates_not_applied", result)
        self.assertEqual(result["current"]["model_status"], "mismatch")

    def test_candidate_is_not_applied_without_anything_to_verify_it(self) -> None:
        graduation = base_graduation()
        del graduation["mezuniyetimeNeKaldiBilgi"]["gpa"]
        result = simulate(graduation, official={})
        self.assertEqual(result["current"]["model_status"], "not_validated")
        self.assertEqual(result["equivalences"], [])
        self.assertEqual(result["equivalence_candidates_not_applied"][0]["reason"],
                         "no official value to verify it against")
        self.assertTrue(any(w.startswith("not_validated") for w in result["warnings"]))

    def test_registered_course_replaces_a_failed_one_under_another_code(self) -> None:
        graduation = base_graduation()
        info = graduation["mezuniyetimeNeKaldiBilgi"]
        info["checkMetMezuniyetList"].append(unmet("NWW 210E", "Thingamajig Design", 3))
        info["unusedSinifOgrenciList"].append(unused("OLD 210", "Thingamajig Design", 3, "VF", "202510"))
        info["gpa"] = 1.83  # 46.75 / 25.5
        official = status(("202410", 1.2, 1.2), ("202420", 1.97, 2.28), ("202510", 1.33, 1.83))
        registered = {"kayitSinifResultList": [
            # The registration endpoint has the Turkish name first; the plan row is English.
            registered_row("NWW", "210E", "32001", "Thingamajig  design", "Zımbırtı Tasarımı"),
        ]}
        result = simulate(graduation, official, registered, default_grade="CC")
        self.assertEqual(result["current"]["model_status"], "reproduced")
        row = result["courses"][0]
        self.assertEqual(row["effect"], "replaces OLD 210 VF from 202510")
        self.assertEqual((row["credit_delta"], row["points_delta"]), (0.0, 6.0))
        unverified = result["equivalences"][-1]
        self.assertEqual((unverified["old_code"], unverified["new_code"]), ("OLD 210", "NWW 210E"))
        self.assertEqual(unverified["basis"], "name_and_credit")
        self.assertFalse(unverified["verified_by_official_series"])
        self.assertEqual([item["code"] for item in result["still_failing"]], ["ABC 205"])


class CreditChoiceTests(unittest.TestCase):
    def test_disagreeing_credits_are_decided_by_the_official_term_average(self) -> None:
        choice = simulate()["credit_choices"][0]
        self.assertEqual((choice["code"], choice["term"]), ("ELC 201E", "202510"))
        self.assertEqual((choice["kredisi"], choice["sayilanKredi"], choice["chosen"]), (2.0, 3.0, 3.0))
        self.assertTrue(choice["decided"])
        self.assertIn("1.78", choice["basis"])

    def test_the_other_credit_is_chosen_when_the_term_average_says_so(self) -> None:
        graduation = base_graduation()
        graduation["mezuniyetimeNeKaldiBilgi"]["gpa"] = 1.99  # 42.75 / 21.5
        result = simulate(graduation, status(("202410", 1.2, 1.2), ("202420", 1.97, 2.28), ("202510", 1.5, 1.99)))
        self.assertEqual(result["credit_choices"][0]["chosen"], 2.0)
        self.assertEqual(result["current"]["model_status"], "reproduced")
        self.assertEqual(result["current"]["credits"], 21.5)

    def test_undecidable_credit_gives_a_range(self) -> None:
        official = copy.deepcopy(BASE_STATUS)
        official["kayitDurumuList"][0]["kayitDurumuDonemList"][2]["donemlikNotOrtalamasi"] = None
        result = simulate(official=official, default_grade="BB")
        choice = result["credit_choices"][0]
        self.assertFalse(choice["decided"])
        self.assertEqual(choice["chosen"], 2.0)
        self.assertEqual(result["current"]["gpa_range"], [1.99, 2.08])
        self.assertEqual(result["projected"]["gpa_range"], [2.68, 2.74])  # 65.75 / 24.5 and 69.75 / 25.5
        self.assertEqual(result["projected"]["gpa"], 2.68)
        self.assertTrue(any(w.startswith("ambiguous_credit") and "ELC 201E" in w for w in result["warnings"]))
        self.assertTrue(result["projected"]["meets_required_min_gpa"])

    def test_rows_with_one_credit_value_are_never_candidates(self) -> None:
        graduation = base_graduation()
        graduation["mezuniyetimeNeKaldiBilgi"]["checkMetMezuniyetList"][5]["sayilanKredi"] = 2
        result = simulate(graduation)
        self.assertNotIn("credit_choices", result)
        self.assertEqual(result["current"]["model_status"], "mismatch")


class ArgumentTests(unittest.TestCase):
    def test_unknown_grade_names_the_valid_ones(self) -> None:
        for arguments in (
            {"default_grade": "B+"},
            {"projected_grades": {"ABC 205": "ZZ"}},
            {"extra_courses": [{"code": "FUT 400", "grade": "", "credit": 3}]},
            {"default_grade": 3},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(ValueError, r"Unknown letter grade .*Valid grades: AA, BA\+, BA, .*VF"):
                    simulate(**arguments)

    def test_grades_are_normalized(self) -> None:
        self.assertEqual(normalize_arguments({" abc 205 ": " bb+ "}, "cc", None)["grades"], {"abc 205": "BB+"})

    def test_same_course_with_two_grades_is_an_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "ABC 205 two different grades"):
            simulate(projected_grades={"ABC 205": "AA", "31001": "BB"})
        with self.assertRaisesRegex(ValueError, "different grades in projected_grades and extra_courses"):
            simulate(projected_grades={"ABC 205": "AA"}, extra_courses=[{"code": "ABC 205", "grade": "BB"}])

    def test_malformed_arguments_are_errors(self) -> None:
        for arguments, message in (
            ({"projected_grades": ["BB"]}, "projected_grades must be an object"),
            ({"extra_courses": {"code": "FUT 400"}}, "extra_courses must be a list"),
            ({"extra_courses": ["FUT 400"]}, "must be an object"),
            ({"extra_courses": [{"grade": "BB"}]}, "needs a course code"),
            ({"extra_courses": [{"code": "FUT 400", "grade": "BB", "credit": -1}]}, "nonnegative number"),
            ({"extra_courses": [{"code": "FUT 400", "grade": "BB", "credit": True}]}, "nonnegative number"),
            ({"extra_courses": [{"code": "FUT 400", "grade": "BB", "ects": 5}]}, r"unknown field\(s\): ects"),
            ({"extra_courses": [{"code": "FUT 400", "grade": "BB", "replaces": ""}]}, "replaces must be"),
            ({"extra_courses": [{"code": "FUT 400", "grade": "BB"}, {"code": "fut400", "grade": "AA"}]},
             "more than once"),
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(ValueError, message):
                    simulate(**arguments)

    def test_unusable_payloads_do_not_raise(self) -> None:
        result = simulate_cumulative_gpa(None, None, None, default_grade="BB")
        self.assertIsNone(result["projected"]["gpa"])
        self.assertTrue(any(w.startswith("no_registered_courses") for w in result["warnings"]))

    def test_tool_result_shape_of_graduation_payload_is_accepted(self) -> None:
        result = simulate({"graduation": base_graduation()})
        self.assertEqual(result["current"]["gpa"], 2.08)


if __name__ == "__main__":
    unittest.main()
