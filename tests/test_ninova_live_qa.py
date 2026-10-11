"""Defects found by a live pass over the Ninova tools, reproduced with synthetic pages."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import Mock

from ninova_mcp import parsing
from ninova_mcp.client import NinovaError
from ninova_mcp.compact import compact_value
from ninova_mcp.parsing import (
    extract_announcement_detail,
    extract_assignment_detail,
    extract_attendance,
    parse_html_page,
)
from ninova_mcp.server import NinovaMcpApp

BASE = "https://ninova.itu.edu.tr"
COURSE_URL = BASE + "/Sinif/7.1"
COURSE = {"code": "ZZT 322E", "title": "Sentetik Mimari", "url": COURSE_URL, "context": "ZZT 322E", "section": None, "crn": None}


def attendance_page(rows: list[tuple[str, str, str]], heading: str = "Kepler Yoklama Bilgileri") -> str:
    body = "".join(f"<tr><td>{week}</td><td>{first}</td><td>{second}</td></tr>" for week, first, second in rows)
    return f"<html><body><h2>{heading}</h2><table><tr><th>Hafta</th><th>Sali</th><th>Persembe</th></tr>{body}</table></body></html>"


INFO_PAGE = (
    "<html><body><table><tr><td>ZZT 322E</td><td>Synthetic Architecture</td><td>Sentetik Mimari</td></tr></table>"
    "<table><tr><td>Dönem</td><td>2030-2031 Güz Dönemi</td></tr>"
    "<tr><td>Başlangıç Tarihi</td><td>02 Eylül 2030</td></tr></table></body></html>"
)


class FakeClient:
    base_url = BASE

    def __init__(self, pages: dict[str, str], refused: dict[str, str] | None = None) -> None:
        self.pages = pages
        self.refused = refused or {}
        self.requested: list[str] = []

    def ninova_url(self, value: str) -> str:
        return value if value.startswith("http") else BASE + value

    def get_html(self, url: str) -> tuple[str, Mock]:
        full = self.ninova_url(url)
        self.requested.append(full)
        if full in self.refused:
            raise NinovaError(self.refused[full])
        if full not in self.pages:
            raise NinovaError(f"Synthetic missing page: {full}")
        return self.pages[full], Mock(url=full)


def app_with(pages: dict[str, str], refused: dict[str, str] | None = None) -> NinovaMcpApp:
    app = NinovaMcpApp.__new__(NinovaMcpApp)
    app._client = FakeClient(pages, refused)
    app.list_courses = Mock(return_value={"courses": [COURSE]})
    app._out = lambda payload, compact=False: payload
    return app


class AttendanceGridTests(unittest.TestCase):
    def parse(self, rows: list[tuple[str, str, str]]) -> dict:
        return extract_attendance(attendance_page(rows), COURSE_URL + "/Yoklama", BASE)

    def test_a_grid_of_zeros_is_not_reported_as_absences(self) -> None:
        result = self.parse([(f"{week}. Hafta", "0 0 0", "0 0 0 0") for week in range(1, 15)])
        self.assertEqual(result["total_present_marks"], 0)
        self.assertEqual(result["total_absent_marks"], 0)
        self.assertEqual(result["total_unmarked_or_absent_marks"], 98)
        self.assertFalse(result["absence_counts_reliable"])
        self.assertIn("0", result["note"])
        session = result["weeks"][0]["sessions"][0]
        self.assertEqual(session["absent_count"], 0)
        self.assertEqual(session["unmarked_or_absent_count"], 3)
        self.assertEqual(result["weeks"][0]["status"], "unmarked_or_absent")

    def test_zeros_count_as_absences_only_in_weeks_that_have_a_recorded_mark(self) -> None:
        result = self.parse([
            ("1. Hafta", "1 1", "0 1"), ("2. Hafta", "0 0", "1 1"),
            ("3. Hafta", "0 0", "0 0"), ("4. Hafta", "0 0", "0 0"),
        ])
        self.assertEqual(result["total_present_marks"], 5)
        self.assertEqual(result["total_absent_marks"], 3)
        self.assertEqual(result["total_unmarked_or_absent_marks"], 8)
        self.assertEqual([week["status"] for week in result["weeks"]],
                         ["recorded", "recorded", "unmarked_or_absent", "unmarked_or_absent"])
        self.assertFalse(result["absence_counts_reliable"])

    def test_fully_recorded_grid_is_reliable(self) -> None:
        result = self.parse([("1. Hafta", "1 1", "0 1"), ("2. Hafta", "0 0", "1 1")])
        self.assertEqual(result["total_absent_marks"], 3)
        self.assertEqual(result["total_unmarked_or_absent_marks"], 0)
        self.assertTrue(result["absence_counts_reliable"])
        self.assertNotIn("note", result)

    def test_weeks_after_today_are_future_when_the_term_start_is_known(self) -> None:
        result = self.parse([(f"{week}. Hafta", "0 0", "0 0") for week in range(1, 5)])
        parsing.apply_attendance_calendar(result, term_start=date(2030, 9, 2), today=date(2030, 9, 12))
        self.assertEqual([week["status"] for week in result["weeks"]],
                         ["unmarked_or_absent", "unmarked_or_absent", "future", "future"])
        self.assertEqual(result["weeks"][2]["week_start"], "2030-09-16")
        self.assertEqual(result["total_future_marks"], 8)
        self.assertEqual(result["total_unmarked_or_absent_marks"], 8)
        self.assertEqual(result["weeks"][3]["sessions"][0]["unmarked_or_absent_count"], 0)
        self.assertEqual(result["weeks"][3]["sessions"][0]["future_count"], 2)

    def test_tool_marks_future_weeks_from_the_course_start_date(self) -> None:
        app = app_with({
            COURSE_URL + "/Yoklama": attendance_page([(f"{week}. Hafta", "0 0", "0 0") for week in range(1, 15)]),
            COURSE_URL + "/SinifBilgileri": INFO_PAGE,
        })
        app._today = lambda: date(2030, 9, 12)
        result = app.get_course_attendance("ZZT 322E")
        self.assertEqual(result["term_start"], "2030-09-02")
        self.assertEqual(result["total_absent_marks"], 0)
        self.assertEqual(result["total_future_marks"], 12 * 4)
        self.assertEqual(result["total_unmarked_or_absent_marks"], 2 * 4)

    def test_tool_still_answers_when_the_course_info_page_fails(self) -> None:
        app = app_with({COURSE_URL + "/Yoklama": attendance_page([("1. Hafta", "0 0", "0 0")])})
        result = app.get_course_attendance("ZZT 322E")
        self.assertIsNone(result["term_start"])
        self.assertEqual(result["total_unmarked_or_absent_marks"], 4)


class CrawlSkipsBadLinksTests(unittest.TestCase):
    def test_a_refused_link_is_skipped_and_the_crawl_continues(self) -> None:
        bad = COURSE_URL + "/Bozuk\\Yol"
        start = (
            '<html><body><h1>Sentetik</h1><a href="/Sinif/7.1/Duyurular">Duyurular</a>'
            '<a href="/Sinif/7.1/Bozuk\\Yol">Bozuk</a><a href="/Sinif/7.1/Odevler">Odevler</a></body></html>'
        )
        app = app_with(
            {COURSE_URL: start, COURSE_URL + "/Duyurular": "<h1>Duyurular</h1>", COURSE_URL + "/Odevler": "<h1>Odevler</h1>"},
            refused={bad: "URL contains a backslash, whitespace or a control character and was refused."},
        )
        result = app.crawl_course("/Sinif/7.1", max_pages=12)
        self.assertEqual([page["url"] for page in result["pages"]],
                         [COURSE_URL, COURSE_URL + "/Duyurular", COURSE_URL + "/Odevler"])
        self.assertEqual(result["pages_crawled"], 3)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["url"], bad)
        self.assertIn("refused", result["skipped"][0]["reason"])

    def test_a_failing_start_page_is_still_an_error(self) -> None:
        app = app_with({})
        with self.assertRaises(NinovaError):
            app.crawl_course("/Sinif/7.1")

    def test_link_hrefs_are_stripped_and_inner_whitespace_is_encoded(self) -> None:
        html = '<a href="  /Sinif/7.1/Sinif Dosyalari?g=a b ">Dosyalar</a><a href="/Sinif/7.1/Notlar\n">Notlar</a>'
        links = parse_html_page(COURSE_URL, html, base_url=BASE)["links"]
        self.assertEqual([link["url"] for link in links],
                         [COURSE_URL + "/Sinif%20Dosyalari?g=a%20b", COURSE_URL + "/Notlar"])


class CompactKeepsLabelsTests(unittest.TestCase):
    def test_short_text_labels_survive_and_long_page_text_does_not(self) -> None:
        payload = {"page": {
            "headings": [{"level": 1, "text": "Sentetik Baslik"}],
            "links": [{"text": "Duyurular", "url": COURSE_URL + "/Duyurular"}],
            "text": "x" * 5000,
        }}
        compacted = compact_value(payload)
        self.assertEqual(compacted["page"]["headings"], [{"level": 1, "text": "Sentetik Baslik"}])
        self.assertEqual(len(compacted["page"]["text"]), 800)
        top_level = compact_value({"links": [{"text": "Duyurular", "url": "u"}]})
        self.assertEqual(top_level["links"], [{"text": "Duyurular", "url": "u"}])

    def test_a_long_label_is_truncated_not_dropped(self) -> None:
        compacted = compact_value({"items": [{"text": "y" * 1000}]}, text_chars=100)
        self.assertEqual(len(compacted["items"][0]["text"]), 100)


CHROME = (
    "<html><head><title>Ninova</title></head><body>"
    "<table><tr><td><a href='/Kampus1'>Ana Sayfa</a></td><td><a href='/Sinif/7.1'>Sinif</a></td></tr></table>"
    "<table><tr><td>" + "Gezinme metni. " * 30 + "</td></tr></table>"
    "<table><tr><td><a href='/Sinif/7.1/Notlar'>Notlar</a></td></tr></table>"
    "{content}</body></html>"
)


class EmptyListWarningTests(unittest.TestCase):
    def test_an_empty_announcements_page_with_only_page_chrome_is_a_clean_empty(self) -> None:
        app = app_with({COURSE_URL + "/Duyurular": CHROME.format(content="<div id='icerik'></div>")})
        result = app.get_course_announcements("ZZT 322E")
        self.assertEqual(result["count"], 0)
        self.assertNotIn("parse_warning", result)

    def test_announcement_links_that_were_not_parsed_still_warn(self) -> None:
        content = "<p><a href='/Sinif/7.1/Duyuru/55'>Sentetik Duyuru</a></p>"
        app = app_with({COURSE_URL + "/Duyurular": CHROME.format(content=content)})
        result = app.get_course_announcements("ZZT 322E")
        self.assertEqual(result["count"], 0)
        self.assertIn("layout may have changed", result["parse_warning"])

    def test_an_empty_assignments_page_with_only_page_chrome_is_a_clean_empty(self) -> None:
        app = app_with({COURSE_URL + "/Odevler": CHROME.format(content="")})
        result = app.get_course_assignments("ZZT 322E")
        self.assertEqual(result["count"], 0)
        self.assertNotIn("parse_warning", result)

    def test_a_nearly_empty_body_still_warns(self) -> None:
        app = app_with({COURSE_URL + "/Duyurular": "<html><body></body></html>"})
        self.assertIn("nearly empty", app.get_course_announcements("ZZT 322E")["parse_warning"])


DETAIL_PAGE = (
    "<html><head><title>Ninova</title></head><body><div class='menu'>Ana Sayfa Yardım</div>"
    "<div class='duyuruGoruntule'><h2>Sentetik Sinav Duyurusu</h2><div class='icerik'>"
    "<p>Sinav 3. hafta yapilacak.</p><p>Yardım masasi acik olacak.</p>"
    "<span class='tarih'>07 Ekim 2030 15:20</span></div></div>"
    "<div class='yardim'><h3>Yardım</h3>Diğer sınıflarınıza ait tüm duyuruları görüntülmek için hızlı "
    "işlemlerden Duyurular bağlantısına tıklayınız.</div><div class='footer'>Dersler . Yardım</div></body></html>"
)
FLAT_DETAIL_PAGE = (
    "<html><body><h1>Sentetik Sinav Duyurusu</h1> 07 Ekim 2030 15:20 Sinav 3. hafta yapilacak. "
    "Yardım Diğer sınıflarınıza ait tüm duyuruları görüntülmek için hızlı işlemlerden Duyurular "
    "bağlantısına tıklayınız. Dersler . Yardım</body></html>"
)


class DetailBodyTests(unittest.TestCase):
    def test_announcement_body_comes_from_its_own_element(self) -> None:
        detail = extract_announcement_detail(DETAIL_PAGE, COURSE_URL + "/Duyuru/55", BASE)
        self.assertEqual(detail["title"], "Sentetik Sinav Duyurusu")
        self.assertEqual(detail["published_at"], "07 Ekim 2030 15:20")
        self.assertEqual(detail["published_at_iso"], "2030-10-07T15:20:00+03:00")
        self.assertEqual(detail["body_text"], "Sinav 3. hafta yapilacak. Yardım masasi acik olacak.")

    def test_flattened_announcement_page_is_cut_at_the_help_box(self) -> None:
        detail = extract_announcement_detail(FLAT_DETAIL_PAGE, COURSE_URL + "/Duyuru/55", BASE)
        self.assertEqual(detail["body_text"], "Sinav 3. hafta yapilacak.")

    def test_assignment_description_stops_before_the_download_link(self) -> None:
        html = (
            "<html><body><h1>Sentetik Odev</h1> Teslim Başlangıcı 01 Ekim 2030 00:00 Teslim Bitişi 09 Ekim 2030 23:59 "
            "Ödev Açıklaması Tum sorulari cozun. <a href='/Sinif/7.1/Odev/9?indir'>Yüklediğiniz ödev dosyalarını indirin.</a>"
            " Yardım</body></html>"
        )
        detail = extract_assignment_detail(html, COURSE_URL + "/Odev/9", BASE)
        self.assertEqual(detail["description"], "Tum sorulari cozun.")


LIST_PAGE = (
    "<html><body><div class='duyuruGoruntule'><h2><a href='/Sinif/7.1/Duyuru/55'>Sentetik Duyuru</a></h2>"
    "<div class='icerik'>Kisa ozet.<br/><span class='tarih'>07 Ekim 2030 15:20</span></div>"
    "<div class='tarih'><span class='tarih'>Sentetik Kisi</span></div></div></body></html>"
)


class DateFieldTests(unittest.TestCase):
    def test_announcement_lists_carry_an_iso_date(self) -> None:
        app = app_with({COURSE_URL + "/Duyurular": LIST_PAGE, BASE + "/Kampus?1/Duyurular": LIST_PAGE})
        for result in (app.get_course_announcements("ZZT 322E"), app.get_dashboard_announcements()):
            self.assertEqual(result["announcements"][0]["published_at_iso"], "2030-10-07T15:20:00+03:00")

    def test_a_date_without_a_year_gets_the_nearest_year_and_is_marked(self) -> None:
        infer = parsing.infer_yearless_datetime
        self.assertEqual(infer("07 Ekim 15:20", today=date(2030, 10, 11)), "2030-10-07T15:20:00+03:00")
        self.assertEqual(infer("28 Aralık 09:00", today=date(2031, 1, 5)), "2030-12-28T09:00:00+03:00")
        self.assertEqual(infer("03 Ocak", today=date(2030, 12, 30)), "2031-01-03T00:00:00+03:00")
        self.assertIsNone(infer("07 Ekim 2030 15:20", today=date(2030, 10, 11)))
        self.assertIsNone(infer("Sentetik metin", today=date(2030, 10, 11)))

    def test_dashboard_rows_get_inferred_iso_dates(self) -> None:
        html = (
            "<html><body><h2>Son Duyurular</h2><table><tr><th>Duyuru</th><th>Tarih</th></tr>"
            "<tr><td>Sentetik Duyuru ZZT 322E</td><td>07 Ekim 15:20</td></tr></table>"
            "<h2>Son Mesajlar</h2><table><tr><td>Sentetik Mesaj ZZT 322E</td><td>06 Ekim 11:05</td></tr></table>"
            "</body></html>"
        )
        page = parse_html_page(BASE + "/Kampus1", html, base_url=BASE)
        dashboard = parsing.summarize_dashboard(page, html=html, base_url=BASE, today=date(2030, 10, 11))
        row = dashboard["recent_announcements"][0]
        self.assertEqual(row["Tarih"], "07 Ekim 15:20")
        self.assertEqual(row["date_iso"], "2030-10-07T15:20:00+03:00")
        self.assertTrue(row["date_year_inferred"])
        message = dashboard["recent_messages"][0]
        self.assertEqual(message["text"], "Sentetik Mesaj ZZT 322E")
        self.assertEqual(message["date"], "06 Ekim 11:05")
        self.assertEqual(message["date_iso"], "2030-10-06T11:05:00+03:00")
        self.assertTrue(message["date_year_inferred"])
        self.assertIsInstance(datetime.fromisoformat(message["date_iso"]), datetime)


class EmptyGradebookTests(unittest.TestCase):
    PAGE = "<html><body><table><tr><th>Değerlendirme</th><th>Not</th></tr>{rows}</table></body></html>"

    def test_average_without_any_grade_is_flagged_as_a_placeholder(self) -> None:
        app = app_with({COURSE_URL + "/Notlar": self.PAGE.format(rows="")})
        result = app.get_course_grades("ZZT 322E")
        self.assertEqual(result["grades"], [])
        self.assertIn("placeholder", result["weighted_average_note"])

    def test_average_with_grades_is_not_flagged(self) -> None:
        rows = "<tr><td>Sentetik Vize</td><td>70</td></tr>"
        app = app_with({COURSE_URL + "/Notlar": self.PAGE.format(rows=rows)})
        result = app.get_course_grades("ZZT 322E")
        self.assertEqual(len(result["grades"]), 1)
        self.assertNotIn("weighted_average_note", result)


if __name__ == "__main__":
    unittest.main()
