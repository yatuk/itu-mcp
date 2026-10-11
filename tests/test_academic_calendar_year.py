"""The academic-year list of the official calendar page, in both languages."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
import re
import unittest

import requests

from ninova_mcp import academic_calendar
from ninova_mcp.academic_calendar import parse_academic_calendar, parse_event_dates
from ninova_mcp.app_public import PublicCatalogToolsMixin
from ninova_mcp.obs_client import ObsError, ObsPublicClient
from ninova_mcp.planning import filter_academic_calendar

FIXTURES = Path(__file__).parent / "fixtures"
EN_URL = "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/EN/academic-calendar/index.php"
TR_URL = "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/TR/akademik-takvim/index.php"
YEAR_QUERY = "?akademikyil={}&takvimadi=&katagori=&baslangic=&bitis="
EN_HTML = (FIXTURES / "academic_calendar_year_en.html").read_text(encoding="utf-8")
TR_HTML = (FIXTURES / "academic_calendar_year_tr.html").read_text(encoding="utf-8")
TODAY = date(2026, 10, 11)


def without_year_list(html: str) -> str:
    """The same page as it was read before: month grid and upcoming list only."""
    return html[: html.index('<div id="results">')] + "</body></html>"


def previous_year(html: str) -> str:
    """A synthetic 2025-2026 page: the same rows one year earlier."""
    html = html.replace('value="854" selected', 'value="854"').replace('value="564"', 'value="564" selected')
    head, rows = html.split('<div id="results">')
    return head + '<div id="results">' + re.sub(r"\b(2026|2027)\b", lambda m: str(int(m[1]) - 1), rows)


def bilingual() -> dict[str, Any]:
    merged = academic_calendar.merge_calendar_languages(
        parse_academic_calendar(EN_HTML, EN_URL), parse_academic_calendar(TR_HTML, TR_URL))
    return academic_calendar.combine_calendars([merged], today=TODAY)


def response(url: str, status: int, body: str) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = body.encode("utf-8")
    result.encoding = "utf-8"
    return result


class FakeSession:
    def __init__(self, routes: dict[str, str]) -> None:
        self.routes = routes
        self.headers: dict[str, str] = {}
        self.requested: list[str] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.requested.append(url)
        body = self.routes.get(url)
        return response(url, 404, "not found") if body is None else response(url, 200, body)


def make_client(routes: dict[str, str] | None = None) -> tuple[ObsPublicClient, FakeSession]:
    session = FakeSession({EN_URL: EN_HTML, TR_URL: TR_HTML} if routes is None else routes)
    client = ObsPublicClient(session=session, cache_ttl=60)  # type: ignore[arg-type]
    client._min_request_interval = 0
    return client, session


class StubApp(PublicCatalogToolsMixin):
    """The public mixin alone: no login, no other client."""

    def __init__(self, client: ObsPublicClient) -> None:
        self.obs_public = client


class YearListParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.english = parse_academic_calendar(EN_HTML, EN_URL)
        cls.calendar = bilingual()

    def event(self, uid: str) -> dict[str, Any]:
        return next(e for e in self.calendar["events"] if e["uid"] == uid)

    def test_each_event_is_returned_once_without_entities(self) -> None:
        events = self.english["events"]
        self.assertEqual(len(events), 86)
        self.assertEqual(len({e["uid"] for e in events}), 86)
        withdrawals = [e for e in events if e["start_date"] == "2026-10-12" and "Withdrawal" in e["description"]]
        self.assertEqual(len(withdrawals), 1)
        self.assertEqual(withdrawals[0]["description"], "Withdrawal Period (will be indicated with a ‘T’ in the transcript)")
        self.assertEqual(withdrawals[0]["academic_term"], "fall")
        for event in events:
            self.assertIsNone(re.search(r"&[a-zA-Z]+;", event["description"]), event["description"])
            self.assertNotIn("(", event["date"])

    def test_month_grid_and_upcoming_list_do_not_duplicate_each_other(self) -> None:
        legacy = parse_academic_calendar(without_year_list(EN_HTML), EN_URL)
        withdrawals = [e for e in legacy["events"] if "Withdrawal" in e["description"] and e["scope"] == "undergraduate"]
        self.assertEqual(len(withdrawals), 1)
        self.assertEqual(withdrawals[0]["description"], "Withdrawal Period (will be indicated with a ‘T’ in the transcript)")
        self.assertEqual(withdrawals[0]["academic_term"], "fall")
        self.assertFalse(any("&lpar;" in e["description"] or "&rpar;" in e["description"] for e in legacy["events"]))
        keys = [(e["description"], e["start_date"], e["end_date"], e["scope"], e["source_calendar"]) for e in legacy["events"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_year_list_covers_the_academic_year(self) -> None:
        coverage = self.english["coverage"]
        self.assertEqual(coverage["scope"], "academic_year")
        self.assertEqual(coverage["academic_years"], ["2026-2027"])
        self.assertLessEqual(coverage["start_date"], "2026-07-09")
        self.assertGreaterEqual(coverage["end_date"], "2027-08-23")
        self.assertTrue(self.english["parse_complete"])
        self.assertEqual(self.english["academic_year"], "2026-2027")
        self.assertIn({"id": "564", "academic_year": "2025-2026"}, self.english["available_academic_years"])

    def test_finals_outside_the_displayed_month_are_found(self) -> None:
        selected = filter_academic_calendar(self.calendar, date_from="2026-11-01", date_to="2027-01-31", category="exam")
        self.assertNotIn("coverage_warning", selected)
        finals = [e for e in selected["events"] if e["description"] == "Final Exams for Fall Term"]
        self.assertEqual([(e["start_date"], e["end_date"], e["scope"]) for e in finals], [("2027-01-02", "2027-01-17", "undergraduate")])
        self.assertTrue(any("Make-up Exams" in e["description"] for e in selected["events"]))

    def test_turkish_and_english_descriptions_are_paired_by_site_id(self) -> None:
        self.assertEqual(self.calendar["languages"], ["en", "tr"])
        self.assertNotIn("bilingual_unmatched_count", self.calendar)
        withdrawal = self.event("1609")
        self.assertEqual(withdrawal["description"], "Withdrawal Period (will be indicated with a ‘T’ in the transcript)")
        self.assertEqual(withdrawal["description_tr"], "Bir Dersten Tümüyle Çekilme (Öğrenim Belgesinde (T) Harfi ile Gösterilecektir.)")
        self.assertTrue(all(e.get("description_tr") for e in self.calendar["events"]))

    def test_turkish_text_is_not_attached_when_dates_disagree(self) -> None:
        moved = TR_HTML.replace("12 Ekim 10:00 - 16 Ekim 2026 17:00", "13 Ekim 10:00 - 16 Ekim 2026 17:00")
        merged = academic_calendar.merge_calendar_languages(
            parse_academic_calendar(EN_HTML, EN_URL), parse_academic_calendar(moved, TR_URL))
        self.assertNotIn("description_tr", next(e for e in merged["events"] if e["uid"] == "1609"))
        self.assertEqual(merged["bilingual_unmatched_count"], 1)

    def test_turkish_queries_match_accent_and_case_insensitively(self) -> None:
        cases = {
            "dersten çekilme": "1609", "cekilme": "1609", "DERSTEN ÇEKİLME": "1609", "withdraw": "1609",
            "ekle bırak": "1603", "ekle-sil": "1603", "add drop": "1603",
            "güz finalleri": "1616", "final exams": "1616", "yarıyıl sonu sınavları": "1616",
            "bütünleme": "1620", "mazeret sınavı": "1620", "ara tatil": "1743", "güz yarıyılı başlangıcı": "1601",
        }
        for query, uid in cases.items():
            with self.subTest(query=query):
                selected = filter_academic_calendar(self.calendar, query=query)
                self.assertIn(uid, [e["uid"] for e in selected["events"]])
        self.assertEqual(filter_academic_calendar(self.calendar, query="olmayan etkinlik")["event_count"], 0)

    def test_categories_follow_the_real_event_names(self) -> None:
        expected = {
            "1609": ("registration", "withdrawal"), "1603": ("registration", "add_drop"),
            "1602": ("registration", "late_registration"), "1594": ("registration", "class_window"),
            "1616": ("exam", None), "1620": ("exam", None), "1621": ("exam", None), "1618": ("exam", None),
            "1743": ("holiday", None), "1641": ("holiday", None),
            "1601": ("semester", None), "1615": ("semester", None), "1645": ("semester", None),
            "1586": ("other", None), "1612": ("other", None),
        }
        for uid, (category, kind) in expected.items():
            with self.subTest(uid=uid, description=self.event(uid)["description"]):
                self.assertEqual(self.event(uid)["category"], category)
                self.assertEqual(self.event(uid).get("registration_kind"), kind)
        self.assertEqual(self.event("1609")["source_category"], "Course Registrations")
        self.assertEqual(self.event("1645")["academic_term"], "spring")
        self.assertEqual(self.event("1625")["academic_term"], "spring")

    def test_class_registration_windows_still_carry_times_and_levels(self) -> None:
        fall = [e for e in self.calendar["events"] if e.get("registration_kind") == "class_window" and e["academic_term"] == "fall"]
        self.assertEqual(sorted(e["class_levels"][0] for e in fall), [1, 2, 3, 4])
        fourth = next(e for e in fall if e["class_levels"] == [4])
        self.assertEqual((fourth["start_at"], fourth["end_at"]), ("2026-09-17T10:00:00+03:00", "2026-09-17T13:00:00+03:00"))
        self.assertEqual((fourth["scope"], fourth["academic_year"]), ("undergraduate", "2026-2027"))

    def test_terms_state_first_and_last_day_of_classes(self) -> None:
        terms = {(t["academic_year"], t["term"], t["scope"]): (t["classes_start"], t["classes_end"]) for t in self.calendar["terms"]}
        self.assertEqual(terms[("2026-2027", "fall", "undergraduate")], ("2026-09-28", "2026-12-31"))
        # The English page misspells this row "End of Sping Term".
        self.assertEqual(terms[("2026-2027", "spring", "undergraduate")], ("2027-02-01", "2027-04-30"))
        self.assertEqual(terms[("2026-2027", "fall", "graduate")], ("2026-09-28", "2026-12-31"))
        self.assertEqual(terms[("2026-2027", "summer", "undergraduate")], ("2027-07-05", "2027-08-13"))
        self.assertEqual(terms[("2026-2027", "summer", "graduate")], ("2027-07-05", "2027-08-13"))
        # Preparatory and evening programmes have their own terms and are not mixed in.
        self.assertEqual(len(terms), 6)
        self.assertEqual(self.calendar["semesters"][0], {"semester": "Fall (Güz)", "academic_year": "2026-2027", "start": "2026-09-28", "end": "2026-12-31"})

    def test_current_semester_only_while_classes_are_in_session(self) -> None:
        current = self.calendar["current_semester"]
        self.assertEqual((current["term"], current["classes_start"], current["classes_end"]), ("fall", "2026-09-28", "2026-12-31"))
        self.assertEqual(self.calendar["current_semester_status"], "derived_from_term_start_and_end_dates")
        exams = academic_calendar.finalize_calendar(dict(self.calendar), today=date(2027, 1, 10))
        self.assertIsNone(exams["current_semester"])
        self.assertEqual(exams["current_semester_status"], "not_explicitly_identified_by_source")

    def test_conflicting_term_rows_leave_the_date_empty(self) -> None:
        events = [dict(e) for e in self.calendar["events"]]
        clash = dict(next(e for e in events if e["uid"] == "1601"), uid="9999", start_date="2026-09-21", end_date="2026-09-21")
        terms = academic_calendar.build_terms([*events, clash])
        fall = next(t for t in terms if (t["term"], t["scope"]) == ("fall", "undergraduate"))
        self.assertEqual((fall["classes_start"], fall["classes_end"]), (None, "2026-12-31"))

    def test_shared_end_time_and_odd_date_forms(self) -> None:
        self.assertEqual(parse_event_dates("07 - 09 September 2026 23:59")["end_at"], "2026-09-09T23:59:00+03:00")
        self.assertIsNone(parse_event_dates("07 - 09 September 2026 23:59")["start_at"])
        self.assertEqual(parse_event_dates("28 - 28 Mayıs 2027 12:00")["end_at"], "2027-05-28T12:00:00+03:00")
        self.assertEqual(parse_event_dates("20 Ocak 2027-17:00")["start_at"], "2027-01-20T17:00:00+03:00")

    def test_empty_or_unrecognised_year_list_does_not_raise(self) -> None:
        empty = parse_academic_calendar('<div id="results"></div>', EN_URL)
        self.assertEqual(empty["event_count"], 0)
        self.assertFalse(empty["parse_complete"])
        self.assertIn("parse_warning", empty)
        self.assertEqual(empty["terms"], [])
        odd = parse_academic_calendar(
            '<div id="results"><div>TBA <br><b>No date yet</b><br><font size="2">A / B / C</font></div>'
            '<div><span>unexpected</span></div><div>05 October 2026 <br><b>Good</b></div></div>', EN_URL)
        self.assertEqual(odd["event_count"], 1)
        self.assertEqual(odd["unparsed_event_count"], 2)
        self.assertFalse(odd["parse_complete"])
        self.assertFalse(odd["coverage"]["complete"])


class CalendarClientTests(unittest.TestCase):
    def test_default_call_reads_both_languages_once_and_caches(self) -> None:
        client, session = make_client()
        calendar = client.get_academic_calendar()
        self.assertEqual(session.requested, [EN_URL, TR_URL])
        self.assertEqual(calendar["event_count"], 86)
        self.assertEqual(calendar["languages"], ["en", "tr"])
        self.assertEqual(calendar["coverage"]["academic_years"], ["2026-2027"])
        client.get_academic_calendar()
        client.get_academic_calendar(date_from="2026-11-01", date_to="2027-01-31")
        self.assertEqual(len(session.requested), 2)

    def test_dates_in_another_academic_year_fetch_that_year(self) -> None:
        old_en, old_tr = EN_URL + YEAR_QUERY.format("564"), TR_URL + YEAR_QUERY.format("564")
        client, session = make_client({EN_URL: EN_HTML, TR_URL: TR_HTML, old_en: previous_year(EN_HTML), old_tr: previous_year(TR_HTML)})
        calendar = client.get_academic_calendar(date_from="2025-11-01", date_to="2026-01-31")
        self.assertEqual(session.requested, [EN_URL, old_en, old_tr])
        self.assertEqual(calendar["coverage"]["academic_years"], ["2025-2026"])
        self.assertNotIn("coverage_warning", calendar)
        finals = filter_academic_calendar(calendar, date_from="2025-11-01", date_to="2026-01-31", category="exam")
        self.assertEqual([e["start_date"] for e in finals["events"] if e["description"] == "Final Exams for Fall Term"], ["2026-01-02"])
        self.assertEqual({e["academic_year"] for e in finals["events"]}, {"2025-2026"})
        self.assertTrue(all(urlhost(url) == "www.takvim.sis.itu.edu.tr" for url in session.requested))

    def test_request_count_is_capped_and_reported(self) -> None:
        old_en, old_tr = EN_URL + YEAR_QUERY.format("564"), TR_URL + YEAR_QUERY.format("564")
        client, session = make_client({EN_URL: EN_HTML, TR_URL: TR_HTML, old_en: previous_year(EN_HTML), old_tr: previous_year(TR_HTML)})
        calendar = client.get_academic_calendar(date_from="2023-10-01", date_to="2027-06-30")
        self.assertLessEqual(len(session.requested), 2 * ObsPublicClient.MAX_CALENDAR_ACADEMIC_YEARS)
        self.assertEqual(len(calendar["coverage"]["academic_years"]), ObsPublicClient.MAX_CALENDAR_ACADEMIC_YEARS)
        self.assertFalse(calendar["coverage"]["complete"])
        self.assertIn("at most", calendar["coverage_warning"])
        self.assertTrue(calendar["missing_academic_years"])

    def test_year_that_the_site_does_not_list_is_reported_not_faked(self) -> None:
        client, session = make_client()
        calendar = client.get_academic_calendar(date_from="2015-11-01", date_to="2015-12-31")
        self.assertEqual(session.requested, [EN_URL])
        self.assertEqual(calendar["event_count"], 0)
        self.assertEqual(calendar["missing_academic_years"], ["2015-2016"])
        self.assertIn("2015-2016", calendar["coverage_warning"])
        self.assertFalse(calendar["coverage"]["complete"])
        self.assertIn("parse_warning", calendar)

    def test_turkish_page_failure_keeps_the_english_calendar(self) -> None:
        client, _ = make_client({EN_URL: EN_HTML})
        calendar = client.get_academic_calendar()
        self.assertEqual(calendar["event_count"], 86)
        self.assertEqual(calendar["languages"], ["en"])
        self.assertIn("language_note", calendar)
        self.assertIn("1609", [e["uid"] for e in filter_academic_calendar(calendar, query="dersten çekilme")["events"]])

    def test_english_page_failure_is_an_error(self) -> None:
        client, _ = make_client({})
        with self.assertRaises(ObsError):
            client.get_academic_calendar()

    def test_page_without_year_list_is_returned_as_before(self) -> None:
        month = (FIXTURES / "academic_calendar_month_public.html").read_text(encoding="utf-8")
        client, session = make_client({EN_URL: month})
        calendar = client.get_academic_calendar(date_from="2026-11-01", date_to="2026-11-30")
        self.assertEqual(session.requested, [EN_URL])
        self.assertEqual(calendar["coverage"]["scope"], "displayed_month")
        self.assertEqual(calendar["event_count"], 71)


def urlhost(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).hostname or ""


class CalendarToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.session = make_client()
        self.app = StubApp(self.client)

    def test_turkish_query_through_the_tool(self) -> None:
        result = self.app.get_academic_calendar(query="dersten çekilme")
        self.assertEqual(result["event_count"], 2)
        self.assertEqual({e["academic_term"] for e in result["events"]}, {"fall", "spring"})
        self.assertNotIn("default_window", result)

    def test_exam_range_through_the_tool(self) -> None:
        result = self.app.get_academic_calendar(date_from="2026-11-01", date_to="2027-01-31", category="exam")
        self.assertGreater(result["event_count"], 0)
        self.assertNotIn("coverage_warning", result)
        self.assertEqual(result["coverage"]["scope"], "academic_year")

    def test_call_without_filters_returns_a_stated_window_not_the_whole_year(self) -> None:
        result = self.app.get_academic_calendar()
        window = result["default_window"]
        self.assertLess(window["start_date"], window["end_date"])
        self.assertEqual(result["total_event_count"], 86)
        self.assertLess(result["event_count"], 86)
        for event in result["events"]:
            self.assertLessEqual(event["start_date"], window["end_date"])
            self.assertGreaterEqual(event["end_date"], window["start_date"])

    def test_fetch_warning_survives_filtering(self) -> None:
        result = self.app.get_academic_calendar(date_from="2015-11-01", date_to="2015-12-31")
        self.assertEqual(result["event_count"], 0)
        self.assertIn("2015-2016", result["coverage_warning"])

    def test_invalid_date_is_rejected_before_any_request(self) -> None:
        with self.assertRaises(ValueError):
            self.app.get_academic_calendar(date_from="11.10.2026")
        self.assertEqual(self.session.requested, [])


if __name__ == "__main__":
    unittest.main()
