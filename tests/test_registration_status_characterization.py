"""Characterization of the registration-status summary.

Every scenario below is a synthetic payload. What the summary, the default
reducer and the term history return for it was recorded from the code before
it was restructured (``fixtures/characterization/registration_status.json.gz``)
and is compared as text, so field order is pinned along with the values.
"""

from __future__ import annotations

import unittest
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from ninova_mcp import registration_status
from ninova_mcp.registration_status import (
    compact_registration_summary,
    summarize_registration_status,
    term_history,
)

import characterization_golden as golden

GOLDEN_FILE = "registration_status.json.gz"
NOW = datetime.fromisoformat("2026-09-16T12:00:00+03:00")
CALENDAR_URL = "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/EN/academic-calendar/index.php"


# -- payload builders -----------------------------------------------------


def term(code: Any, name: Any = None, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"akademikDonemKodu": code}
    if name is not None:
        row["akademikDonemAdi"] = name
    row.update(fields)
    return row


def identity(name: str = "Örnek Mühendisliği", **fields: Any) -> dict[str, Any]:
    return {
        "akademikProgramAdi": name, "akademikProgramAdiEN": f"{name} (EN)",
        "akademikBolumAdi": "Örnek Bölümü", "akademikBolumAdiEN": "Example Department",
        "fakulteAdi": "Örnek Fakültesi", "fakulteAdiEN": "Example Faculty",
        "durum": "Aktif", "durumKodu": "AS", **fields,
    }


def status_terms(level: Any = "3. Sınıf") -> list[dict[str, Any]]:
    return [
        term("202620", "2025-2026 Bahar Dönemi", akademikDonemAdiEN="2025-2026 Spring Semester",
             sinifSeviye=level, donemlikNotOrtalamasi=2.5, genelNotOrtalamasi=2.4, verilenKredi=12.0),
        term("202610", "2025-2026 Güz Dönemi", sinifSeviye="2. Sınıf",
             donemlikNotOrtalamasi=2.0, genelNotOrtalamasi=2.2, verilenKredi=15.5),
    ]


def lesson_terms(name: Any = "2026-2027 Güz Dönemi") -> list[dict[str, Any]]:
    return [
        term("202710", name, dersKayitDurumu="Kayıt Olabilir"),
        term("202620", "2025-2026 Bahar Dönemi", dersKayitDurumu="Kayıt Olamaz"),
    ]


def status_program(name: str = "Örnek Mühendisliği", level: Any = "3. Sınıf", **fields: Any) -> dict[str, Any]:
    return {**identity(name), "kayitDurumuDonemList": status_terms(level), **fields}


def lesson_program(name: str = "Örnek Mühendisliği", **fields: Any) -> dict[str, Any]:
    return {**identity(name), "dersKayitDurumuDonemList": lesson_terms(), **fields}


def overall_program(**fields: Any) -> dict[str, Any]:
    return {
        "akademikProgramAdi": "", "akademikProgramAdiEN": "", "fakulteAdi": "", "fakulteAdiEN": "",
        "akademikBolumAdi": "Üniversite Geneli", "akademikBolumAdiEN": "University Overall",
        "durum": "", "durumKodu": None, "kayitDurumuDonemList": status_terms(), **fields,
    }


def status_payload(*programs: Any) -> dict[str, Any]:
    return {"statusCode": 0, "kayitDurumuList": list(programs)}


def lesson_payload(*programs: Any) -> dict[str, Any]:
    return {"statusCode": 0, "dersKayitDurumuList": list(programs)}


def window(start: Any, end: Any, level: int | None = 3, **fields: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "description": f"Online Registration Day for class {level}", "start_at": start, "end_at": end,
        "category": "registration", "scope": "undergraduate",
        "class_levels": [] if level is None else [level],
        "registration_kind": "class_window" if level is not None else "all_students_reopening",
        "academic_term": "fall", "academic_year": "2026-2027", "source_url": CALENDAR_URL,
    }
    event.update(fields)
    return event


def day(number: int, start: str = "10:00", end: str = "13:00") -> tuple[str, str]:
    return f"2026-09-{number:02d}T{start}:00+03:00", f"2026-09-{number:02d}T{end}:00+03:00"


