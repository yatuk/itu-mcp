"""Announcements, building codes, sports hours, degree plans, library records and
the OBS course lookup, against trimmed copies of the real public pages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import unittest
from unittest.mock import Mock, patch

import requests

from ninova_mcp.library_catalog import parse_record, parse_search
from ninova_mcp.obs_client import ObsPublicClient
from ninova_mcp.prompts import research_course
from ninova_mcp.public_client import ItuPublicClient
from ninova_mcp.public_parsing import (
    extract_announcement_list,
    extract_building_codes,
    extract_degree_plan_detail,
    extract_sports_facility_hours,
    parse_announcement_date,
    truncate_text,
)
from ninova_mcp.server import NinovaMcpApp

FIXTURES = Path(__file__).parent / "fixtures"
ITU_NEWS_URL = "https://www.itu.edu.tr/duyurular"
ERASMUS_URL = "https://erasmus.itu.edu.tr/haberler"
SITEFINITY = {name: f"https://{name}.itu.edu.tr/api/default/newsitems" for name in ("odek", "ikm", "sks")}


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeSession:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.headers: dict[str, str] = {}
        self.requested: list[str] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.requested.append(url)
        body = self.routes.get(url)
        response = requests.Response()
        response.status_code = 200 if body is not None else 404
        response.url = url
        text = body if isinstance(body, str) else json.dumps(body if body is not None else {})
        response._content = text.encode("utf-8")
        response.encoding = "utf-8"
        return response


def make_client(routes: dict[str, Any]) -> ItuPublicClient:
    client = ItuPublicClient(session=FakeSession(routes), cache_ttl=60)  # type: ignore[arg-type]
    client._min_request_interval = 0
    return client


def news(prefix: str, dates: list[str]) -> dict[str, Any]:
    return {"value": [
        {"Title": f"{prefix} duyurusu {index}", "PublicationDate": stamp, "UrlName": f"{prefix}-{index}", "Summary": f"{prefix} özet {index}"}
        for index, stamp in enumerate(dates)
    ]}


ERASMUS_HTML = """<html><body><div class="news-item"><h3>Erasmus başvuru takvimi açıklandı</h3>
<span>06 Eki 2026</span><a href="/haberler/basvuru-takvimi">Devamı</a></div></body></html>"""


class AnnouncementDateTests(unittest.TestCase):
    def test_turkish_short_and_full_month_names(self) -> None:
        cases = {
            "06 Eki 2026": "2026-10-06", "6 Ekim 2026": "2026-10-06", "18 Eyl 2026": "2026-09-18",
            "31 Ağu 2026": "2026-08-31", "27 Şub 2026": "2026-02-27", "1 Oca 2025": "2025-01-01",
            "10 Mar 2026": "2026-03-10", "5 Nis 2024": "2024-04-05", "9 May 2024": "2024-05-09",
            "2 Haz 2024": "2024-06-02", "31 Tem 2026": "2026-07-31", "3 Kas 2023": "2023-11-03",
            "12 Ara 2022": "2022-12-12", "14 Ağustos 2020": "2020-08-14", "1 Şubat 2021": "2021-02-01",
            "20 Aralık 2021": "2021-12-20", "7 Kasım 2019": "2019-11-07", "15 Mayıs 2019": "2019-05-15",
            "06.10.2026": "2026-10-06", "2026-10-06": "2026-10-06",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_announcement_date(raw), expected)

    def test_iso_timestamps_keep_their_time(self) -> None:
        self.assertEqual(parse_announcement_date("2026-10-06T08:30:00Z"), "2026-10-06T08:30:00+00:00")

    def test_unreadable_dates_are_none(self) -> None:
        for raw in (None, "", "yakında", "32 Eki 2026", "06 Foo 2026"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_announcement_date(raw))


class MainSiteAnnouncementParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = extract_announcement_list(fixture("itu_duyurular.html"), ITU_NEWS_URL, "itu")

    def test_cards_of_the_real_page_are_read(self) -> None:
        self.assertEqual(len(self.items), 8)
        first = self.items[0]
        self.assertEqual(first["source"], "itu")
        self.assertTrue(first["title"].startswith("18.09.2026 Tarihli ve 33374 Sayılı Resmi Gazete"))
        self.assertEqual(first["published_at"], "18 Eyl 2026")
        self.assertEqual(first["published_at_iso"], "2026-09-18")
        self.assertTrue(first["url"].startswith("https://www.itu.edu.tr/haber-detay/2026/09/18/"))

    def test_summary_is_the_card_text_not_title_date_and_button(self) -> None:
        first = self.items[0]
        self.assertTrue(first["summary"].startswith("Müracaat süresi ilan tarihinden itibaren 15 gün"))
        self.assertNotIn("Devamı", first["summary"])
        self.assertNotIn("18 Eyl 2026", first["summary"])

    def test_every_card_has_a_date(self) -> None:
        self.assertEqual([item["published_at_iso"] for item in self.items], [
            "2026-09-18", "2026-08-31", "2026-08-21", "2026-08-03",
            "2026-07-31", "2026-03-31", "2026-03-10", "2026-02-27",
        ])


class TruncationTests(unittest.TestCase):
    def test_short_text_is_untouched(self) -> None:
        self.assertEqual(truncate_text("kısa metin", 50), ("kısa metin", False))

    def test_long_text_ends_at_a_word_with_a_marker(self) -> None:
        text = " ".join(["başvuru"] * 200)
        cut, truncated = truncate_text(text, 100)
        self.assertTrue(truncated)
        self.assertLessEqual(len(cut), 101)
        self.assertTrue(cut.endswith("başvuru…"))

    def test_long_summary_in_a_feed_is_marked(self) -> None:
        long_text = " ".join(["yönetmelik"] * 200)
        client = make_client({SITEFINITY["sks"]: {"value": [
            {"Title": "Uzun duyuru metni", "PublicationDate": "2026-05-01T09:00:00Z", "UrlName": "uzun", "Summary": long_text},
        ]}})
        item = client.get_announcements(sources=["sks"])["announcements"][0]
        self.assertTrue(item["summary"].endswith("yönetmelik…"))
        self.assertIs(item["summary_truncated"], True)
        self.assertLessEqual(len(item["summary"]), 701)


class AnnouncementAggregationTests(unittest.TestCase):
    def routes(self) -> dict[str, Any]:
        return {
            ITU_NEWS_URL: fixture("itu_duyurular.html"),
            ERASMUS_URL: ERASMUS_HTML,
            SITEFINITY["sks"]: news("sks", ["2016-03-0%dT10:00:00Z" % day for day in range(1, 10)]),
            SITEFINITY["ikm"]: news("ikm", ["2026-09-25T10:00:00Z", "2025-01-10T10:00:00Z"]),
            SITEFINITY["odek"]: news("odek", ["2026-10-%02dT10:00:00Z" % day for day in range(1, 31)]),
        }

    def test_main_site_items_are_returned(self) -> None:
        result = make_client(self.routes()).get_announcements(sources=["ITU", "sks", "ikm", "erasmus"], limit=40)
        self.assertEqual(result["source_counts"], {"itu": 8, "sks": 9, "ikm": 2, "erasmus": 1})
        self.assertEqual(result["empty_sources"], [])
        self.assertEqual(result["count"], 20)

    def test_turkish_dates_sort_with_iso_dates_newest_first(self) -> None:
        result = make_client(self.routes()).get_announcements(sources=["itu", "sks", "ikm", "erasmus"], limit=40)
        items = result["announcements"]
        self.assertEqual(items[0]["source"], "erasmus")
        self.assertEqual(items[0]["published_at_iso"], "2026-10-06")
        dates = [item["published_at_iso"][:10] for item in items]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual({item["source"] for item in items[-9:]}, {"sks"})

    def test_source_with_no_items_is_listed_but_is_not_an_error(self) -> None:
        routes = self.routes()
        routes[ITU_NEWS_URL] = "<html><body><p>Liste boş</p></body></html>"
        result = make_client(routes).get_announcements(sources=["itu", "ikm"])
        self.assertEqual(result["empty_sources"], ["itu"])
        self.assertEqual(result["source_errors"], [])
        self.assertIs(result["partial"], False)
        self.assertEqual(result["source_counts"], {"itu": 0, "ikm": 2})
        self.assertIn("empty_sources_note", result)

    def test_failed_source_is_an_error_not_an_empty_source(self) -> None:
        routes = self.routes()
        del routes[ERASMUS_URL]
        result = make_client(routes).get_announcements(sources=["erasmus", "ikm"])
        self.assertEqual([item["source"] for item in result["source_errors"]], ["erasmus"])
        self.assertEqual(result["empty_sources"], [])
        self.assertIs(result["partial"], True)

    def test_default_call_does_not_return_one_source_only(self) -> None:
        # odek alone has 30 items newer than almost everything else.
        result = make_client(self.routes()).get_announcements()
        self.assertEqual(result["count"], 30)
        counts = result["source_counts"]
        self.assertEqual(set(counts), {"itu", "odek", "ikm", "sks", "erasmus"})
        for source in ("itu", "odek", "ikm", "sks", "erasmus"):
            self.assertGreater(counts[source], 0, source)
        dates = [item["published_at_iso"][:10] for item in result["announcements"]]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_undated_items_come_last(self) -> None:
        routes = self.routes()
        routes[ERASMUS_URL] = ERASMUS_HTML.replace("06 Eki 2026", "tarih yok")
        result = make_client(routes).get_announcements(sources=["erasmus", "ikm"])
        self.assertEqual([item["source"] for item in result["announcements"]], ["ikm", "ikm", "erasmus"])
        self.assertIsNone(result["announcements"][-1]["published_at_iso"])

    def test_query_filters_before_the_limit(self) -> None:
        result = make_client(self.routes()).get_announcements(query="faaliyet raporu")
        self.assertEqual([item["source"] for item in result["announcements"]], ["itu"])
        self.assertEqual(result["empty_sources"], [])


class BuildingCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = fixture("bina_kodlari.html")
        self.result = extract_building_codes(self.html, "https://obs.itu.edu.tr/buildings")

    def test_each_building_code_appears_once(self) -> None:
        codes = [item["code"] for item in self.result["locations"]]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(codes.count("DZB"), 1)
        self.assertIn({"code": "BBB", "name": "Bilgisayar ve Bilişim Binası"}, self.result["locations"])
        self.assertEqual(self.result["count"], len(codes))

    def test_repeated_code_is_one_building_with_sub_locations(self) -> None:
        building = next(item for item in self.result["locations"] if item["code"] == "DZB")
        self.assertIsNone(building["name"])
        self.assertIs(building["name_missing_in_source"], True)
        self.assertEqual(building["sub_location_codes"], ["C", "B", "DG", "ATL", "SIM", "SEM", "TG", "LAB", "KULE", "AKB", "DEB", "AMFİ"])
        self.assertEqual(len(self.result["sub_locations"]), 12)
        self.assertIn({"building_code": "DZB", "code": "AMFİ"}, self.result["sub_locations"])
        self.assertEqual(self.result["sub_location_count"], 12)

    def test_pseudo_entries_are_not_buildings(self) -> None:
        codes = {item["code"] for item in self.result["locations"]}
        self.assertNotIn("undeclared", codes)
        self.assertNotIn("Online/Çevrimiçi", codes)
        self.assertEqual(
            {(item["code"], item["kind"]) for item in self.result["pseudo_entries"]},
            {("undeclared", "announced_later"), ("Online/Çevrimiçi", "online")},
        )

    def test_query_matches_names_codes_and_sub_locations(self) -> None:
        by_name = extract_building_codes(self.html, "https://obs.itu.edu.tr/b", "bilgisayar")
        self.assertEqual([item["code"] for item in by_name["locations"]], ["BBB"])
        by_code = extract_building_codes(self.html, "https://obs.itu.edu.tr/b", "dzb")
        self.assertEqual([item["code"] for item in by_code["locations"]], ["DZB"])
        self.assertEqual(len(by_code["sub_locations"]), 12)
        by_sub = extract_building_codes(self.html, "https://obs.itu.edu.tr/b", "amfi")
        self.assertEqual(by_sub["sub_locations"], [{"building_code": "DZB", "code": "AMFİ"}])
        self.assertEqual(by_sub["locations"], [])

    def test_no_match_explains_what_the_source_lists(self) -> None:
        result = extract_building_codes(self.html, "https://obs.itu.edu.tr/b", "kutuphane")
        self.assertEqual(result["count"], 0)
        self.assertIn("no_match_note", result)
        self.assertNotIn("parse_warning", result)
        self.assertNotIn("no_match_note", self.result)


class SportsHoursTests(unittest.TestCase):
    def setUp(self) -> None:
        result = extract_sports_facility_hours(fixture("sks_spor_tesisleri_saatleri.html"), "https://sks.itu.edu.tr/s")
        self.assertEqual(result["count"], 7)
        self.facilities = {item["facility"]: item for item in result["facilities"]}

    def test_dotted_time_is_normalized(self) -> None:
        self.assertEqual(self.facilities["Tenis Kortları"]["weekday"], {"opens": "09:00", "closes": "22:00", "closed": False})

    def test_closed_period_is_a_flag_not_two_strings(self) -> None:
        self.assertEqual(
            self.facilities["Gümüşsuyu Yerleşkesi Spor Salonu"]["weekend"],
            {"opens": None, "closes": None, "closed": True},
        )
        self.assertEqual(self.facilities["Gümüşsuyu Yerleşkesi Spor Salonu"]["weekday"]["closed"], False)

    def test_closure_note_moves_out_of_the_name(self) -> None:
        self.assertNotIn("Olimpik Yüzme Havuzu (Pazartesi Kapalı)", self.facilities)
        pool = self.facilities["Olimpik Yüzme Havuzu"]
        self.assertEqual(pool["closed_days"], ["Pazartesi"])
        self.assertEqual(pool["note"], "Pazartesi Kapalı")
        self.assertEqual(pool["weekday"], {"opens": "08:00", "closes": "20:00", "closed": False})

    def test_other_parentheses_stay_in_the_name(self) -> None:
        gym = self.facilities["Sağlıklı Yaşam Merkezi (Fitness Salonu)"]
        self.assertEqual(gym["closed_days"], [])
        self.assertNotIn("note", gym)


class DegreePlanEctsTests(unittest.TestCase):
    ROW = (
        '<tr><td><a href="/public/DersBilgi?bransKodu=BLG&amp;dersNo={no}">BLG {no}</a></td><td>Ders</td>'
        "<td>English</td><td>Z</td><td>3</td><td>{ects}</td><td>3</td><td>0</td><td>0</td><td>TM</td></tr>"
    )

    def plan(self, **kwargs: Any) -> dict[str, Any]:
        rows = "".join(self.ROW.format(no=no, ects=ects) for no, ects in (("101", "4,5"), ("102", "4 / 5"), ("103", "6"), ("104", "")))
        html = f'<table class="datalist"><thead><tr><th><h2>1. Yarıyıl</h2></th></tr></thead><tbody>{rows}</tbody></table>'
        return extract_degree_plan_detail(html, "https://obs.itu.edu.tr/public/DersPlan/DersPlanDetay/1", **kwargs)

    def test_ects_is_a_number(self) -> None:
        courses = self.plan()["semesters"][0]["courses"]
        self.assertEqual(courses[0]["ects"], 4.5)
        self.assertEqual(courses[2]["ects"], 6.0)
        self.assertIsNone(courses[3]["ects"])
        self.assertNotIn("ects_raw", courses[0])

    def test_ects_range_keeps_the_text_and_exposes_both_ends(self) -> None:
        course = self.plan()["semesters"][0]["courses"][1]
        self.assertIsNone(course["ects"])
        self.assertEqual((course["ects_min"], course["ects_max"]), (4.0, 5.0))
        self.assertEqual(course["ects_raw"], "4 / 5")

    def test_courses_are_listed_once_by_default(self) -> None:
        plan = self.plan()
        self.assertNotIn("courses", plan)
        self.assertEqual(plan["course_count"], 4)
        self.assertEqual(plan["semesters"][0]["course_count"], 4)

    def test_flat_list_is_available_on_request(self) -> None:
        plan = self.plan(include_flat_courses=True)
        self.assertEqual(len(plan["courses"]), 4)
        self.assertEqual(plan["courses"][0]["semester"], "1. Yarıyıl")

    def test_real_fixture(self) -> None:
        detail = extract_degree_plan_detail(fixture("degree_plan.html"), "https://obs.itu.edu.tr/public/DersPlan/DersPlanDetay/2340")
        course = detail["semesters"][0]["courses"][0]
        self.assertEqual((course["credit"], course["ects"]), (1.5, 5.0))


class LibraryRecordTests(unittest.TestCase):
    CELL = (
        '<div class="results_cell"><input class="results_chkbox" value="ent://SD_ILS/0/SD_ILS:140160"/>'
        '<div class="displayElementWrapper INITIAL_TITLE_SRCH"><div class="displayDetailLink"><a href="#">Introduction to algorithms</a></div></div>'
        '<div class="displayElementText INITIAL_AUTHOR_SRCH">Yazar, Örnek, author.</div>'
        '<div class="displayElementText ISBN">{isbn}</div>'
        '<div class="hitNumber">1</div></div><div class="resultsToolbar_num_results">1 Sonuç</div>'
    )
    DETAIL = (
        '<div class="DOC_ID_value">SD_ILS:140160</div>'
        '<div class="displayElementText INITIAL_TITLE_SRCH">Örnek derleme</div>{body}'
        '<table class="detailItemTable"><tr class="detailItemsTableRow"><td class="detailItemsTable_LOCATION">Merkez</td>'
        '<td class="detailItemsTable_BARCODE">1</td></tr></table>'
    )

    def test_two_isbns_in_one_field_are_split(self) -> None:
        html = self.CELL.format(isbn="9780070131439 9780262031417")
        record = parse_search(html, "https://katalog.kutuphane.itu.edu.tr/client/tr_TR/default/search/results?qu=x")["records"][0]
        self.assertEqual(record["isbn"], ["9780070131439", "9780262031417"])

    def test_isbn_with_a_qualifier_and_separators(self) -> None:
        html = self.CELL.format(isbn="0-262-03141-8 (pbk.) ; 007013143X")
        record = parse_search(html, "https://katalog.kutuphane.itu.edu.tr/client/tr_TR/default/search/results?qu=x")["records"][0]
        self.assertEqual(record["isbn"], ["0-262-03141-8", "007013143X"])

    def test_detail_reads_the_personal_author(self) -> None:
        body = '<div class="displayElementText PERSONAL_AUTHOR">Yazar, Örnek, author.</div>'
        fields = parse_record(self.DETAIL.format(body=body), "u")["fields"]
        self.assertEqual(fields["author"], ["Yazar, Örnek, author."])
        self.assertEqual(fields["author_source"], "personal_author")

    def test_detail_without_a_personal_author_uses_the_other_author_fields(self) -> None:
        corporate = '<div class="displayElementText CORPORATE_AUTHOR">Örnek Kurum.</div>'
        fields = parse_record(self.DETAIL.format(body=corporate), "u")["fields"]
        self.assertEqual(fields["author"], ["Örnek Kurum."])
        self.assertEqual(fields["author_source"], "corporate_author")
        added = (
            '<div class="displayElementText ADDED_AUTHOR">Editör, Birinci.</div>'
            '<div class="displayElementText ADDED_AUTHOR">Editör, İkinci.</div>'
        )
        fields = parse_record(self.DETAIL.format(body=added), "u")["fields"]
        self.assertEqual(fields["author"], ["Editör, Birinci.", "Editör, İkinci."])
        self.assertEqual(fields["author_source"], "added_author")
        self.assertEqual(fields["added_authors"], ["Editör, Birinci.", "Editör, İkinci."])

    def test_detail_with_no_author_field_says_so(self) -> None:
        fields = parse_record(self.DETAIL.format(body=""), "u")["fields"]
        self.assertEqual(fields["author"], [])
        self.assertIsNone(fields["author_source"])

    def test_detail_isbns_are_split_too(self) -> None:
        body = '<div class="displayElementText ISBN">9780070131439 9780262031417</div>'
        fields = parse_record(self.DETAIL.format(body=body), "u")["fields"]
        self.assertEqual(fields["isbn"], ["9780070131439", "9780262031417"])


class ObsCourseSearchTests(unittest.TestCase):
    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        client = ObsPublicClient()
        app = NinovaMcpApp()
        app._obs_public = client
        url = "https://obs.itu.edu.tr/public/DersBilgi/DersBilgiSearch?bransKodu=BLG&dersNo=335"
        with patch.object(client, "_get_html", return_value=(fixture("ders_bilgi_search_blg335.html"), url)):
            return app.obs_search_courses(query, **kwargs)

    def test_rows_are_clean_entries_without_link_texts(self) -> None:
        result = self.search("BLG 335")
        self.assertEqual(result["count"], 2)
        self.assertNotIn("Tıklayınız", json.dumps(result, ensure_ascii=False))
        turkish, english = result["courses"]
        self.assertEqual(turkish, {
            "code": "BLG 335",
            "name": "Algoritma Analizi I",
            "language": "Türkçe",
            "paired_codes": ["BLG 335E"],
            "catalog_url": "https://obs.itu.edu.tr/public/DersKatalog/DersKatalogBilgiBransDersKodu?bransKodu=BLG&dersNo=335",
            "grade_distribution_url": "https://obs.itu.edu.tr/public/DersNotDagilimi?bransKodu=BLG&dersNo=335&yil=2026",
        })
        self.assertEqual(english["code"], "BLG 335E")
        self.assertEqual(english["name"], "Analysis of Algorithms I")
        self.assertEqual(english["language"], "English")

    def test_entry_never_carries_a_link_to_another_course_number(self) -> None:
        english = self.search("BLG 335")["courses"][1]
        self.assertNotIn("catalog_url", english)
        self.assertNotIn("grade_distribution_url", english)
        self.assertNotIn("url", english)

    def test_changed_limit_is_reported(self) -> None:
        result = self.search("BLG 335", limit=500)
        self.assertEqual(result["limit"], 50)
        self.assertIn("500", result["limit_note"])
        self.assertNotIn("limit_note", self.search("BLG 335", limit=15))

    def test_rows_without_raw_columns_still_pass_through(self) -> None:
        app = NinovaMcpApp()
        app._obs_public = Mock()
        app._obs_public.search_courses.return_value = [{"code": "UZB 438E", "name": "Robotic Control Systems"}]
        result = app.obs_search_courses("UZB438E")
        self.assertEqual(result["courses"], [{"code": "UZB 438E", "name": "Robotic Control Systems"}])


class ResearchCoursePromptTests(unittest.TestCase):
    def test_prompt_does_not_claim_the_lookup_sees_only_the_active_term(self) -> None:
        text = research_course("BLG 335E")
        self.assertNotIn("yalnızca aktif dönemi görür", text)
        self.assertIn("archive_search_courses", text)
        self.assertIn("obs_search_courses", text)


class DirectoryNoResultTests(unittest.TestCase):
    def test_site_message_for_no_result_is_passed_on_with_a_hint(self) -> None:
        home = '<form><input name="__RequestVerificationToken" value="t"/></form>'
        empty = '<div class="alert">Deneme Kişi için sonuç bulunamadı.</div>'
        client = make_client({})
        answer = requests.Response()
        answer.status_code, answer.url, answer._content, answer.encoding = 200, "https://rehber.itu.edu.tr/Rehber/Search", empty.encode("utf-8"), "utf-8"
        with patch.object(client, "_get_text", return_value=(home, "https://rehber.itu.edu.tr/")), \
                patch.object(client, "_request", return_value=answer):
            result = client.search_directory("Deneme", "Kişi", identity_type="academic")
        self.assertEqual(result["count"], 0)
        self.assertNotIn("parse_warning", result)
        self.assertIs(result["no_results"], True)
        self.assertIn("identity_type", result["message"])


if __name__ == "__main__":
    unittest.main()
