"""Characterization of the GPA code: what it returns today, pinned before a refactor.

Each case calls a public function with invented data and its outcome (the
returned value with its key order, or the error type and message) is compared
with ``fixtures/gpa_characterization.json``. The snapshot was written from the
code as it stood before the refactor:

    PYTHONPATH=src python tests/test_gpa_characterization.py --write

Rewrite it only for an intended behaviour change. Every course code, CRN,
term and grade below is invented.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock

from ninova_mcp.client import NinovaError
from ninova_mcp.gpa import (
    attempt_notes,
    calculate_gpa,
    calculate_target_gpa,
    compact_term_result,
    plan_credit_table,
    plan_rows,
    reconcile_credit_candidates,
    resolve_projection_keys,
    validate_projected_grades,
)
from ninova_mcp.gpa_reference import attach_official_reference, official_term_reference
from ninova_mcp.gpa_simulation import normalize_arguments
from ninova_mcp.obs_client import ObsClient, ObsError
from ninova_mcp.server import NinovaMcpApp

SNAPSHOT = Path(__file__).parent / "fixtures" / "gpa_characterization.json"
TERM = "203120"
NAN, INF = float("nan"), float("inf")
SEMESTERS = {"ogrenciDonemListesi": [
    {"akademikDonemId": 11, "donemKodu": "203110", "akademikDonemAdi": "2030-2031 Güz Dönemi"},
    {"akademikDonemId": 12, "donemKodu": "203120", "akademikDonemAdi": "2030-2031 Bahar Dönemi"},
    {"akademikDonemId": 13, "donemKodu": "203130", "akademikDonemAdi": "2030-2031 Yaz Dönemi"},
]}

CASES: dict[str, Callable[[], Any]] = {}


def many(prefix: str, calls: dict[str, Callable[[], Any]]) -> None:
    for name, function in calls.items():
        if f"{prefix}/{name}" in CASES:
            raise AssertionError(f"duplicate case {prefix}/{name}")
        CASES[f"{prefix}/{name}"] = function


def outcome(function: Callable[[], Any]) -> str:
    """The returned value or the raised error, as text that keeps key order."""
    try:
        value = function()
    except (ValueError, NinovaError) as exc:
        value = {"raises": type(exc).__name__, "message": str(exc)}
    return json.dumps(value, ensure_ascii=False, indent=1)


# --- calculate_gpa ----------------------------------------------------------

def course(code: str, credit: Any, grade: Any, **extra: Any) -> dict[str, Any]:
    return {"code": code, "credit": credit, "grade": grade, **extra}


EVIDENCE = {
    "recorded_credit": None, "plan_credit": 3, "counted_credit": 4, "credit_source": "degree_plan",
    "grade_source": "registration", "credit_candidates": [3, 4], "credit_choice": "matches_official_average",
    "crn": "40001", "attempts": {"earlier": [{"term": "203010", "grade": "FF"}]}, "not_copied": "x",
}

many("calculate_gpa", {
    "no_courses": lambda: calculate_gpa([]),
    "counted_courses": lambda: calculate_gpa([
        course("ZZT 101E", 3, "AA", name="First"), course("ZZT 102E", "2,5", "CB"), course("ZZT 103", "4", "BA+"),
    ]),
    "obs_field_names": lambda: calculate_gpa([
        {"dersKodu": "ZZT 104E", "dersAdiTR": "Dördüncü", "kredi": "3", "harfNotu": "BB"},
        {"code": "ZZT 105E", "credit": None, "kredi": 2, "grade": "", "harfNotu": "CC"},
        {"code": "ZZT 106E", "credit": 0, "kredi": 5, "grade": None, "harfNotu": "DD"},
        {"credit": 1, "grade": "AA"},
    ]),
    "grade_spelling": lambda: calculate_gpa([
        course("ZZT 107E", 3, " bb "), course("ZZT 108E", 3, "ba+"), course("ZZT 109E", 3, "   "),
        course("ZZT 110E", 3, 5), course("ZZT 111E", 3, "XY"), course("ZZT 112E", 3, False),
        course("ZZT 113E", 3, 0),
    ]),
    "ungraded_credit_status": lambda: calculate_gpa([
        course("ZZT 114E", 3, None, crn="40002", plan_credit=3), course("ZZT 115E", None, None),
        course("ZZT 116E", "", None), course("ZZT 117E", "  ", ""), course("ZZT 118E", "abc", None),
        course("ZZT 119E", -1, None), course("ZZT 120E", True, None),
        {"dersKodu": "ZZT 121E", "dersAdiTR": "Yirmi bir", "kredi": "2"},
    ]),
    "excluded_grades": lambda: calculate_gpa([
        course("ZZT 122E", 0, "BL"), course("ZZT 123E", None, "BZ"), course("ZZT 124E", 3, "T"),
        course("ZZT 125E", "bad", "E"), course("ZZT 126E", 3, "KF"), course("ZZT 127E", 3, "IA"),
        course("ZZT 128E", 3, "MU"), course("ZZT 129E", 3, "GE"), course("ZZT 130E", 3, "NA"),
    ]),
    "only_excluded_is_complete_without_gpa": lambda: calculate_gpa([course("ZZT 131E", 0, "BL")]),
    "zero_credit_only": lambda: calculate_gpa([course("ZZT 132E", 0, "AA")]),
    "invalid_credits": lambda: calculate_gpa([
        course("ZZT 133E", None, "FF"), course("ZZT 134E", "", "VF"), course("ZZT 135E", "bad", "BB"),
        course("ZZT 136E", -2, "FF"), course("ZZT 137E", True, "AA"), course("ZZT 138E", NAN, "CC"),
        course("ZZT 139E", INF, "VF"), course("ZZT 140E", "  ", "DD"), course("ZZT 141E", 3, "AA"),
    ]),
    "credit_overflow": lambda: calculate_gpa([
        course("ZZT 142E", 1e308, "AA"), course("ZZT 143E", 1e308, "FF"), course("ZZT 144E", 1e308, "FF"),
        course("ZZT 145E", 3, "BB"),
    ]),
    "failing_and_low_grades": lambda: calculate_gpa([
        course(f"ZZT {200 + index}E", 3, grade)
        for index, grade in enumerate(("FF", "VF", "FD", "DD", "DD+", "DC", "DC+", "CC", "AA"))
    ]),
    "evidence_fields_are_copied": lambda: calculate_gpa([
        course("ZZT 146E", 3, "FF", **EVIDENCE), course("ZZT 147E", 3, "DD", **EVIDENCE),
        course("ZZT 148E", 3, "AA", crn=40003, attempts={"later": [{"term": "203130", "grade": NAN}]}),
    ]),
    "nonfinite_values_are_removed": lambda: calculate_gpa([
        course("ZZT 149E", 3, "AA", recorded_credit=NAN, plan_credit=INF, counted_credit=-INF),
        course("ZZT 150E", INF, None, extra={"values": [NAN, (INF, 1)]}),
    ]),
    "projection_overrides_recorded_grade": lambda: calculate_gpa(
        [course("ZZT 151E", 3, "FF"), course("ZZT 152E", 3, None), course("ZZT 153E", 3, "CC")],
        projected_grades={"zzt151e": "aa", "ZZT 152E": "BB"},
    ),
    "projection_unmatched_key": lambda: calculate_gpa(
        [course("ZZT 154E", 3, "BB")], projected_grades={"QQQ 999": "AA", "40011": "BB"},
    ),
    "projection_to_excluded_grade": lambda: calculate_gpa(
        [course("ZZT 155E", 3, "BB"), course("ZZT 156E", 3, None)], projected_grades={"ZZT 155E": "BL"},
    ),
    "projection_same_course_twice_same_grade": lambda: calculate_gpa(
        [course("ZZT 157E", 3, None)], projected_grades={"ZZT 157E": "AA", "zzt157e": "aa"},
    ),
    "projection_same_course_twice_conflict": lambda: calculate_gpa(
        [course("ZZT 157E", 3, None)], projected_grades={"ZZT 157E": "AA", "zzt157e": "BB"},
    ),
    "projection_empty_mapping": lambda: calculate_gpa([course("ZZT 158E", 3, "BB")], projected_grades={}),
    "projection_display_label_course": lambda: calculate_gpa(
        [course("seminar  one", 2, None)], projected_grades={"Seminar One": "CB"},
    ),
    "projection_with_missing_credit": lambda: calculate_gpa(
        [course("ZZT 159E", None, None)], projected_grades={"ZZT 159E": "FF"},
    ),
    "projection_not_an_object": lambda: calculate_gpa([], projected_grades=["AA"]),  # type: ignore[arg-type]
    "projection_unknown_grade": lambda: calculate_gpa([], projected_grades={"ZZT 160E": "ZZ"}),
    "projection_grade_not_text": lambda: calculate_gpa([], projected_grades={"ZZT 160E": 4}),  # type: ignore[dict-item]
    "projection_blank_key": lambda: calculate_gpa([], projected_grades={"  ": "AA"}),
})

many("validate_projected_grades", {
    "none": lambda: validate_projected_grades(None),
    "normalized": lambda: validate_projected_grades({"zzt 101e": " bb ", 40011: "BL", "ZZT 101E": "aa"}),
    "not_an_object": lambda: validate_projected_grades("AA"),
    "unknown_grade": lambda: validate_projected_grades({40011: "A+"}),
    "empty_grade": lambda: validate_projected_grades({"ZZT 101E": ""}),
    "none_key": lambda: validate_projected_grades({None: "AA"}),
})

# --- calculate_target_gpa ---------------------------------------------------

def target(**overrides: Any) -> Any:
    arguments: dict[str, Any] = {"current_gpa": 2.5, "current_credits": 60, "target_gpa": 3.0,
                                 "future_credits": 30}
    arguments.update(overrides)
    return calculate_target_gpa(**arguments)


many("calculate_target_gpa", {
    "plain": lambda: target(),
    "numbers_as_text": lambda: target(current_gpa="2.5", current_credits="60", target_gpa="3", future_credits="30"),
    "already_above": lambda: target(current_gpa=3.5, target_gpa=2.0),
    "infeasible": lambda: target(current_gpa=1.0, target_gpa=3.9, future_credits=3),
    "no_current_credits": lambda: target(current_gpa=0, current_credits=0),
    "bool": lambda: target(current_gpa=True),
    "not_a_number": lambda: target(current_credits="many"),
    "none": lambda: target(target_gpa=None),
    "nan": lambda: target(future_credits=NAN),
    "inf": lambda: target(current_credits=INF),
    "first_bad_argument_is_named": lambda: target(current_gpa="x", current_credits="y", future_credits=0),
    "current_gpa_range": lambda: target(current_gpa=4.01),
    "target_gpa_range": lambda: target(target_gpa=-0.1),
    "range_is_checked_before_credits": lambda: target(current_gpa=5, target_gpa=5, current_credits=-1,
                                                      future_credits=0),
    "negative_current_credits": lambda: target(current_credits=-1, future_credits=0),
    "zero_future_credits": lambda: target(future_credits=0, retakes="bad"),
    "overflow": lambda: target(current_credits=1e308, future_credits=1e308, current_gpa=4, target_gpa=4),
    "retakes": lambda: target(retakes=[{"credit": 3, "old_grade": "ff"}]),
    "retakes_two": lambda: target(retakes=[{"credit": 3, "old_grade": "FF"}, {"credit": 3.5, "old_grade": " dd "}]),
    "retakes_tuple": lambda: target(retakes=({"credit": 4, "old_grade": "DC"},)),
    "retakes_empty": lambda: target(retakes=[]),
    "retakes_not_a_list": lambda: target(retakes={"credit": 3, "old_grade": "FF"}),
    "retakes_item_not_an_object": lambda: target(retakes=[3]),
    "retakes_unknown_field": lambda: target(retakes=[{"credit": 3, "old_grade": "FF", "code": "X", "term": 1}]),
    "retakes_credit_text": lambda: target(retakes=[{"credit": "3", "old_grade": "FF"}]),
    "retakes_credit_zero": lambda: target(retakes=[{"credit": 0, "old_grade": "FF"}]),
    "retakes_credit_missing": lambda: target(retakes=[{"old_grade": "FF"}]),
    "retakes_second_item_is_named": lambda: target(retakes=[{"credit": 3, "old_grade": "FF"},
                                                            {"credit": -1, "old_grade": "FF"}]),
    "retakes_grade_uncounted": lambda: target(retakes=[{"credit": 3, "old_grade": "BL"}]),
    "retakes_grade_missing": lambda: target(retakes=[{"credit": 3}]),
    "retakes_grade_not_text": lambda: target(retakes=[{"credit": 3, "old_grade": 4}]),
    "retakes_exceed_future": lambda: target(current_credits=3, future_credits=3,
                                            retakes=[{"credit": 4, "old_grade": "AA"}]),
    "retakes_exceed_current": lambda: target(current_credits=3, retakes=[{"credit": 4, "old_grade": "AA"}]),
    "retakes_exceed_points": lambda: target(current_gpa=1.0, current_credits=4, future_credits=6,
                                            retakes=[{"credit": 4, "old_grade": "BB"}]),
    "retakes_points_within_rounding": lambda: target(current_gpa=2.0, current_credits=3, future_credits=6,
                                                     retakes=[{"credit": 3, "old_grade": "CC"}]),
    "retakes_all_of_the_record": lambda: target(current_gpa=0, current_credits=3, future_credits=3,
                                                retakes=[{"credit": 3, "old_grade": "FF"}]),
})

# --- official term reference ------------------------------------------------

def status(gpa: Any = 3.5, credits: Any = 10, term: Any = TERM, **program: Any) -> dict[str, Any]:
    return {"statusCode": 0, "kayitDurumuList": [{"akademikProgramAdi": "Sample Program", **program,
            "kayitDurumuDonemList": [
                {"akademikDonemKodu": "203010", "verilenKredi": 9, "donemlikNotOrtalamasi": 3.1},
                {"akademikDonemKodu": term, "verilenKredi": credits, "donemlikNotOrtalamasi": gpa},
            ]}]}


def with_terms(*terms: Any) -> dict[str, Any]:
    return {"statusCode": 0, "kayitDurumuList": [{"akademikBolumAdi": "Sample Department",
                                                  "kayitDurumuDonemList": list(terms)}]}


PROGRAM = status()["kayitDurumuList"][0]

many("official_term_reference", {
    "reported": lambda: official_term_reference(status(), TERM),
    "term_code_as_number": lambda: official_term_reference(status(term=203120), 203120),  # type: ignore[arg-type]
    "program_name_falls_back_to_department": lambda: official_term_reference(
        with_terms({"akademikDonemKodu": TERM, "donemlikNotOrtalamasi": "3,25", "verilenKredi": "12,5"}), TERM),
    "program_without_a_name": lambda: official_term_reference(
        {"statusCode": 0, "kayitDurumuList": [{"kayitDurumuDonemList": [
            {"akademikDonemKodu": TERM, "donemlikNotOrtalamasi": 2}]}]}, TERM),
    "payload_not_an_object": lambda: official_term_reference([], TERM),  # type: ignore[arg-type]
    "payload_empty": lambda: official_term_reference({}, TERM),
    "status_code_failed": lambda: official_term_reference({**status(), "statusCode": 1}, TERM),
    "status_code_text": lambda: official_term_reference({**status(), "statusCode": "0"}, TERM),
    "status_code_bool": lambda: official_term_reference({**status(), "statusCode": False}, TERM),
    "programs_missing": lambda: official_term_reference({"statusCode": 0}, TERM),
    "programs_not_a_list": lambda: official_term_reference({"statusCode": 0, "kayitDurumuList": "x"}, TERM),
    "programs_with_a_non_object": lambda: official_term_reference(
        {"statusCode": 0, "kayitDurumuList": [PROGRAM, 42]}, TERM),
    "programs_empty": lambda: official_term_reference({"statusCode": 0, "kayitDurumuList": []}, TERM),
    "two_programs": lambda: official_term_reference({"statusCode": 0, "kayitDurumuList": [PROGRAM, PROGRAM]}, TERM),
    "two_programs_without_term": lambda: official_term_reference(
        {"statusCode": 0, "kayitDurumuList": [PROGRAM, PROGRAM]}, None),
    "no_term_code": lambda: official_term_reference(status(), None),
    "empty_term_code": lambda: official_term_reference(status(), ""),
    "no_term_code_skips_term_list_check": lambda: official_term_reference(
        {"statusCode": 0, "kayitDurumuList": [{"kayitDurumuDonemList": 42}]}, None),
    "terms_not_a_list": lambda: official_term_reference(
        {"statusCode": 0, "kayitDurumuList": [{"kayitDurumuDonemList": 42}]}, TERM),
    "terms_missing": lambda: official_term_reference({"statusCode": 0, "kayitDurumuList": [{}]}, TERM),
    "terms_with_a_non_object": lambda: official_term_reference(with_terms({"akademikDonemKodu": TERM}, 42), TERM),
    "term_not_listed": lambda: official_term_reference(status(term="203110"), TERM),
    "term_listed_twice": lambda: official_term_reference(with_terms(
        {"akademikDonemKodu": TERM, "donemlikNotOrtalamasi": 3.0, "verilenKredi": 9},
        {"akademikDonemKodu": int(TERM), "donemlikNotOrtalamasi": "bad"}), TERM),
    "average_missing": lambda: official_term_reference(with_terms({"akademikDonemKodu": TERM}), TERM),
    "average_not_a_number": lambda: official_term_reference(status(gpa="NaN"), TERM),
    "average_bool": lambda: official_term_reference(status(gpa=True), TERM),
    "average_above_four": lambda: official_term_reference(status(gpa=4.01), TERM),
    "average_negative": lambda: official_term_reference(status(gpa=-0.5), TERM),
    "average_four": lambda: official_term_reference(status(gpa=4, credits=None), TERM),
    "zero_average_with_earned_credit": lambda: official_term_reference(status(gpa=0, credits=3), TERM),
    "zero_average_without_earned_credit": lambda: official_term_reference(status(gpa=0, credits=0), TERM),
    "zero_average_credit_missing": lambda: official_term_reference(status(gpa="0,0", credits=None), TERM),
    "zero_average_credit_unreadable": lambda: official_term_reference(status(gpa=0, credits="x"), TERM),
    "zero_average_declared_final": lambda: official_term_reference(
        status(gpa=0, credits=0), TERM, zero_average_is_final=True),
    "zero_average_declared_final_credit_missing": lambda: official_term_reference(
        status(gpa=0, credits=None), TERM, zero_average_is_final=True),
    "nonzero_average_without_earned_credit": lambda: official_term_reference(status(gpa=1.5, credits=0), TERM),
    "nonzero_average_declared_final": lambda: official_term_reference(
        status(gpa=1.5, credits=0), TERM, zero_average_is_final=True),
})


def attached(result: dict[str, Any], reference: dict[str, Any], **options: Any) -> dict[str, Any]:
    attach_official_reference(result, reference, **options)
    return result


REPORTED = {"status": "reported", "gpa": 3.5, "credits": 10}
AMBIGUOUS = [{"code": "ZZT 455E", "kredisi": 2, "sayilanKredi": 3.5, "used_credit": 2},
             {"code": "ZZT 456E", "kredisi": None, "sayilanKredi": "bad", "used_credit": 3}, {}]

many("attach_official_reference", {
    "match": lambda: attached({"gpa": 3.5, "first": 1}, dict(REPORTED), projection=False),
    "match_within_tolerance": lambda: attached({"gpa": 3.4951}, dict(REPORTED), projection=False),
    "mismatch": lambda: attached({"gpa": 3.4}, dict(REPORTED), projection=False),
    "mismatch_at_tolerance": lambda: attached({"gpa": 3.495}, dict(REPORTED), projection=False),
    "mismatch_with_ambiguous_credits": lambda: attached(
        {"gpa": 3.4}, dict(REPORTED), projection=False, ambiguous_credits=AMBIGUOUS),
    "mismatch_with_empty_ambiguous_credits": lambda: attached(
        {"gpa": 3.4}, dict(REPORTED), projection=False, ambiguous_credits=[]),
    "match_ignores_ambiguous_credits": lambda: attached(
        {"gpa": 3.5}, dict(REPORTED), projection=False, ambiguous_credits=AMBIGUOUS),
    "no_official": lambda: attached({"gpa": 3.4}, {"status": "unavailable", "gpa": None}, projection=False),
    "no_calculated": lambda: attached({"gpa": None, "calculation_complete": False}, dict(REPORTED), projection=False),
    "neither": lambda: attached({}, {}, projection=False),
    "official_zero": lambda: attached({"gpa": 0.0}, {"gpa": 0, "credits": 0}, projection=False),
    "projection": lambda: attached({"gpa": 4.0}, dict(REPORTED), projection=True),
    "projection_without_official": lambda: attached({"gpa": 4.0}, {"gpa": None}, projection=True),
    "projection_incomplete": lambda: attached(
        {"gpa": 3.0, "projection_complete": False}, dict(REPORTED), projection=True),
    "projection_calculation_incomplete": lambda: attached(
        {"gpa": None, "calculation_complete": False, "projection_complete": True}, dict(REPORTED), projection=True),
    "projection_complete_without_gpa": lambda: attached({"gpa": None}, dict(REPORTED), projection=True),
    "incomplete_projection_flag_without_projection": lambda: attached(
        {"gpa": 3.5, "projection_complete": False}, dict(REPORTED), projection=False),
    "existing_fields_are_overwritten": lambda: attached(
        {"gpa": 3.4, "preferred_term_gpa": 1, "calculation_warning": "old"}, dict(REPORTED), projection=True),
    "existing_warning_is_replaced_on_mismatch": lambda: attached(
        {"gpa": 3.4, "calculation_warning": "old", "mismatch_ambiguous_credit_courses": ["X"]},
        dict(REPORTED), projection=False, ambiguous_credits=AMBIGUOUS[:1]),
})

# --- plan helpers -------------------------------------------------------------

def met(code: Any, grade: Any, credit: Any, term: Any = TERM, **extra: Any) -> dict[str, Any]:
    return {"isMet": True, "bransKodu": code, "harfNotu": grade, "kredisi": str(credit),
            "kredisiDec": credit, "sayilanKredi": credit, "donem": term, "grupName": "",
            "grupId": None, "dersAdi": f"Course {code}", **extra}


def unmet(code: str, credit: Any, **extra: Any) -> dict[str, Any]:
    return {"isMet": False, "bransKodu": code, "harfNotu": "", "kredisi": str(credit),
            "kredisiDec": credit, "donem": None, "grupName": "", "grupId": None,
            "dersAdi": f"Course {code}", **extra}


def unused(code: str, grade: str, credit: Any, term: Any, **extra: Any) -> dict[str, Any]:
    return {"bransKodu": code, "harfNotu": grade, "kredisi": credit, "donem": term,
            "isValidAndUnused": False, "dersAdi": f"Course {code}", **extra}


def info(met_rows: Any = None, unused_rows: Any = None) -> dict[str, Any]:
    return {"checkMetMezuniyetList": met_rows or [], "unusedSinifOgrenciList": unused_rows or []}


PLAN = info(
    [
        met("ZZT 301E", "bb", 4), met("zzt302e", " CC ", "3,5", kredisiDec=None),
        met("ZZT 303E", "DD", 2, sayilanKredi=3, dersAdi="Şeçmeli & Öğe  İ-1"),
        met("ZZT 304E", "AA", 3, term=203110, isMet=False), met("ZZT 305E", "AA", 0, sayilanKredi="x"),
        unmet("ZZT 306E", 2), met("not a code", "AA", 3), met(None, "", "bad", term=None, dersAdi=None),
        42, None,
    ],
    [unused("ZZT 301E", "FF", 4, "203010"), unused("ZZT 307E", "VF", True, "203010"), "x"],
)


def sorted_table(table: dict[str, Any]) -> dict[str, Any]:
    """``plan_credit_table`` walks a set of codes, so its key order is not fixed."""
    return dict(sorted(table.items()))


def credit_table(rows: list[Any], term: Any = TERM, unused_rows: Any = None) -> dict[str, Any]:
    return sorted_table(plan_credit_table(plan_rows(info(rows, unused_rows)), term))


many("plan_rows", {
    "every_row_kind": lambda: plan_rows(PLAN),
    "not_an_object": lambda: plan_rows([PLAN]),
    "none": lambda: plan_rows(None),
    "buckets_not_lists": lambda: plan_rows({"checkMetMezuniyetList": {"a": 1}, "unusedSinifOgrenciList": "x"}),
    "kredisi_dec_wins_over_kredisi": lambda: plan_rows(info([
        met("ZZT 301E", "AA", 3, kredisi="5"), met("ZZT 302E", "AA", "bad", kredisi="5"),
        met("ZZT 303E", "AA", 0, kredisi="5"), met("ZZT 304E", "AA", None, kredisi="5"),
    ])),
})

many("plan_credit_table", {
    "plan": lambda: sorted_table(plan_credit_table(plan_rows(PLAN), TERM)),
    "plan_without_term": lambda: sorted_table(plan_credit_table(plan_rows(PLAN), None)),
    "plan_numeric_term": lambda: sorted_table(plan_credit_table(plan_rows(PLAN), 203110)),
    "term_row_wins": lambda: credit_table([met("ZZT 310E", "CC", 3), unmet("ZZT 310E", 5)],
                                          unused_rows=[unused("ZZT 310E", "FF", 4, "203010")]),
    "undated_wins_over_other_term": lambda: credit_table(
        [unmet("ZZT 311E", 5)], unused_rows=[unused("ZZT 311E", "FF", 4, "203010")]),
    "other_term": lambda: credit_table([], unused_rows=[unused("ZZT 312E", "FF", 4, "203010")]),
    "disagreeing_term_rows_are_not_overruled": lambda: credit_table(
        [met("ZZT 313E", "CC", 3), met("ZZT 313E", "FF", 4), unmet("ZZT 313E", 5)]),
    "disagreeing_other_terms": lambda: credit_table(
        [], unused_rows=[unused("ZZT 314E", "FF", 3, "203010"), unused("ZZT 314E", "FF", 4, "203020")]),
    "counted_credit_only_from_met_rows": lambda: credit_table([
        met("ZZT 315E", "CC", 2, sayilanKredi=3), met("ZZT 316E", "CC", 2, sayilanKredi=3, isMet=False),
        met("ZZT 317E", "CC", 2, sayilanKredi=3), met("ZZT 317E", "CC", 2, sayilanKredi=4),
        met("ZZT 318E", "CC", 2, sayilanKredi=0), met("ZZT 319E", "CC", 2, sayilanKredi=3, isMet=None),
    ]),
    "rows_without_code_or_credit": lambda: credit_table([met("bad", "CC", 3), met("ZZT 320E", "CC", 0)]),
})

REGISTERED_COURSES = [
    {"code": "ZZT 411E", "crn": "40011"}, {"code": "ZZT 412E", "crn": 40012},
    {"code": "ZZT 413E", "crn": "40013"}, {"code": "ZZT 414E", "crn": "40013"},
    {"code": "ZZT 415E", "crn": None}, {"code": "seminar one", "crn": " 40016 "}, {"code": None},
]


def projection_keys(projected: Any) -> Any:
    translated, unmatched = resolve_projection_keys(projected, REGISTERED_COURSES)
    return {"translated": translated, "unmatched": unmatched}


many("resolve_projection_keys", {
    "none": lambda: projection_keys(None),
    "codes_and_crns": lambda: projection_keys({"40011": "AA", " zzt  412e ": "bb", 40012: "BB", "'ZZT 415E'": "CC",
                                               "Seminar  One": "DD", '"40016"': "DD"}),
    "crn_shared_by_two_courses": lambda: projection_keys({"40013": "AA"}),
    "unmatched_keys_stay": lambda: projection_keys({"49999": "AA", "QQQ 999": "BB"}),
    "blank_key_and_the_course_without_a_code": lambda: projection_keys({"": "CC"}),
    "crn_and_code_agree": lambda: projection_keys({"40011": "aa", "ZZT 411E": " AA "}),
    "crn_and_code_conflict": lambda: projection_keys({"40011": "AA", "ZZT 411E": "BB"}),
})


def reconciled(courses: list[dict[str, Any]], official_gpa: Any, **options: Any) -> dict[str, Any]:
    courses = copy.deepcopy(courses)
    report = reconcile_credit_candidates(courses, official_gpa, **options)
    return {"report": report, "courses": courses}


def two_credits(code: str, grade: Any, low: float, high: float, used: float | None = None) -> dict[str, Any]:
    return {"code": code, "grade": grade, "credit": low if used is None else used, "plan_credit": low,
            "counted_credit": high, "credit_candidates": [low, high]}


ELECTIVE = [course("ZZT 301E", 4, "BB"), course("ZZT 302E", 3, "CC"), two_credits("ZZT 455E", "DD", 2, 3)]
TWO_ELECTIVES = [course("ZZT 301E", 4, "AA"), two_credits("ZZT 302E", "CC", 2, 3),
                 two_credits("ZZT 455E", "DD", 2, 3)]

many("reconcile_credit_candidates", {
    "nothing_ambiguous": lambda: reconciled([course("ZZT 301E", 4, "BB"),
                                             course("ZZT 302E", 3, "CC", credit_candidates=[3])], 2.5),
    "no_official_average": lambda: reconciled(ELECTIVE, None),
    "resolved": lambda: reconciled(ELECTIVE, 2.1),
    "confirmed": lambda: reconciled(ELECTIVE, 2.22),
    "no_candidate_matches": lambda: reconciled(ELECTIVE, 2.9),
    "both_match": lambda: reconciled(
        [course("ZZT 301E", 4, "CC"), two_credits("ZZT 455E", "CC", 2, 3)], 2.0),
    "current_credit_is_not_a_candidate_and_two_match": lambda: reconciled(
        [course("ZZT 301E", 4, "CC"), two_credits("ZZT 455E", "CC", 2, 3, used=5)], 2.0),
    "current_credit_is_not_a_candidate_and_one_matches": lambda: reconciled(
        [course("ZZT 301E", 4, "BB"), course("ZZT 302E", 3, "CC"), two_credits("ZZT 455E", "DD", 2, 3, used=5)],
        2.1),
    "not_comparable": lambda: reconciled([*ELECTIVE, course("ZZT 303E", 3, None)], 2.1),
    "too_many": lambda: reconciled(ELECTIVE, 2.1, max_ambiguous=0),
    "two_rows_resolved_together": lambda: reconciled(TWO_ELECTIVES, 2.67),
    "two_rows_undetermined": lambda: reconciled(TWO_ELECTIVES, 2.5),
    "official_at_tolerance": lambda: reconciled(ELECTIVE, 2.105),
    "official_zero": lambda: reconciled([two_credits("ZZT 455E", "FF", 2, 3)], 0),
})

ATTEMPT_ROWS = plan_rows(info(
    [
        met("ZZT 150E", "CB", 3, term="203130"), met("ZZT 150E", "CB", 3, term="203130"),
        met("ZZT 150E", "", 3, term="203210", isMet=False), met("ZZT 150E", "DD", 3, term=None),
        met("ZZT 150E", "AA", 3, term="yaz"), met("ZZT 160E", "BB", 3),
    ],
    [
        unused("ZZT 150E", "FF", 3, "203110"), unused("ZZT 150E", "VF", 3, TERM),
        unused("OLD 150E", "FF", 3, "203010", dersAdi="course zzt-150e"),
        unused("OLD 151E", "FF", 4, "203010", dersAdi="Course ZZT 150E"),
        unused("OLD 152E", "CC", 3, "203010", dersAdi="Course ZZT 150E"),
        unused("OLD 153E", "BZ", 3, "203020", dersAdi="Widgets"),
        unused("bad code", "FF", 3, "203020", dersAdi="Widgets"),
    ],
))

many("attempt_notes", {
    "earlier_later_and_equivalent": lambda: attempt_notes(
        ATTEMPT_ROWS, code="ZZT 150E", term_code=TERM, names=set(), credits={3.0}),
    "without_term": lambda: attempt_notes(ATTEMPT_ROWS, code="ZZT 150E", term_code=None, names=set(), credits={3}),
    "numeric_term": lambda: attempt_notes(
        ATTEMPT_ROWS, code="ZZT 150E", term_code=203130, names={"widgets", ""}, credits={3}),
    "no_code_uses_names": lambda: attempt_notes(
        ATTEMPT_ROWS, code=None, term_code=TERM, names={"widgets"}, credits={3}),
    "credit_must_match": lambda: attempt_notes(
        ATTEMPT_ROWS, code="ZZT 150E", term_code=TERM, names=set(), credits=set()),
    "course_without_other_attempts": lambda: attempt_notes(
        ATTEMPT_ROWS, code="ZZT 160E", term_code=TERM, names=set(), credits={3}),
    "no_rows": lambda: attempt_notes([], code="ZZT 150E", term_code=TERM, names={"x"}, credits={3}),
})

# --- compact_term_result ----------------------------------------------------

def full_result() -> dict[str, Any]:
    result = calculate_gpa([
        course("ZZT 301E", 3, "AA", credit_source="registration", grade_source="degree_plan", recorded_credit="3",
               plan_credit=3, counted_credit=4, crn="1"),
        course("ZZT 302E", 4, "FF", credit_source="degree_plan", grade_source="degree_plan", recorded_credit=None,
               plan_credit=None, counted_credit=4, crn="2"),
        course("ZZT 303E", 2, None, credit_source="degree_plan", grade_source="registration", plan_credit=2, crn="3"),
        course("ZZT 304E", 2, None, credit_source=None, grade_source=7, plan_credit=5),
    ], projected_grades={"ZZT 301E": "BB"})
    attach_official_reference(result, {"status": "reported", "gpa": 2.0, "credits": 7, "term_code": TERM,
                                       "program": "Sample", "source": "s", "finality": "f"}, projection=True)
    return result


many("compact_term_result", {
    "projection_requested": lambda: compact_term_result(full_result(), projection_requested=True),
    "projection_not_requested": lambda: compact_term_result(full_result(), projection_requested=False),
    "complete_result_drops_known_courses_gpa": lambda: compact_term_result(
        calculate_gpa([course("ZZT 301E", 3, "AA")]), projection_requested=False),
    "no_sources": lambda: compact_term_result(calculate_gpa([course("ZZT 301E", 3, None)]), projection_requested=False),
    "source_tie_goes_to_registration": lambda: compact_term_result(
        {"courses": [{"code": "A", "credit_source": "degree_plan", "grade_source": "registration"},
                     {"code": "B", "credit_source": "registration", "grade_source": "degree_plan"}],
         "ungraded": [], "official_reference": "text", "ff_risk": []}, projection_requested=False),
    "result_without_course_lists": lambda: compact_term_result(
        {"gpa": None, "known_courses_gpa": None, "official_reference": None, "extra": 1}, projection_requested=True),
})

# --- obs_calculate_gpa ----------------------------------------------------

def reg(branch: str, number: str, crn: Any, **extra: Any) -> dict[str, Any]:
    return {"bransKodu": branch, "dersKodu": number, "crn": crn, "kredi": None, "harfNotu": None,
            "dersAdiTR": f"{branch} {number}", **extra}


def make_app(registered: Any, *, met_rows: Any = None, unused_rows: Any = None,
             official_status: Any = None, payload: Any = None, graduation: Any = None) -> NinovaMcpApp:
    """An app whose OBS client never leaves the process."""
    app = NinovaMcpApp.__new__(NinovaMcpApp)
    obs = ObsClient(ninova_client=MagicMock())
    obs.list_semesters = Mock(return_value=SEMESTERS)  # type: ignore[method-assign]
    obs.list_registered_courses = Mock(  # type: ignore[method-assign]
        return_value=payload if payload is not None else {"statusCode": 0, "kayitSinifResultList": registered})
    obs.default_program_id = Mock(return_value=1)  # type: ignore[method-assign]
    obs.get_graduation_remaining = Mock(  # type: ignore[method-assign]
        return_value=graduation if graduation is not None
        else {"mezuniyetimeNeKaldiBilgi": info(met_rows, unused_rows)})
    obs.get_registration_status = Mock(  # type: ignore[method-assign]
        return_value=official_status if official_status is not None else {})
    app._obs = obs
    return app


def tool(registered: Any, *, semester: Any = TERM, projected_grades: Any = None, include_official: bool = True,
         **setup: Any) -> Any:
    return make_app(registered, **setup).obs_calculate_gpa(
        semester=semester, projected_grades=projected_grades, include_official=include_official)


def failing(error: Exception, method: str, registered: Any, **options: Any) -> Any:
    app = make_app(registered)
    getattr(app.obs, method).side_effect = error
    return app.obs_calculate_gpa(semester=TERM, **options)


GRADED = [reg("ZZT", "301E", "40031", kredi="4", harfNotu="BB", dersAdiEN="Three-o-one"),
          reg("ZZT", "302E", "40032", kredi=3, harfNotu="CC")]
ELECTIVE_REGISTERED = [reg("ZZT", "301E", "40031"), reg("ZZT", "302E", "40032"), reg("ZZT", "455E", "40033")]
ELECTIVE_PLAN = [met("ZZT 301E", "BB", 4), met("ZZT 302E", "CC", 3),
                 met("ZZT 455E", "DD", 2, sayilanKredi=3, grupName="Elective", grupId=7001)]

many("obs_calculate_gpa", {
    "graded_term_matches_official": lambda: tool(GRADED, official_status=status(2.57, 7)),
    "graded_term_mismatch": lambda: tool(GRADED, official_status=status(3.0, 7)),
    "official_not_requested": lambda: tool(GRADED, include_official=False),
    "official_unreadable": lambda: failing(NinovaError("down"), "get_registration_status", GRADED),
    "official_unreadable_value_error": lambda: failing(ValueError("bad"), "get_registration_status", GRADED),
    "official_empty": lambda: tool(GRADED),
    "no_registered_courses": lambda: tool([], include_official=False),
    "latest_term_by_default": lambda: tool(GRADED, semester=None, include_official=False),
    "registered_response_failed": lambda: tool([], payload={"statusCode": 1, "kayitSinifResultList": []}),
    "registered_status_code_text": lambda: tool([], payload={"statusCode": "0", "kayitSinifResultList": []}),
    "registered_not_an_object": lambda: tool([], payload=[1]),
    "registered_rows_not_a_list": lambda: tool([], payload={"kayitSinifResultList": 42}),
    "registered_rows_missing": lambda: tool([], payload={"statusCode": 0}),
    "registered_row_not_an_object": lambda: tool([reg("ZZT", "301E", "1"), 42]),
    "registered_without_status_code": lambda: tool(
        [], payload={"kayitSinifResultList": [reg("ZZT", "301E", "1", kredi=3, harfNotu="AA")]},
        include_official=False),
    "credit_and_grade_from_plan": lambda: tool(
        [reg("ZZT", "301E", "40031"), reg("ZZT", "302E", "40032", kredi="0"),
         reg("ZZT", "303E", "40033", kredi="x", harfNotu=""), reg("ZZT", "304E", "40034", kredi=True),
         reg("ZZT", "305E", "40035", kredi="3", harfNotu="AA"), reg("ZZT", "306E", "40036", kredi=-1)],
        met_rows=[met("ZZT 301E", "BB", 4), met("ZZT 302E", "CC", 3), unmet("ZZT 303E", 2),
                  met("ZZT 305E", "FF", 5)],
        unused_rows=[unused("ZZT 304E", "FF", 3, "203010")], include_official=False),
    "credit_from_other_term_and_missing": lambda: tool(
        [reg("ZZT", "210E", "40021"), reg("ZZT", "250E", "40025"), reg("ZZT", "260E", "40026", harfNotu="BL"),
         reg("", "", None), reg("zzt", "270e", "40027", kredi=NAN),
         {"bransKodu": "ZZT", "dersKodu": "280E", "dersAdiEN": "Only English", "kredi": "2,5", "harfNotu": "cb"},
         {"bransKodu": "ZZT", "harfNotu": "AA"}],
        unused_rows=[unused("ZZT 210E", "FF", 4, "203010"), unused("ZZT 250E", "FF", 3, "203010"),
                     unused("ZZT 250E", "FF", 4, "203020")], include_official=False),
    "conflicting_plan_grades_are_not_used": lambda: tool(
        [reg("ZZT", "301E", "40031", kredi=3)],
        met_rows=[met("ZZT 301E", "BB", 3)], unused_rows=[unused("ZZT 301E", "FF", 3, TERM)],
        include_official=False),
    "graduation_unavailable": lambda: failing(ObsError("down"), "get_graduation_remaining", GRADED,
                                              include_official=False),
    "graduation_program_unavailable": lambda: failing(NinovaError("down"), "default_program_id", GRADED,
                                                      include_official=False),
    "graduation_unrecognized": lambda: tool(GRADED, graduation={"mezuniyetimeNeKaldiBilgi": [1]},
                                            include_official=False),
    "graduation_not_an_object": lambda: tool(GRADED, graduation=[1], include_official=False),
    "elective_resolved_by_official": lambda: tool(
        ELECTIVE_REGISTERED, met_rows=ELECTIVE_PLAN, official_status=status(2.1, 10)),
    "elective_confirmed_by_official": lambda: tool(
        ELECTIVE_REGISTERED, met_rows=ELECTIVE_PLAN, official_status=status(2.22, 9)),
    "elective_mismatch_names_course": lambda: tool(
        ELECTIVE_REGISTERED, met_rows=ELECTIVE_PLAN, official_status=status(2.9, 10)),
    "elective_without_official": lambda: tool(ELECTIVE_REGISTERED, met_rows=ELECTIVE_PLAN, include_official=False),
    "elective_with_registration_credit_is_not_ambiguous": lambda: tool(
        [*ELECTIVE_REGISTERED[:2], reg("ZZT", "455E", "40033", kredi=2)], met_rows=ELECTIVE_PLAN,
        official_status=status(2.1, 10)),
    "elective_projection_uses_resolved_credit": lambda: tool(
        ELECTIVE_REGISTERED, met_rows=ELECTIVE_PLAN, official_status=status(2.1, 10),
        projected_grades={"40033": "aa"}),
    "attempts_are_listed": lambda: tool(
        [reg("ZZT", "150E", "40041", dersAdiEN="Intro to Widgets"), reg("ZZT", "160E", "40042")],
        met_rows=[met("ZZT 150E", "CB", 3, term="203130"), met("ZZT 160E", "BB", 3)],
        unused_rows=[unused("ZZT 150E", "FF", 3, "203110"), unused("ZZT 150E", "VF", 3, TERM),
                     unused("OLD 150E", "FF", 3, "203010", dersAdi="INTRO TO  WIDGETS")],
        include_official=False),
    "all_failed_term_official_zero": lambda: tool(
        [reg("ZZT", "180E", "40051"), reg("ZZT", "190E", "40052"), reg("ZZT", "195E", "40053", harfNotu="BZ")],
        unused_rows=[unused("ZZT 180E", "FF", 3, TERM), unused("ZZT 190E", "VF", 4, TERM)],
        official_status=status(0, 0)),
    "ungraded_term_official_zero": lambda: tool([reg("ZZT", "180E", "40051", kredi="3")],
                                                official_status=status(0, 0)),
    "partly_failed_term_official_zero": lambda: tool(
        [reg("ZZT", "180E", "40051", kredi=3, harfNotu="FF"), reg("ZZT", "190E", "40052", kredi=3)],
        official_status=status(0, 0)),
    "only_excluded_grades_official_zero": lambda: tool(
        [reg("ZZT", "180E", "40051", kredi=0, harfNotu="BL")], official_status=status(0, 0)),
    "unrecognized_grade_official_zero": lambda: tool(
        [reg("ZZT", "180E", "40051", kredi=3, harfNotu="FF"), reg("ZZT", "190E", "40052", kredi=3, harfNotu="??")],
        official_status=status(0, 0)),
    "projection_by_crn_and_code": lambda: tool(
        [reg("ZZT", "411E", "40011", kredi="3"), reg("ZZT", "412E", 40012, kredi="3")],
        projected_grades={"40011": "bb", "zzt412e": "AA"}, official_status=status(3.0, 6)),
    "projection_partly_unmatched": lambda: tool(
        [reg("ZZT", "411E", "40011", kredi="3"), reg("ZZT", "412E", None, kredi="3")],
        projected_grades={"49999": "AA", "ZZT 412E": "BB"}, include_official=False),
    "projection_nothing_matched": lambda: tool(
        [reg("ZZT", "411E", "40011", kredi="3", harfNotu="CC")], projected_grades={"QQQ 999": "AA"},
        official_status=status(2.0, 3)),
    "projection_conflict": lambda: tool(
        [reg("ZZT", "411E", "40011", kredi="3")], projected_grades={"40011": "AA", "ZZT 411E": "BB"}),
    "projection_unknown_grade": lambda: tool(GRADED, projected_grades={"40031": "A"}),
    "projection_empty_mapping": lambda: tool(GRADED, projected_grades={}, include_official=False),
    "unknown_semester": lambda: tool(GRADED, semester="209920"),
})


def bare_app() -> NinovaMcpApp:
    return NinovaMcpApp.__new__(NinovaMcpApp)


many("plan_lookups", {
    "credit": lambda: sorted_table(bare_app()._plan_credit_lookup(PLAN, TERM)),
    "credit_without_term": lambda: sorted_table(bare_app()._plan_credit_lookup(PLAN)),
    "grade": lambda: sorted_table(bare_app()._plan_grade_lookup(PLAN, TERM)),
    "grade_numeric_term": lambda: sorted_table(bare_app()._plan_grade_lookup(PLAN, 203110)),  # type: ignore[arg-type]
    "grade_without_term": lambda: bare_app()._plan_grade_lookup(PLAN, None),
    "grade_conflict": lambda: bare_app()._plan_grade_lookup(
        info([met("ZZT 301E", "BB", 3)], [unused("ZZT 301E", "FF", 3, TERM)]), TERM),
    "target_gpa_tool": lambda: bare_app().calculate_target_gpa(
        2.5, 60, 3.0, 30, retakes=[{"credit": 3, "old_grade": "FF"}]),
})

many("normalize_arguments", {
    "accepted": lambda: normalize_arguments({"zzt 101e": " bb ", 40011: "bl"}, "cc",
                                            [{"code": " ZZT 102E ", "grade": "aa", "credit": "3,5",
                                              "replaces": "zzt 103e"}], 2.5),
    "unknown_projected_grade": lambda: normalize_arguments({"ZZT 101E": "A+"}),
    "projected_grade_not_text": lambda: normalize_arguments({40011: 4}),
    "unknown_default_grade": lambda: normalize_arguments(default_grade="ZZ"),
    "unknown_extra_grade": lambda: normalize_arguments(extra_courses=[{"code": "ZZT 102E", "grade": None}]),
    "blank_key": lambda: normalize_arguments({" ": "AA"}),
    "not_an_object": lambda: normalize_arguments(["AA"]),
})


class GpaCharacterizationTests(unittest.TestCase):
    maxDiff = None

    def test_every_case_matches_the_snapshot(self) -> None:
        expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(sorted(expected), sorted(CASES))
        for name, function in CASES.items():
            with self.subTest(case=name):
                self.assertEqual(outcome(function), expected[name])


class ToolCallOrderTests(unittest.TestCase):
    """What obs_calculate_gpa reads, in which order, and what stops it early."""

    METHODS = ("list_semesters", "list_registered_courses", "default_program_id",
               "get_graduation_remaining", "get_registration_status")

    def calls(self, app: NinovaMcpApp) -> list[str]:
        return [method for method in self.METHODS if getattr(app.obs, method).call_count]

    def recorded(self, app: NinovaMcpApp) -> list[str]:
        order: list[str] = []
        for method in self.METHODS:
            mock = getattr(app.obs, method)

            def record(*args: Any, _name: str = method, _value: Any = mock.return_value, **kwargs: Any) -> Any:
                order.append(_name)
                return _value

            mock.side_effect = record
        return order

    def test_reads_happen_once_each_in_a_fixed_order(self) -> None:
        app = make_app(GRADED, official_status=status(2.57, 7))
        order = self.recorded(app)
        app.obs_calculate_gpa(semester=TERM)
        self.assertEqual(order, list(self.METHODS))
        app.obs.list_registered_courses.assert_called_once_with(12)
        app.obs.get_graduation_remaining.assert_called_once_with(1)

    def test_official_record_is_not_read_when_not_requested(self) -> None:
        app = make_app(GRADED)
        order = self.recorded(app)
        app.obs_calculate_gpa(semester=TERM, include_official=False)
        self.assertEqual(order, list(self.METHODS[:4]))

    def test_bad_projected_grade_stops_before_any_read(self) -> None:
        for projected in ({"40031": "A+"}, ["AA"], {"": "AA"}):
            with self.subTest(projected=projected):
                app = make_app(GRADED)
                with self.assertRaises(ValueError):
                    app.obs_calculate_gpa(semester=TERM, projected_grades=projected)  # type: ignore[arg-type]
                self.assertEqual(self.calls(app), [])

    def test_unreadable_registered_courses_stop_before_the_graduation_read(self) -> None:
        app = make_app([], payload={"statusCode": 1, "kayitSinifResultList": []})
        with self.assertRaises(NinovaError):
            app.obs_calculate_gpa(semester=TERM)
        self.assertEqual(self.calls(app), ["list_semesters", "list_registered_courses"])

    def test_conflicting_projection_keys_are_found_after_the_reads(self) -> None:
        app = make_app([reg("ZZT", "411E", "40011", kredi="3")])
        with self.assertRaises(ValueError):
            app.obs_calculate_gpa(semester=TERM, projected_grades={"40011": "AA", "ZZT 411E": "BB"})
        self.assertEqual(self.calls(app), list(self.METHODS))

    def test_tool_goes_through_the_overridable_graduation_reader(self) -> None:
        app = make_app([reg("ZZT", "301E", "40031")])
        app._fetch_graduation_info = Mock(return_value=info([met("ZZT 301E", "AA", 3)]))  # type: ignore[method-assign]
        app._plan_grade_lookup = Mock(return_value={"ZZT 301E": "CC"})  # type: ignore[method-assign]
        result = app.obs_calculate_gpa(semester=TERM, include_official=False)
        app._fetch_graduation_info.assert_called_once_with()
        app._plan_grade_lookup.assert_called_once_with(app._fetch_graduation_info.return_value, TERM)
        app.obs.get_graduation_remaining.assert_not_called()
        self.assertEqual(result["gpa"], 2.0)

    def test_input_courses_are_not_changed_by_the_calculator(self) -> None:
        courses = [course("ZZT 301E", "3", " bb "), course("ZZT 302E", None, None, extra=[1])]
        before = copy.deepcopy(courses)
        calculate_gpa(courses, projected_grades={"ZZT 302E": "AA"})
        self.assertEqual(courses, before)


def write_snapshot() -> None:
    SNAPSHOT.write_text(
        json.dumps({name: outcome(function) for name, function in CASES.items()}, ensure_ascii=False, indent=1)
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    if "--write" in sys.argv:
        write_snapshot()
    else:
        unittest.main()