def calendar(*events: Any, **fields: Any) -> dict[str, Any]:
    return {
        "events": list(events),
        "coverage": {"scope": "displayed_month", "start_date": "2026-09-01", "end_date": "2026-09-30", "complete": True},
        "parse_complete": True, **fields,
    }


DEFAULT_CALENDAR = calendar(
    window(*day(17, "14:00", "17:00"), 3),
    window(*day(18), 2),
    window(*day(17), 4),
    window(*day(19, "12:00", "17:00"), None),
)


def case(
    registration: Any = None, lesson: Any = None, cal: Any = DEFAULT_CALENDAR,
    now: Any = NOW, calendar_error: str | None = None, *, default_status: bool = True,
) -> dict[str, Any]:
    if registration is None and default_status:
        registration = status_payload(status_program())
    if lesson is None and default_status:
        lesson = lesson_payload(lesson_program())
    return {"registration": registration, "lesson": lesson, "calendar": cal, "now": now,
            "calendar_error": calendar_error}


def with_status_terms(terms: Any) -> dict[str, Any]:
    return case(status_payload(status_program(kayitDurumuDonemList=terms)))


def with_lesson_terms(terms: Any) -> dict[str, Any]:
    return case(lesson=lesson_payload(lesson_program(dersKayitDurumuDonemList=terms)))


def with_windows(*events: Any, now: Any = NOW) -> dict[str, Any]:
    return case(cal=calendar(*events), now=now)


def at(moment: str) -> datetime:
    return datetime.fromisoformat(moment)


SUMMER = term("202630", "2025-2026 Yaz Öğretimi", sinifSeviye="3. Sınıf",
              donemlikNotOrtalamasi=0.0, genelNotOrtalamasi=2.3, verilenKredi=0.0)


