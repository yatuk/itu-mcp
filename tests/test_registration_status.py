from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import requests

from ninova_mcp.parsing import extract_academic_calendar
from ninova_mcp.registration_draft import DRAFT_PATH, read_registration_draft
from ninova_mcp.registration_status import summarize_registration_status
from ninova_mcp.server import NinovaMcpApp

NOW = datetime.fromisoformat("2026-09-16T12:00:00+03:00")
CALENDAR = extract_academic_calendar((Path(__file__).parent / "fixtures/academic_calendar_month_public.html").read_text(encoding="utf-8"), "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/EN/academic-calendar/index.php")


def records(name="Example Undergraduate Program", level=3, status="Aktif"):
    # Entirely synthetic private-record shapes. Dates match the public fixture.
    common = {"akademikProgramAdi": name, "akademikProgramAdiEN": name,
        "akademikBolumAdi": "Example Department", "fakulteAdi": "Example Faculty", "durum": status, "durumKodu": "EXAMPLE"}
    registration = {"statusCode": 0, "kayitDurumuList": [{**common, "kayitDurumuDonemList": [
        {"akademikDonemKodu": "202620", "akademikDonemAdi": "2025-2026 Bahar", "sinifSeviye": f"{level}. Sınıf", "donemlikNotOrtalamasi": 2.5, "genelNotOrtalamasi": 2.4, "verilenKredi": 12.0},
        {"akademikDonemKodu": "202510", "akademikDonemAdi": "2024-2025 Güz", "sinifSeviye": "1. Sınıf", "donemlikNotOrtalamasi": 2.0},
    ]}]}
    lesson = {"statusCode": 0, "dersKayitDurumuList": [{**common, "dersKayitDurumuDonemList": [
        {"akademikDonemKodu": "202710", "akademikDonemAdi": "2026-2027 Güz", "dersKayitDurumu": "Kayıt Olabilir"},
        {"akademikDonemKodu": "202620", "akademikDonemAdi": "2025-2026 Bahar", "dersKayitDurumu": "Kayıt Olabilir"},
    ]}]}
    return registration, lesson


