from __future__ import annotations

from pathlib import Path
import unittest

from ninova_mcp.academic_calendar import parse_event_dates
from ninova_mcp.parsing import extract_academic_calendar
from ninova_mcp.planning import filter_academic_calendar

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/EN/academic-calendar/index.php"


class CalendarDateTests(unittest.TestCase):
    def test_future_start_is_not_declared_current_semester(self):
        result = extract_academic_calendar(
            '<table><tr><td><b>21 September 2035: Beginning of fall term</b></td></tr></table>', SOURCE)
        self.assertEqual(len(result['semesters']), 1)
        self.assertIsNone(result['current_semester'])
        self.assertEqual(result['current_semester_status'], 'not_explicitly_identified_by_source')

    def test_explicit_time_ranges_and_shared_components(self):
        cases = [
            ("17 September 10:00 - 17 September 2026 13:00", "2026-09-17T10:00:00+03:00", "2026-09-17T13:00:00+03:00"),
            ("28 September 10:00 - 02 October 2026 17:00", "2026-09-28T10:00:00+03:00", "2026-10-02T17:00:00+03:00"),
            ("17 Eylül 2026 Saat 10:00-13:00", "2026-09-17T10:00:00+03:00", "2026-09-17T13:00:00+03:00"),
            ("Kayıt Başlangıç: 15 Eylül 2026 Saat:14:00 Kayıt Kapanış: 16 Eylül 2026 Saat:17:00", "2026-09-15T14:00:00+03:00", "2026-09-16T17:00:00+03:00"),
            ("28 Eylül 2026 Saat: 10:00 Başlangıç 09 Ekim 2026 Saat: 17:00 Bitiş", "2026-09-28T10:00:00+03:00", "2026-10-09T17:00:00+03:00"),
            ("31 December 10:00 - 02 January 2027 17:00", "2026-12-31T10:00:00+03:00", "2027-01-02T17:00:00+03:00"),
            ("31 December 2026 10:00 - 02 January 17:00", "2026-12-31T10:00:00+03:00", "2027-01-02T17:00:00+03:00"),
            ("16 September 2026-10:00", "2026-09-16T10:00:00+03:00", None),
        ]
        for text, start, end in cases:
            with self.subTest(text=text):
                parsed = parse_event_dates(text)
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed["start_at"], start)
                self.assertEqual(parsed["end_at"], end)
                self.assertEqual(parsed["timezone"], "Europe/Istanbul")

    def test_all_day_ranges_do_not_invent_times(self):
        for raw, start, end in [
            ("07 - 11 September 2026", "2026-09-07", "2026-09-11"),
            ("20 August - 02 September 2026", "2026-08-20", "2026-09-02"),
            ("31 December - 02 January 2027", "2026-12-31", "2027-01-02"),
            ("17 Eylül 2026", "2026-09-17", "2026-09-17"),
        ]:
            with self.subTest(raw=raw):
                parsed = parse_event_dates(raw)
                self.assertEqual((parsed["start_date"], parsed["end_date"]), (start, end))
                self.assertIsNone(parsed["start_at"])
                self.assertIsNone(parsed["end_at"])

    def test_invalid_dates_times_and_missing_year_are_not_guessed(self):
        for raw in ["31 February 2026", "17 September 2026 25:00", "17 September 2026 14:00-10:00", "17 September 2026 - 18 Smarch 2026", "17 September", "17 September 2026 9am"]:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_event_dates(raw))
        self.assertEqual(parse_event_dates("17 September", default_year=2028)["start_date"], "2028-09-17")


class CalendarSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calendar = extract_academic_calendar((FIXTURES / "academic_calendar_month_public.html").read_text(), SOURCE)
        cls.registration = extract_academic_calendar((FIXTURES / "academic_calendar_registration_public.html").read_text(), "https://www.sis.itu.edu.tr/registration-fixture")

    def test_real_month_has_four_distinct_class_windows_and_reopening(self):
        windows = [x for x in self.calendar["events"] if x.get("registration_kind") == "class_window"]
        self.assertEqual(len(windows), 4)
        expected = {4: ("17", "10:00", "13:00"), 3: ("17", "14:00", "17:00"), 2: ("18", "10:00", "13:00"), 1: ("18", "14:00", "17:00")}
        for event in windows:
            level = event["class_levels"][0]
            day, start, end = expected[level]
            self.assertEqual(event["start_at"], f"2026-09-{day}T{start}:00+03:00")
            self.assertEqual(event["end_at"], f"2026-09-{day}T{end}:00+03:00")
            self.assertEqual(event["scope"], "undergraduate")
            self.assertEqual(event["academic_term"], "fall")
            self.assertEqual(event["academic_year"], "2026-2027")
            self.assertEqual(event["source_url"], SOURCE)
        reopening = [x for x in self.calendar["events"] if x.get("registration_kind") == "all_students_reopening"]
        self.assertEqual(len(reopening), 1)
        self.assertEqual(reopening[0]["class_levels"], [])
        self.assertEqual(reopening[0]["start_at"], "2026-09-19T12:00:00+03:00")

    def test_real_turkish_table_agrees_with_independent_english_source(self):
        self.assertEqual(self.registration["event_count"], 9)
        for event in self.registration["events"]:
            if event.get("registration_kind") != "class_window":
                continue
            equivalent = next(x for x in self.calendar["events"] if x["class_levels"] == event["class_levels"])
            self.assertEqual((event["start_at"], event["end_at"]), (equivalent["start_at"], equivalent["end_at"]))
            self.assertEqual(event["academic_year"], "2026-2027")
        late = next(x for x in self.registration["events"] if x.get("registration_kind") == "late_registration")
        self.assertEqual(late["end_at"], "2026-10-02T17:00:00+03:00")

    def test_bilingual_queries_and_registration_classification(self):
        for query in ("ders kayıt", "ders kaydı", "course registration"):
            with self.subTest(query=query):
                selected = filter_academic_calendar(self.calendar, query=query)
                self.assertTrue(any(x.get("class_levels") == [4] for x in selected["events"]))
        fourth = filter_academic_calendar(self.calendar, query="4. sınıf ders kayıt")
        self.assertEqual([x["class_levels"] for x in fourth["events"]], [[4]])
        category = filter_academic_calendar(self.calendar, category="registration")
        self.assertTrue(any(x.get("registration_kind") == "add_drop" for x in category["events"]))
        # Passing an English exam is a condition for this registration event,
        # not grounds to relabel the entire event as an exam.
        self.assertTrue(any(x.get("registration_kind") == "first_registration" for x in category["events"]))
        self.assertFalse(any(x.get("registration_kind") == "class_window" and x["scope"] != "undergraduate" for x in category["events"]))

    def test_date_intersections_keep_cross_month_events_and_warn_about_coverage(self):
        selected = filter_academic_calendar(self.calendar, date_from="2026-10-01", date_to="2026-10-02", category="registration")
        self.assertTrue(any(x["start_date"] == "2026-09-28" and x["end_date"] == "2026-10-02" for x in selected["events"]))
        self.assertIn("coverage_warning", selected)
        self.assertEqual(selected["coverage"], {"scope": "displayed_month", "start_date": "2026-09-01", "end_date": "2026-09-30", "complete": True})
        outside = filter_academic_calendar(self.calendar, date_from="2030-01-01", date_to="2030-01-03")
        self.assertEqual(outside["event_count"], 0)
        self.assertIn("coverage_warning", outside)
        self.assertIn("coverage_warning", filter_academic_calendar(self.calendar, date_from="2026-09-17"))
        self.assertNotIn("coverage_warning", filter_academic_calendar(self.calendar, date_from="2026-09-17", date_to="2026-09-18"))

    def test_deduplication_and_no_silent_100_event_cutoff(self):
        self.assertEqual(self.calendar["event_count"], 71)
        self.assertEqual(len(self.calendar["events"]), self.calendar["event_count"])
        html = "<table><tr><td>" + "".join(f"<b>17 September 2026 : Event {i}</b><br>" for i in range(125)) + "</td></tr></table>"
        parsed = extract_academic_calendar(html, SOURCE)
        self.assertEqual(parsed["event_count"], 125)
        self.assertEqual(len(parsed["events"]), 125)
        self.assertFalse(parsed["truncated"])

    def test_bad_event_or_unrecognized_page_is_never_a_confident_empty(self):
        html = '<select name="yil"><option selected value="2026"></option></select><select name="ay"><option selected value="9"></option></select><p class="auto"><b>17 September 2026 :</b> Good event<br><b>31 September 2026 :</b> Bad date<br><b>TBA :</b> Unknown date<br></p>'
        parsed = extract_academic_calendar(html, SOURCE)
        self.assertEqual(parsed["event_count"], 1)
        self.assertFalse(parsed["parse_complete"])
        self.assertFalse(parsed["coverage"]["complete"])
        self.assertEqual(parsed["unparsed_event_count"], 2)
        self.assertIn("parse_warning", parsed)
        empty = extract_academic_calendar("<html>Maintenance</html>", SOURCE)
        self.assertFalse(empty["parse_complete"])
        self.assertIn("parse_warning", empty)

    def test_draft_preparation_is_not_a_class_registration_window(self):
        html = '<p class="auto"><b><span>Undergraduate / Associate Level Academic Calendar</span></b><br><b>17 September 2026 :</b> 4th Class registration draft preparation<br></p>'
        parsed = extract_academic_calendar(html, SOURCE)
        self.assertEqual(parsed["events"][0]["registration_kind"], "draft_preparation")


if __name__ == "__main__":
    unittest.main()