def scenarios() -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}

    # One programme, and where "now" falls against its class window.
    cases["one_program_before_window"] = case()
    cases["one_program_during_window"] = case(now=at("2026-09-17T15:00:00+03:00"))
    cases["one_program_after_window"] = case(now=at("2026-09-17T18:00:00+03:00"))
    cases["now_exactly_at_opening_is_ongoing"] = case(now=at("2026-09-17T14:00:00+03:00"))
    cases["now_exactly_at_closing_is_past"] = case(now=at("2026-09-17T17:00:00+03:00"))
    cases["now_in_another_timezone"] = case(now=datetime(2026, 9, 17, 12, 0, tzinfo=UTC))
    cases["now_after_every_event"] = case(now=at("2026-12-01T00:00:00+03:00"))

    # Several programmes.
    cases["two_active_programs"] = case(
        status_payload(status_program("Program A"), status_program("Program B", "2. Sınıf")),
        lesson_payload(lesson_program("Program B"), lesson_program("Program A")))
    cases["one_active_one_passive"] = case(
        status_payload(status_program("Program A", durum="Pasif"), status_program("Program B")),
        lesson_payload(lesson_program("Program A", durum="Pasif"), lesson_program("Program B")))
    cases["one_active_one_unknown"] = case(
        status_payload(status_program("Program A"), status_program("Program B", durum="İzinli")),
        lesson_payload(lesson_program("Program A"), lesson_program("Program B", durum="İzinli")))
    cases["second_program_without_lesson_record"] = case(
        status_payload(status_program("Program A"), status_program("Program B")),
        lesson_payload(lesson_program("Program A")))
    cases["duplicate_lesson_programs_are_ambiguous"] = case(
        lesson=lesson_payload(lesson_program(), lesson_program()))
    cases["graduated_and_active"] = case(
        status_payload(status_program("Program A", durum="Mezun"), status_program("Program B", durum="Active Student")),
        lesson_payload(lesson_program("Program A", durum="Graduated"), lesson_program("Program B", durum="Active")))

    # The "Üniversite Geneli" row.
    cases["university_overall_with_sole_program"] = case(
        status_payload(overall_program()), lesson_payload(lesson_program()))
    cases["university_overall_english_label_only"] = case(
        status_payload(overall_program(akademikBolumAdi="")), lesson_payload(lesson_program()))
    cases["university_overall_with_two_lesson_programs"] = case(
        status_payload(overall_program()), lesson_payload(lesson_program("Program A"), lesson_program("Program B")))
    cases["university_overall_beside_a_named_program"] = case(
        status_payload(overall_program(), status_program()), lesson_payload(lesson_program()))
    cases["university_overall_with_blank_lesson_status"] = case(
        status_payload(overall_program()), lesson_payload(lesson_program(durum="")))
    cases["university_overall_with_own_status"] = case(
        status_payload(overall_program(durum="Aktif Öğrenci")), lesson_payload(lesson_program(durum="Pasif")))
    cases["university_overall_with_faculty_is_not_overall"] = case(
        status_payload(overall_program(fakulteAdi="Örnek Fakültesi")), lesson_payload(lesson_program()))
    cases["university_overall_without_lesson_programs"] = case(
        status_payload(overall_program()), lesson_payload())
    cases["university_overall_with_graduate_sole_program"] = case(
        status_payload(overall_program()), lesson_payload(lesson_program("Örnek Yüksek Lisans Programı")))

    # Status labels.
    cases["blank_status_label"] = case(status_payload(status_program(durum="")))
    cases["whitespace_status_label"] = case(status_payload(status_program(durum="   ")))
    cases["missing_status_label"] = case(status_payload(status_program(durum=None)))
    cases["blank_status_on_both_endpoints"] = case(
        status_payload(status_program(durum="")), lesson_payload(lesson_program(durum=None)))
    cases["conflicting_status_labels"] = case(lesson=lesson_payload(lesson_program(durum="Pasif")))
    cases["unrecognized_status_label"] = case(status_payload(status_program(durum="Kayıt Donduruldu")),
                                              lesson_payload(lesson_program(durum="Kayıt Donduruldu")))
    cases["status_removed_from_register"] = case(status_payload(status_program(durum="Kaydı Silindi")),
                                                 lesson_payload(lesson_program(durum="İlişiği Kesildi")))
    cases["non_string_status_label"] = case(status_payload(status_program(durum=1)))

    # Summer as the latest term.
    cases["summer_latest_by_name"] = with_status_terms([SUMMER, *status_terms()])
    cases["summer_latest_by_code_only"] = with_status_terms(
        [term("202630", "Dönem 3", sinifSeviye="3. Sınıf", donemlikNotOrtalamasi=1.0), *status_terms()])
    cases["code_30_named_as_fall_is_regular"] = with_status_terms(
        [term("202630", "2025-2026 Güz Dönemi", sinifSeviye="3. Sınıf"), *status_terms()])
    cases["season_word_without_years"] = with_status_terms(
        [term("202630", "Yaz", sinifSeviye="3. Sınıf"), term("202620", "Bahar", sinifSeviye="2. Sınıf")])
    cases["only_summer_terms"] = with_status_terms([SUMMER])
    cases["summer_then_conflicting_regular_terms"] = with_status_terms(
        [SUMMER, *status_terms(), {**status_terms()[0], "genelNotOrtalamasi": 3.9}])
    cases["conflicting_term_names_between_languages"] = with_status_terms(
        [term("202620", "2025-2026 Bahar Dönemi", akademikDonemAdiEN="2025-2026 Fall Semester", sinifSeviye="3. Sınıf")])

    # No terms at all, and malformed rows.
    cases["no_status_terms_key"] = case(status_payload(identity()))
    cases["empty_status_terms"] = with_status_terms([])
    cases["null_status_terms"] = with_status_terms(None)
    cases["status_terms_not_a_list"] = with_status_terms({"akademikDonemKodu": "202620"})
    cases["status_term_row_not_a_dict"] = with_status_terms([*status_terms(), "202510"])
    cases["status_term_code_malformed"] = with_status_terms([term("20262", "2025-2026 Bahar Dönemi", sinifSeviye="3. Sınıf")])
    cases["status_term_code_missing"] = with_status_terms([term(None, "2025-2026 Bahar Dönemi", sinifSeviye="3. Sınıf")])
    cases["status_term_code_numeric"] = with_status_terms([term(202620, "2025-2026 Bahar Dönemi", sinifSeviye="3. Sınıf")])
    cases["latest_status_term_conflicts"] = with_status_terms(
        [*status_terms(), {**status_terms()[0], "sinifSeviye": "2. Sınıf"}])
    cases["latest_status_term_repeated_identically"] = with_status_terms([status_terms()[0], *status_terms()])
    cases["no_lesson_terms_key"] = case(lesson=lesson_payload(identity()))
    cases["empty_lesson_terms"] = with_lesson_terms([])
    cases["lesson_term_code_malformed"] = with_lesson_terms([term("x", "2026-2027 Güz Dönemi")])
    cases["latest_lesson_term_conflicts"] = with_lesson_terms(
        [*lesson_terms(), {**lesson_terms()[0], "dersKayitDurumu": "Kayıt Olamaz"}])
    cases["lesson_term_without_season"] = with_lesson_terms([term("202710", "Yeni Dönem", dersKayitDurumu="Kayıt Olabilir")])
    cases["lesson_term_without_name"] = with_lesson_terms([term("202710", dersKayitDurumu="Kayıt Olabilir")])
    cases["lesson_term_of_another_year"] = with_lesson_terms([term("202810", "2027-2028 Güz Dönemi")])
    cases["lesson_term_of_another_season"] = with_lesson_terms([term("202720", "2026-2027 Bahar Dönemi")])

    # Unrecognized payloads.
    cases["both_payloads_missing"] = case(default_status=False)
    cases["both_payloads_missing_with_calendar_error"] = case(
        cal=None, calendar_error="The public academic calendar is unavailable.", default_status=False)
    cases["status_payload_failed"] = case({"statusCode": 1, "kayitDurumuList": [status_program()]})
    cases["status_code_is_a_boolean"] = case({"statusCode": False, "kayitDurumuList": [status_program()]})
    cases["status_payload_is_text"] = case("unavailable")
    cases["status_list_holds_a_non_dict"] = case(status_payload(status_program(), "row"))
    cases["status_list_is_not_a_list"] = case({"statusCode": 0, "kayitDurumuList": {"0": status_program()}})
    cases["lesson_payload_failed"] = case(lesson={"statusCode": 404, "dersKayitDurumuList": []})
    cases["lesson_payload_without_list"] = case(lesson={"statusCode": 0})
    cases["empty_program_lists"] = case(status_payload(), lesson_payload())

    # Programme identity.
    cases["program_without_faculty_cannot_be_matched"] = case(
        status_payload(status_program(fakulteAdi="", fakulteAdiEN="")),
        lesson_payload(lesson_program(fakulteAdi="", fakulteAdiEN="")))
    cases["labels_match_across_languages_and_case"] = case(
        status_payload(status_program(akademikProgramAdi="ÖRNEK  MÜHENDİSLİĞİ")),
        lesson_payload(lesson_program(akademikBolumAdi="", akademikBolumAdiEN="Örnek Bölümü")))
    cases["program_without_names"] = case(
        status_payload({"durum": "Aktif", "kayitDurumuDonemList": status_terms()}), lesson_payload(lesson_program()))

    # Class level.
    for label_name, label in (
        ("english_ordinal", "3rd Class"), ("bare_number_word", "4 sinif"), ("first_class", "1st class"),
        ("preparatory", "Hazırlık"), ("fifth", "5. Sınıf"), ("blank", ""), ("missing", None),
        ("number", 3), ("padded", "  2. Sınıf  "),
    ):
        cases[f"class_label_{label_name}"] = case(status_payload(status_program(level=label)))

    # Programme type.
    for type_name, program_name in (
        ("masters", "Örnek Yüksek Lisans Programı"), ("doctorate", "Example PhD Program"),
        ("preparatory", "İngilizce Hazırlık"), ("doktora", "Örnek Doktora"), ("master_inside_word", "Mastering Design"),
    ):
        cases[f"program_type_{type_name}"] = case(
            status_payload(status_program(program_name)), lesson_payload(lesson_program(program_name)))

    # Calendar shapes.
    cases["calendar_missing"] = case(cal=None)
    cases["calendar_missing_with_error"] = case(
        cal=None, calendar_error="The public academic calendar is unavailable. Official OBS status is retained.")
    cases["calendar_empty_dict"] = case(cal={})
    cases["calendar_without_events"] = case(cal={"coverage": None, "parse_complete": False})
    cases["calendar_events_null"] = case(cal=calendar(events=None))
    cases["calendar_with_non_dict_events"] = case(cal=calendar("row", None, 7, window(*day(17), 3)))
    cases["calendar_error_with_calendar_present"] = case(calendar_error="Partial calendar.")
    cases["no_window_for_this_class"] = with_windows(window(*day(17), 4), window(*day(18), 2))
    cases["window_for_several_classes_is_not_this_class"] = with_windows(window(*day(17), 3, class_levels=[3, 4]))
    cases["window_of_other_scope_category_year_term"] = with_windows(
        window(*day(17), 3, scope="graduate"), window(*day(17), 3, category="exam"),
        window(*day(17), 3, academic_year="2025-2026"), window(*day(17), 3, academic_term="spring"),
        window(*day(17), 3, registration_kind="draft_preparation"))
    cases["reopening_without_class_window"] = with_windows(window(*day(19), None))
    cases["window_with_bare_fields"] = with_windows(
        {"start_at": day(17)[0], "end_at": day(17)[1], "category": "registration", "scope": "undergraduate",
         "class_levels": [3], "registration_kind": "class_window", "academic_term": "fall",
         "academic_year": "2026-2027"})

    # Several windows for one class level.
    cases["two_upcoming_windows_takes_the_nearest"] = with_windows(window(*day(20), 3), window(*day(17), 3))
    cases["two_upcoming_windows_same_opening_differ_in_closing"] = with_windows(
        window(*day(17, "10:00", "13:00"), 3), window(*day(17, "10:00", "15:00"), 3))
    cases["two_identical_upcoming_windows"] = with_windows(
        window(*day(17), 3, description="first"), window(*day(17), 3, description="second"))
    cases["ongoing_and_upcoming_window"] = with_windows(
        window(*day(20), 3), window(*day(16), 3), now=at("2026-09-16T11:00:00+03:00"))
    cases["two_ongoing_windows"] = with_windows(
        window(*day(16, "09:00", "13:00"), 3), window(*day(16, "10:00", "14:00"), 3), now=at("2026-09-16T11:00:00+03:00"))
    cases["two_identical_ongoing_windows"] = with_windows(
        window(*day(16), 3), window(*day(16), 3), now=at("2026-09-16T11:00:00+03:00"))
    cases["two_past_windows_takes_the_latest"] = with_windows(
        window(*day(10), 3), window(*day(14), 3), window(*day(12), 3))
    cases["two_past_windows_same_closing_differ_in_opening"] = with_windows(
        window(*day(14, "09:00", "13:00"), 3), window(*day(14, "10:00", "13:00"), 3))
    cases["past_and_upcoming_window"] = with_windows(window(*day(10), 3), window(*day(20), 3))
    cases["several_reopenings"] = with_windows(
        window(*day(17), 3), window(*day(19), None), window(*day(25), None), window(*day(10), None))
    cases["ambiguous_reopenings_carry_a_reason"] = with_windows(
        window(*day(17), 3), window(*day(19, "10:00", "13:00"), None), window(*day(19, "10:00", "14:00"), None))

    # Malformed windows.
    cases["window_without_timezone"] = with_windows(window("2026-09-17T10:00:00", "2026-09-17T13:00:00", 3))
    cases["window_closing_without_timezone"] = with_windows(window("2026-09-17T10:00:00+03:00", "2026-09-17T13:00:00", 3))
    cases["window_closing_before_opening"] = with_windows(window(day(17)[1], day(17)[0], 3))
    cases["window_of_zero_length"] = with_windows(window(day(17)[0], day(17)[0], 3))
    cases["window_without_times"] = with_windows(
        {key: value for key, value in window(*day(17), 3).items() if key not in ("start_at", "end_at")})
    cases["window_times_null"] = with_windows(window(None, None, 3))
    cases["window_times_not_text"] = with_windows(window(20260917, 20260917, 3))
    cases["window_times_unreadable"] = with_windows(window("17 September 10:00", "17 September 13:00", 3))
    cases["malformed_window_beside_a_good_one"] = with_windows(window(None, None, 3), window(*day(18), 3))
    cases["window_in_another_utc_offset"] = with_windows(
        window("2026-09-17T07:00:00+00:00", "2026-09-17T10:00:00+00:00", 3))

    # Rows with no fields at all. An empty status row is still a program; an
    # empty lesson row is not evidence of one and is related to nothing.
    cases["university_overall_with_empty_lesson_row"] = case(status_payload(overall_program()), lesson_payload({}))
    cases["university_overall_active_with_empty_lesson_row"] = case(
        status_payload(overall_program(durum="Aktif")), lesson_payload({}))
    cases["empty_status_row"] = case(status_payload({}), lesson_payload(lesson_program()))
    cases["empty_rows_on_both_endpoints"] = case(status_payload({}, {}), lesson_payload({}))

    cases["now_without_timezone"] = case(now=datetime(2026, 9, 16, 12, 0))
    return cases