class RegistrationStatusTests(unittest.TestCase):
    def test_university_overall_record_can_only_relate_to_one_named_program(self):
        registration, lesson = records()
        aggregate = registration['kayitDurumuList'][0]
        aggregate.update(akademikProgramAdi='', akademikProgramAdiEN='', fakulteAdi='',
                         akademikBolumAdi='Üniversite Geneli', akademikBolumAdiEN='University Overall', durum='')
        row = summarize_registration_status(registration, lesson, CALENDAR, now=NOW)['per_program'][0]
        self.assertEqual(row['program_match_method'], 'sole_program_with_university_overall_status')
        self.assertEqual(row['academic_status_scope'], 'university_overall')
        self.assertEqual(row['registration_window']['class_levels'], [3])
        self.assertFalse(row['registration_window']['personal_eligibility_verified'])
        _, other = records('Another Program')
        lesson['dersKayitDurumuList'].extend(other['dersKayitDurumuList'])
        row = summarize_registration_status(registration, lesson, CALENDAR, now=NOW)['per_program'][0]
        self.assertIsNone(row['program_match_method'])
        self.assertIsNone(row['registration_window'])

    def test_blank_status_uses_explicit_activity_from_exact_matching_program(self):
        registration, lesson = records()
        registration['kayitDurumuList'][0]['durum'] = ''
        registration['kayitDurumuList'][0]['durumKodu'] = 'AS'
        row = summarize_registration_status(registration, lesson, CALENDAR, now=NOW)['per_program'][0]
        self.assertTrue(row['active'])
        self.assertEqual(row['registration_window']['class_levels'], [3])
        self.assertTrue(row['enrollment_status_evidence'][1]['source_url'].endswith('/DersKayitDurumu'))
        lesson['dersKayitDurumuList'][0]['durum'] = ''
        self.assertIsNone(summarize_registration_status(registration, lesson, CALENDAR, now=NOW)['per_program'][0]['active'])

    def test_conflicting_activity_does_not_choose_one_source(self):
        registration, lesson = records()
        lesson['dersKayitDurumuList'][0]['durum'] = 'Pasif'
        result = summarize_registration_status(registration, lesson, CALENDAR, now=NOW)
        self.assertIsNone(result['per_program'][0]['active'])
        self.assertIsNone(result['per_program'][0]['registration_window'])
        self.assertIsNone(result['selected_program_index'])

    def test_latest_official_class_and_term_are_order_independent_and_sourced(self):
        registration, lesson = records()
        result = summarize_registration_status(registration, lesson, CALENDAR, now=NOW)
        row = result["per_program"][0]
        self.assertEqual(result["selected_program_index"], 0)
        self.assertEqual(row["class_level"], 3)
        self.assertEqual(row["class_level_source"]["term"]["code"], "202620")
        self.assertEqual(row["course_registration_term"]["code"], "202710")
        self.assertEqual(row["official_term_gpa"], 2.5)
        self.assertEqual(row["reported_term_credit"], {"value": 12.0, "source_field": "verilenKredi"})
        window = row["registration_window"]
        self.assertEqual(window["start_at"], "2026-09-17T14:00:00+03:00")
        self.assertEqual(window["end_at"], "2026-09-17T17:00:00+03:00")
        self.assertEqual(window["class_levels"], [3])
        self.assertEqual(window["state"], "upcoming")
        self.assertEqual(window["source"], "class_calendar")
        self.assertFalse(window["personal_eligibility_verified"])
        self.assertEqual(row["all_students_reopening"]["start_at"], "2026-09-19T12:00:00+03:00")
        for field in ("max_credit", "blockers", "academic_standing"):
            self.assertIsNone(row[field])
            self.assertTrue(row[field + "_unavailable_reason"])

    def test_open_and_expired_windows_are_labeled(self):
        registration, lesson = records()
        for at, expected in [("2026-09-17T14:30:00+03:00", "ongoing"), ("2026-09-17T17:00:00+03:00", "past")]:
            with self.subTest(at=at):
                row = summarize_registration_status(registration, lesson, CALENDAR, now=datetime.fromisoformat(at))["per_program"][0]
                self.assertEqual(row["registration_window"]["state"], expected)

    def test_multiple_programs_are_matched_by_identity_without_selecting_first(self):
        registration, lesson = records("Program A", 3)
        other_registration, other_lesson = records("Program B", 2)
        registration["kayitDurumuList"].extend(other_registration["kayitDurumuList"])
        lesson["dersKayitDurumuList"] = other_lesson["dersKayitDurumuList"] + lesson["dersKayitDurumuList"]
        result = summarize_registration_status(registration, lesson, CALENDAR, now=NOW)
        self.assertIsNone(result["selected_program_index"])
        self.assertEqual([row["registration_window"]["class_levels"] for row in result["per_program"]], [[3], [2]])

    def test_ambiguous_program_match_does_not_borrow_permission(self):
        registration, lesson = records()
        lesson["dersKayitDurumuList"].append(deepcopy(lesson["dersKayitDurumuList"][0]))
        row = summarize_registration_status(registration, lesson, CALENDAR, now=NOW)["per_program"][0]
        self.assertIsNone(row["course_registration_term"])
        self.assertIsNone(row["registration_window"])
        self.assertTrue(row["warnings"])

    def test_unknown_or_inactive_program_is_not_assigned_a_window(self):
        for status in ("Unrecognized activity", "Pasif", "Mezun"):
            with self.subTest(status=status):
                registration, lesson = records(status=status)
                result = summarize_registration_status(registration, lesson, CALENDAR, now=NOW)
                self.assertIsNone(result["selected_program_index"])
                self.assertIsNone(result["per_program"][0]["registration_window"])

    def test_graduate_program_cannot_borrow_undergraduate_class_window(self):
        registration, lesson = records("Example Yüksek Lisans Programı", 1)
        row = summarize_registration_status(registration, lesson, CALENDAR, now=NOW)["per_program"][0]
        self.assertIsNone(row["registration_window"])
        self.assertIn("program type", row["registration_window_unavailable_reason"])

    def test_missing_or_wrong_term_does_not_match_another_year(self):
        for name in ("Unknown Term", "2027-2028 Güz", "2026-2027 Bahar"):
            with self.subTest(name=name):
                registration, lesson = records()
                lesson["dersKayitDurumuList"][0]["dersKayitDurumuDonemList"][0]["akademikDonemAdi"] = name
                row = summarize_registration_status(registration, lesson, CALENDAR, now=NOW)["per_program"][0]
                self.assertIsNone(row["registration_window"])

    def test_conflicting_latest_class_records_are_unknown(self):
        registration, lesson = records()
        rows = registration["kayitDurumuList"][0]["kayitDurumuDonemList"]
        duplicate = {**rows[0], "sinifSeviye": "2. Sınıf"}
        rows.append(duplicate)
        row = summarize_registration_status(registration, lesson, CALENDAR, now=NOW)["per_program"][0]
        self.assertIsNone(row["class_level"])
        self.assertIsNone(row["registration_window"])

    def test_draft_and_other_class_windows_cannot_replace_missing_target(self):
        registration, lesson = records()
        calendar = deepcopy(CALENDAR)
        for event in calendar["events"]:
            if event.get("class_levels") == [3]:
                event["registration_kind"] = "draft_preparation"
        row = summarize_registration_status(registration, lesson, calendar, now=NOW)["per_program"][0]
        self.assertIsNone(row["registration_window"])
        self.assertIsNotNone(row["all_students_reopening"])

    def test_method_preserves_raw_status_on_public_calendar_failure(self):
        registration, lesson = records()
        obs = Mock(spec=["get_registration_status", "get_lesson_registration_status"])
        obs.get_registration_status.return_value = registration
        obs.get_lesson_registration_status.return_value = lesson
        public = Mock(spec=["get_academic_calendar"])
        public.get_academic_calendar.side_effect = requests.Timeout("Synthetic failure")
        result = NinovaMcpApp.obs_get_registration_status(SimpleNamespace(obs=obs, obs_public=public))
        self.assertIs(result["kayit_durumu"], registration)
        self.assertIs(result["ders_kayit_durumu"], lesson)
        self.assertIsNone(result["summary"]["per_program"][0]["registration_window"])
        self.assertTrue(result["summary"]["errors"])
        obs.get_registration_status.assert_called_once_with()
        obs.get_lesson_registration_status.assert_called_once_with()
        public.get_academic_calendar.assert_called_once_with()

    def test_business_failure_never_becomes_a_successful_empty_program_list(self):
        result = summarize_registration_status({"statusCode": 1, "kayitDurumuList": []}, {"statusCode": 1}, None, now=NOW)
        self.assertEqual(result["per_program"], [])
        self.assertTrue(result["errors"])
        self.assertIsNone(result["selected_program_index"])

    def test_draft_timestamps_are_explicitly_draft_preparation(self):
        period = {"akademikDonemKodu": "203410", "baslangicTarihi": "2033-09-10T12:00:00", "bitisTarihi": "2033-09-17T17:00:00", "sinif": 2}
        obs = Mock(spec=["api_get", "base_url"])
        obs.base_url = "https://obs.itu.edu.tr"
        obs.api_get.side_effect = [{"statusCode": 0, "kayitZamanKontrolResult": period}, {"statusCode": 0, "taslakBilgi": None}]
        result = read_registration_draft(obs)
        self.assertEqual(result["registration_window"], result["draft_preparation_window"])
        self.assertEqual(result["registration_window_kind"], "draft_preparation")
        self.assertIn("not a personal", result["registration_window_notice"])
        self.assertEqual(result["draft_preparation_window_source"], obs.base_url + DRAFT_PATH + "KayitZamaniKontrolu")
        self.assertEqual(obs.api_get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
