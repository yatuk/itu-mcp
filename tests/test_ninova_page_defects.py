"""Ninova page parsing defects: neighbour codes, empty folders, file scopes, table headers.

Synthetic pages only (invented codes, names and ids).
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from ninova_mcp import parsing_course, parsing_page
from ninova_mcp.parsing import extract_courses, extract_file_directory, extract_named_table, make_soup, parse_html_page
from ninova_mcp.server import NinovaMcpApp

BASE = "https://ninova.itu.edu.tr"
PAGE = BASE + "/Kampus1"
COURSE_URL = BASE + "/Sinif/7.1"
FILE_HEADER = "<tr><th>Dosyalar</th><th>Boyut</th><th>Tarih</th></tr>"
EMPTY_FOLDER = f'<table>{FILE_HEADER}<tr><td colspan="3">Bu klasörde herhangi bir dosya bulunmamaktadır.</td></tr></table>'


class NeighbourCourseCodeTests(unittest.TestCase):
    """A course never takes its code, CRN or context from the course next to it."""

    def courses(self, html: str) -> list[dict[str, Any]]:
        return extract_courses(html, PAGE, BASE)

    def test_course_without_a_code_does_not_take_the_neighbours(self) -> None:
        html = (
            '<table><tr><td><b>ZZT 101</b> <a href="/Sinif/1.1">CRN: 11111</a></td></tr>'
            '<tr><td><a href="/Sinif/2.2">B Grubu</a></td></tr></table>'
        )
        first, second = self.courses(html)
        self.assertEqual((first["code"], first["crn"], first["context"]), ("ZZT 101", "11111", "ZZT 101 CRN: 11111"))
        self.assertNotIn("code_uncertain", first)
        self.assertIsNone(second["code"])
        self.assertIs(second["code_uncertain"], True)
        self.assertNotIn("code_source", second)
        self.assertEqual(second["context"], "B Grubu")
        self.assertNotIn("ZZT 101", str(second))
        self.assertNotIn("11111", str(second))

    def test_the_order_of_the_two_courses_does_not_matter(self) -> None:
        html = (
            '<ul><li><a href="/Sinif/2.2">B Grubu</a></li>'
            '<li><span>ZZT 101</span> <a href="/Sinif/1.1">CRN: 11111</a></li></ul>'
        )
        first, second = self.courses(html)
        self.assertIsNone(first["code"])
        self.assertIs(first["code_uncertain"], True)
        self.assertEqual(first["context"], "B Grubu")
        self.assertEqual(second["code"], "ZZT 101")

    def test_two_links_side_by_side_in_one_element_stay_apart(self) -> None:
        html = '<p><a href="/Sinif/1.1">ZZT 101 Giris</a> <a href="/Sinif/2.2">Ikinci Ders</a></p>'
        first, second = self.courses(html)
        self.assertEqual((first["code"], first["code_source"]), ("ZZT 101", "section_label"))
        self.assertEqual(first["context"], "ZZT 101 Giris")
        self.assertIsNone(second["code"])
        self.assertEqual(second["context"], "Ikinci Ders")

    def test_sections_of_one_course_still_share_its_code_and_context(self) -> None:
        html = (
            '<div><span>ZZT 500</span><ul><li><a href="/Sinif/5.1">2031 Exemption</a>'
            '<a href="/Sinif/5.2">ZZT 2030 Summer Internship</a></li></ul></div>'
            '<div><span>QQQ 201</span><ul><li><a href="/Sinif/6.1">CRN: 30303</a></li></ul></div>'
        )
        first, second, third = self.courses(html)
        self.assertEqual([first["code"], second["code"], third["code"]], ["ZZT 500", "ZZT 500", "QQQ 201"])
        self.assertEqual(first["context"], "2031 Exemption ZZT 2030 Summer Internship")
        self.assertEqual(first["context"], second["context"])
        self.assertEqual(third["context"], "QQQ 201 CRN: 30303")
        for course in (first, second, third):
            self.assertNotIn("code_uncertain", course)

    def test_a_course_without_any_code_is_marked_uncertain(self) -> None:
        course = self.courses('<p><a href="/Sinif/9.9">Yalniz Ad</a></p>')[0]
        self.assertIsNone(course["code"])
        self.assertIs(course["code_uncertain"], True)


class EmptyDirectoryTests(unittest.TestCase):
    def test_the_empty_folder_message_is_recognized_in_any_spelling(self) -> None:
        for text in (
            "Bu klasörde herhangi bir dosya bulunmamaktadır.", "HERHANGİ BİR DOSYA BULUNMAMAKTADIR",
            "herhangi  bir dosya\n bulunmamaktadir",
        ):
            with self.subTest(text=text):
                self.assertTrue(parsing_course._is_empty_directory_message(text))
        for text in ("", None, "Ders Notu.pdf", "herhangi bir duyuru bulunmamaktadır"):
            with self.subTest(text=text):
                self.assertFalse(parsing_course._is_empty_directory_message(text))

    def test_an_empty_folder_is_an_empty_list(self) -> None:
        listing = extract_file_directory(EMPTY_FOLDER, COURSE_URL + "/SinifDosyalari", BASE)
        self.assertEqual(listing, {"current_path": "/", "url": COURSE_URL + "/SinifDosyalari", "entries": []})

    def test_the_message_is_judged_only_in_a_row_of_its_own(self) -> None:
        # A file whose name contains the sentence is still a file.
        html = (
            f"<table>{FILE_HEADER}<tr><td><a href=\"/f/1\">herhangi bir dosya bulunmamaktadır.pdf</a></td>"
            "<td>1 KB</td><td>1 Eylül 2031</td></tr></table>"
        )
        self.assertEqual(len(extract_file_directory(html, COURSE_URL + "/SinifDosyalari", BASE)["entries"]), 1)

    def test_an_empty_folder_is_complete_and_without_errors_for_both_file_tools(self) -> None:
        app = NinovaMcpApp.__new__(NinovaMcpApp)
        app._client = SimpleNamespace(
            base_url=BASE, get_html=lambda url: (EMPTY_FOLDER, SimpleNamespace(url=url)),
        )
        for path in ("/SinifDosyalari", "/DersDosyalari"):
            scope = "class_files" if path == "/SinifDosyalari" else "lesson_files"
            with self.subTest(path=path):
                result = app._walk_file_directory(COURSE_URL + path, recursive=True, max_depth=3, scope=scope)
                self.assertEqual(result["entries"], [])
                self.assertEqual(result["errors"], [])
                self.assertEqual(result["coverage"]["status"], "complete")
                self.assertEqual(result["pages"][0]["coverage"], {"status": "complete", "reason": "empty_table"})


class FileScopeTests(unittest.TestCase):
    """Each file tool has its pages judged under its own scope."""

    def setUp(self) -> None:
        self.app = NinovaMcpApp.__new__(NinovaMcpApp)
        self.app._client = SimpleNamespace(
            base_url=BASE, get_html=lambda url: (EMPTY_FOLDER, SimpleNamespace(url=url)),
        )
        self.enterContext(patch.object(self.app, "_resolve_course", return_value={"code": "ZZT 101", "url": COURSE_URL}))
        self.enterContext(patch.object(self.app, "_out", side_effect=lambda payload, **_: payload))
        self.judge = self.enterContext(patch(
            "ninova_mcp.app_ninova.scope_coverage", return_value={"status": "complete"},
        ))

    def scopes(self) -> list[str]:
        return [call.args[3] for call in self.judge.call_args_list]

    def test_lesson_files_are_judged_as_lesson_files(self) -> None:
        result = self.app.get_course_lesson_files("ZZT 101")
        self.assertEqual(result["scope"], "lesson_files")
        self.assertEqual(self.scopes(), ["lesson_files"])
        self.assertEqual(self.judge.call_args.args[2], COURSE_URL + "/DersDosyalari")

    def test_class_files_are_judged_as_class_files(self) -> None:
        result = self.app.get_course_class_files("ZZT 101")
        self.assertEqual(result["scope"], "class_files")
        self.assertEqual(self.scopes(), ["class_files"])

    def test_every_folder_of_a_lesson_file_tree_keeps_the_scope(self) -> None:
        root = COURSE_URL + "/DersDosyalari"
        folder = (
            f'<table>{FILE_HEADER}<tr><td><img src="/img/folder.png"><a href="{root}?g=1">Hafta 1</a></td>'
            "<td></td><td>1 Eylül 2031</td></tr></table>"
        )
        self.app._client.get_html = lambda url: (folder if url == root else EMPTY_FOLDER, SimpleNamespace(url=url))
        self.app.get_course_lesson_files("ZZT 101")
        self.assertEqual(self.scopes(), ["lesson_files", "lesson_files"])

    def test_both_scope_names_are_known_to_the_coverage_check(self) -> None:
        from ninova_mcp.tracking_coverage import scope_coverage

        for scope in ("class_files", "lesson_files"):
            with self.subTest(scope=scope):
                listing = extract_file_directory(EMPTY_FOLDER, COURSE_URL, BASE)
                self.assertEqual(
                    scope_coverage(EMPTY_FOLDER, COURSE_URL, COURSE_URL, scope, listing),
                    {"status": "complete", "reason": "empty_table"},
                )


class TableHeaderTests(unittest.TestCase):
    """A header is a name: it is never rewritten, whatever it looks like."""

    RANGE = "25 Şubat - 31 Aralık 00:00 18:00"
    REWRITTEN = "25 Şubat 00:00 - 31 Aralık 18:00"
    HTML = (
        "<h2>Son Ödevler</h2><table><tr><th>Ödev</th><th>25 Şubat - 31 Aralık 00:00 18:00</th></tr>"
        "<tr><td>Odev 1</td><td>25 Şubat - 31 Aralık 00:00 18:00</td></tr>"
        "<tr><td>Odev 2</td><td>03 Mart 23:59</td></tr></table>"
    )

    def test_a_header_that_looks_like_a_date_range_keeps_its_text(self) -> None:
        table = parsing_page._table_to_rows(make_soup(self.HTML).find("table"))
        self.assertEqual(table["headers"], ["Ödev", self.RANGE])
        first, second = table["rows"]
        # The cell under it is still put in reading order, and keyed by the header as written.
        self.assertEqual(first, {
            "Ödev": "Odev 1",
            self.RANGE: self.REWRITTEN,
            "date_ranges": {self.RANGE: {"start": "25 Şubat 00:00", "end": "31 Aralık 18:00", "raw": self.RANGE}},
        })
        self.assertEqual(second, {"Ödev": "Odev 2", self.RANGE: "03 Mart 23:59"})

    def test_headers_and_rows_are_separate_lists(self) -> None:
        headers, rows = parsing_page._own_rows_text(make_soup(self.HTML).find("table"))
        self.assertEqual(headers, rows[0])
        self.assertIsNot(headers, rows[0])

    def test_the_page_and_the_named_table_show_the_header_as_written(self) -> None:
        page = parse_html_page(PAGE, self.HTML, base_url=BASE)
        self.assertEqual(page["tables"][0]["headers"], ["Ödev", self.RANGE])
        self.assertEqual(list(extract_named_table(self.HTML, "Son Ödevler")[0]), ["Ödev", self.RANGE, "date_ranges"])

    def test_ordinary_headers_are_unchanged(self) -> None:
        html = "<table><tr><th>Ödev</th><th>Teslim Tarihi</th></tr><tr><td>Odev 1</td><td>25 Şubat - 31 Aralık 00:00 18:00</td></tr></table>"
        table = parsing_page._table_to_rows(make_soup(html).find("table"))
        self.assertEqual(table["headers"], ["Ödev", "Teslim Tarihi"])
        self.assertEqual(table["rows"][0]["Teslim Tarihi"], self.REWRITTEN)


if __name__ == "__main__":
    unittest.main()