def run(case_: dict[str, Any]) -> dict[str, Any]:
    """Summary, default reducer and term history for one scenario."""
    registration, lesson = deepcopy(case_["registration"]), deepcopy(case_["lesson"])

    def summarize() -> dict[str, Any]:
        return summarize_registration_status(
            registration, lesson, deepcopy(case_["calendar"]),
            now=case_["now"], calendar_error=case_["calendar_error"])

    recorded = {"summary": golden.outcome(summarize)}
    # The inputs must come back untouched.
    recorded["inputs_unchanged"] = registration == case_["registration"] and lesson == case_["lesson"]
    if "returned" in recorded["summary"]:
        summary = recorded["summary"]["returned"]
        untouched = deepcopy(summary)
        recorded["compact"] = golden.outcome(lambda: compact_registration_summary(summary, registration))
        recorded["summary_unchanged_by_compact"] = summary == untouched
    recorded["term_history"] = golden.outcome(lambda: term_history(registration))
    return recorded


def many_terms(count: int) -> list[Any]:
    rows: list[Any] = [
        term(f"{2000 + index // 2}{10 + 10 * (index % 2)}", f"Term {index}", donemlikNotOrtalamasi=index / 10,
             genelNotOrtalamasi=None, verilenKredi=index, sinifSeviye="")
        for index in range(count)
    ]
    return [*rows, "not a row", term(None, akademikDonemAdiEN="Nameless"), term("209910")]


def reducer_scenarios() -> dict[str, Callable[[], Any]]:
    """Shapes of ``compact_registration_summary`` no summary of a real payload has."""
    long_history = status_payload(status_program(kayitDurumuDonemList=many_terms(34)), identity("Second"))
    base = summarize_registration_status(
        status_payload(status_program()), lesson_payload(lesson_program()), DEFAULT_CALENDAR, now=NOW)

    def program(**fields: Any) -> dict[str, Any]:
        return {**deepcopy(base["per_program"][0]), **fields}

    odd_programs = [
        program(program_name="", program_name_en="Only English", registration_permission="open",
                registration_window="closed", all_students_reopening=None, latest_academic_term=None,
                course_registration_term="2026", last_regular_term="none", last_regular_term_note="kept"),
        program(enrollment_status_evidence=[{"value": "Aktif"}, {"value": " Aktif "}, {"value": None}],
                last_regular_term={"code": None, "name": "", "name_en": "Spring", "source_url": "x"}),
        program(enrollment_status_evidence=[{"value": "Aktif"}, {"value": "Pasif"}],
                last_regular_term={"code": "202610", "name": "Güz", "name_en": "Fall", "gpa": 2.0, "source_url": "x"}),
        program(enrollment_status_evidence=None, last_regular_term=None),
    ]
    return {
        "history_longer_than_the_limit": lambda: compact_registration_summary(
            summarize_registration_status(long_history, None, None, now=NOW), long_history),
        "term_history_of_long_payload": lambda: term_history(long_history),
        "more_programs_than_history_rows": lambda: compact_registration_summary(
            {**base, "per_program": odd_programs}, status_payload(status_program())),
        "odd_program_shapes_without_payload": lambda: compact_registration_summary(
            {**base, "per_program": odd_programs}),
        "summary_without_programs": lambda: compact_registration_summary({"selection_notice": "x", "errors": ["e"]}),
        "summary_with_null_programs": lambda: compact_registration_summary({"per_program": None}, {"statusCode": 3}),
        "term_history_of_failed_payload": lambda: term_history({"statusCode": 1, "kayitDurumuList": []}),
        "term_history_of_nothing": lambda: term_history(None),
    }


class RegistrationStatusCharacterizationTests(unittest.TestCase):
    maxDiff = None
    recorded: dict[str, Any]
    actual: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.actual = {name: run(case_) for name, case_ in scenarios().items()}
        cls.actual.update({f"reducer:{name}": golden.outcome(call) for name, call in reducer_scenarios().items()})
        if golden.updating():
            golden.save(GOLDEN_FILE, cls.actual)
        cls.recorded = golden.load(GOLDEN_FILE)

    def test_every_scenario_is_recorded(self) -> None:
        self.assertEqual(list(self.recorded), list(self.actual))
        self.assertGreater(len(self.actual), 100)

    def test_outcomes_match_the_recording(self) -> None:
        for name, actual in self.actual.items():
            with self.subTest(scenario=name):
                self.assertEqual(golden.serialize(actual), golden.serialize(self.recorded.get(name)))

    def test_inputs_are_never_modified(self) -> None:
        for name, actual in self.actual.items():
            if name.startswith("reducer:"):
                continue
            with self.subTest(scenario=name):
                self.assertTrue(actual["inputs_unchanged"])
                self.assertTrue(actual.get("summary_unchanged_by_compact", True))

    def test_the_scenarios_reach_every_window_reason_and_state(self) -> None:
        reasons, states, methods, warnings = set(), set(), set(), set()
        for name, actual in self.actual.items():
            if name.startswith("reducer:"):
                continue
            for row in actual["summary"].get("returned", {}).get("per_program", []):
                reasons.add(row["registration_window_unavailable_reason"])
                states.add((row["registration_window"] or {}).get("state"))
                methods.add(row["program_match_method"])
                warnings.update(row["warnings"])
        self.assertEqual(states, {None, "upcoming", "ongoing", "past"})
        self.assertEqual(methods, {None, "exact_program_labels", "sole_program_with_university_overall_status"})
        self.assertEqual(reasons, {
            None,
            "The program is inactive or its active status is not established.",
            "An undergraduate class window is not applicable to this program type.",
            "The latest official class level is unavailable.",
            "The course-registration term cannot be matched to an explicit academic year and season.",
            "The official calendar could not be read.",
            "No matching event with explicit opening and closing times is available in the fetched calendar.",
            "Multiple matching calendar windows are ambiguous.",
        })
        self.assertEqual(warnings, {
            "No term records were provided.",
            "Term records contain an unknown term identity.",
            "The latest term has conflicting records.",
            "The corresponding course-registration program is missing or ambiguous.",
        })


class DefaultClockTests(unittest.TestCase):
    """Without ``now`` the summary reads the clock in the calendar's timezone."""

    def test_default_now_is_the_module_clock_in_istanbul_time(self) -> None:
        asked: list[Any] = []

        class FrozenClock(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
                asked.append(tz)
                return datetime(2026, 9, 17, 15, 0, tzinfo=tz)

        with patch.object(registration_status, "datetime", FrozenClock):
            result = summarize_registration_status(
                status_payload(status_program()), lesson_payload(lesson_program()), DEFAULT_CALENDAR)
        self.assertEqual([str(zone) for zone in asked], ["Europe/Istanbul"])
        self.assertEqual(result["checked_at"], "2026-09-17T15:00:00+03:00")
        self.assertEqual(result["per_program"][0]["registration_window"]["state"], "ongoing")
        pinned = summarize_registration_status(
            status_payload(status_program()), lesson_payload(lesson_program()), DEFAULT_CALENDAR,
            now=datetime.fromisoformat("2026-09-17T15:00:00+03:00"))
        self.assertEqual(golden.serialize(result), golden.serialize(pinned))


if __name__ == "__main__":
    unittest.main()
