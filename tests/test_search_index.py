"""The local search index: Turkish folding, safe queries, feeding and the tool."""

from __future__ import annotations

import asyncio
import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mcp.server.fastmcp import FastMCP

from ninova_mcp import search_index
from ninova_mcp.app_search import snapshot_documents
from ninova_mcp.client import NinovaError
from ninova_mcp.search_index import SearchIndex, fold, make_snippet, match_expression, query_tokens
from ninova_mcp.server import NinovaMcpApp
from ninova_mcp.server_build import register_tools
from ninova_mcp.text_extract import extract_text_from_bytes
from ninova_mcp.tool_registry import REMOTE_TOOL_NAMES, STATEFUL_TOOL_NAMES, TOOLS
from ninova_mcp.toolsets import TOOLSETS
from ninova_mcp.tracking import SNAPSHOT_SCOPES

from recording import offline_app

BASE = "https://ninova.itu.edu.tr"
COURSE = {"code": "BLG 335E", "title": "Algoritma Analizi", "url": BASE + "/Sinif/1.1"}
OTHER = {"code": "MAT 281", "title": "Lineer Cebir", "url": BASE + "/Sinif/1.2"}


def doc(key: str, title: str, body: str = "", *, kind: str = "announcement", course: dict = COURSE, **extra) -> dict:
    return {
        "kind": kind, "source_key": key, "title": title, "body": body, "course_url": course["url"],
        "course_code": course["code"], "course_title": course["title"], **extra,
    }


def snapshot(**overview) -> dict:
    return {
        "course": COURSE, "captured_at": "2026-10-01T00:00:00+00:00", "errors": [],
        "coverage": {scope: {"status": "complete"} for scope in SNAPSHOT_SCOPES},
        "overview": {
            "info": {}, "sections": [], "announcements": [], "assignments": [], "class_files": [],
            "lesson_files": [], "grades": {"grades": []}, "message_board": {"topics": []},
            "attendance": {"weeks": []}, "remote_learning": {"active_sessions": [], "past_sessions": []},
            **overview,
        },
    }


class IndexCase(unittest.TestCase):
    force_scan = False

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        self.index = SearchIndex(self.dir / "search.sqlite3", force_scan=self.force_scan)

    def titles(self, query: str, **options) -> list[str]:
        return sorted(item["title"] for item in self.index.search(query, **options)["results"])


class FoldingTests(IndexCase):
    def test_fold_is_one_to_one(self) -> None:
        text = "İSTANBUL Iğdır ışık ŞÖLEN çÇ ÜĞ"
        self.assertEqual(fold(text), "istanbul igdir isik solen cc ug")
        self.assertEqual(len(fold(text)), len(text))
        self.assertNotEqual(len(text.lower()), len(text))  # why str.lower() is not used

    def test_turkish_spellings_find_each_other(self) -> None:
        self.index.upsert([
            doc("a", "Sınav duyurusu", "Vize sınavı 12 Kasım'da."),
            doc("b", "SINAV SALONLARI", "Salon listesi ektedir."),
            doc("c", "Ödev 3", "İstanbul yol ağı üzerinde en kısa yol."),
            doc("d", "Ders iptali", "Perşembe dersi yapılmayacak."),
        ])
        for query in ("sınav", "sinav", "SINAV", "Sınavı"):
            with self.subTest(query=query):
                expected = ["Sınav duyurusu"] if query == "Sınavı" else ["SINAV SALONLARI", "Sınav duyurusu"]
                self.assertEqual(self.titles(query), expected)
        for query in ("ödev", "odev", "ÖDEV"):
            self.assertEqual(self.titles(query), ["Ödev 3"])
        for query in ("İstanbul", "istanbul", "ISTANBUL", "ıstanbul"):
            self.assertEqual(self.titles(query), ["Ödev 3"])

    def test_snippet_offsets_survive_turkish_capitals(self) -> None:
        body = "İ" * 300 + " İLK SINAV TARİHİ açıklandı. " + "x" * 300
        snippet = make_snippet(body, query_tokens("sinav"))
        self.assertIn("İLK SINAV TARİHİ", snippet)
        self.assertLessEqual(len(snippet), 240)
        self.assertTrue(snippet.startswith("…") and snippet.endswith("…"))
        self.index.upsert([doc("a", "Duyuru", body)])
        result = self.index.search("sınav tarihi")["results"][0]
        self.assertIn("SINAV TARİHİ", result["snippet"])
        self.assertLessEqual(len(result["snippet"]), 240)


class QueryTests(IndexCase):
    def setUp(self) -> None:
        super().setUp()
        self.index.upsert([
            doc("a", "Vize", "Sınavda hesap makinesi serbest."),
            doc("b", "Notlar", "Notlar açıklandı, itiraz haftaya."),
            doc("c", "Proje", "Proje teslimi 5 Ocak.", course=OTHER, kind="assignment"),
        ])

    def test_prefix_only_from_four_characters(self) -> None:
        self.assertEqual(match_expression(["sinav", "not"]), '"sinav"* AND "not"')
        self.assertEqual(self.titles("sinav"), ["Vize"])        # sinav* finds Sınavda
        self.assertEqual(self.titles("itir"), ["Notlar"])       # itir* finds itiraz
        if self.index.engine == "fts5":
            self.assertEqual(self.titles("not"), [])            # three letters: whole word only

    def test_all_words_first_then_any_word(self) -> None:
        both = self.index.search("notlar itiraz")
        self.assertEqual((both["matched"], both["total_count"]), ("all", 1))
        either = self.index.search("itiraz makinesi")
        self.assertEqual(either["matched"], "any")
        self.assertEqual(sorted(item["title"] for item in either["results"]), ["Notlar", "Vize"])
        nothing = self.index.search("kuantum")
        self.assertEqual((nothing["status"], nothing["count"]), ("no_match", 0))

    def test_hostile_queries_never_raise(self) -> None:
        for query in ('"', '""', 'NEAR(', 'NEAR(a b, 2)', 'title:', 'body : x', '*', '-', '^a', 'a AND', 'OR', "'; DROP TABLE documents; --",
                      '(', ')', '{a}', 'a\x00b', '%', '_', '', '   ', 'sınav" OR "x', 'col:sinav*'):
            with self.subTest(query=query):
                result = self.index.search(query)
                self.assertIn(result["status"], {"ok", "no_match", "empty_query"})
        self.assertEqual(self.index.stats()["doc_count"], 3)
        self.assertEqual(self.index.search("*")["status"], "empty_query")

    def test_filters_paging_and_shape(self) -> None:
        self.assertEqual(self.titles("proje", course="mat281"), ["Proje"])
        self.assertEqual(self.titles("proje", course="BLG 335E"), [])
        self.assertEqual(self.titles("proje", course="lineer"), ["Proje"])
        self.assertEqual(self.titles("proje", kinds=["announcement"]), [])
        self.index.upsert([doc(f"p{n}", f"Sınav {n}", "sınav") for n in range(30)])
        first = self.index.search("sınav", limit=100)
        self.assertEqual((first["count"], first["total_count"], first["next_offset"]), (25, 31, 25))
        last = self.index.search("sınav", limit=25, offset=25)
        self.assertEqual((last["count"], last["next_offset"]), (6, None))
        self.assertEqual(set(first["results"][0]), {"kind", "course", "title", "snippet", "page", "date", "url", "score"})
        self.assertEqual(first["results"][0]["course"], {"code": "BLG 335E", "title": "Algoritma Analizi"})


class ScanQueryTests(QueryTests):
    """The same behaviour without FTS5."""

    force_scan = True

    def test_engine_is_reported(self) -> None:
        self.assertEqual(self.index.search("sinav")["engine"], "scan")
        self.assertEqual(self.titles("SINAVDA"), ["Vize"])

    def test_missing_fts5_selects_the_scan(self) -> None:
        with patch.object(search_index, "_fts5_available", False):
            index = SearchIndex(self.index.path)
        self.assertEqual(index.engine, "scan")

    def test_file_written_by_one_engine_is_readable_by_the_other(self) -> None:
        fts = SearchIndex(self.index.path)
        self.assertEqual(sorted(item["title"] for item in fts.search("sınav")["results"]), ["Vize"])
        self.assertEqual(self.titles("sinav"), ["Vize"])


class MaintenanceTests(IndexCase):
    def test_unchanged_documents_are_skipped(self) -> None:
        documents = [doc("a", "Vize", "Sınav 12 Kasım"), doc("b", "Final", "Sınav 5 Ocak")]
        self.assertEqual(self.index.upsert(documents), {"indexed": 2, "unchanged": 0, "deleted": 0})
        self.assertEqual(self.index.upsert(documents), {"indexed": 0, "unchanged": 2, "deleted": 0})
        documents[0]["body"] = "Sınav 19 Kasım'a ertelendi"
        self.assertEqual(self.index.upsert(documents), {"indexed": 1, "unchanged": 1, "deleted": 0})
        self.assertEqual(self.titles("ertelendi"), ["Vize"])
        self.assertEqual(self.titles("12"), [])
        self.assertEqual(self.index.stats()["doc_count"], 2)

    def test_replace_prefix_and_course_pruning_delete(self) -> None:
        self.index.upsert([doc("sync|x|1", "Bir", "sınav"), doc("sync|x|2", "İki", "sınav"),
                           doc("other", "Üç", "sınav", course=OTHER)])
        counts = self.index.replace_prefix("sync|x|", [doc("sync|x|2", "İki", "sınav")])
        self.assertEqual(counts, {"indexed": 0, "unchanged": 1, "deleted": 1})
        self.assertEqual(self.titles("sınav"), ["Üç", "İki"])
        self.assertEqual(self.index.prune_courses([COURSE["url"]]), 1)
        self.assertEqual(self.titles("sınav"), ["İki"])

    def test_oldest_documents_are_evicted_over_the_cap(self) -> None:
        index = SearchIndex(self.index.path, max_total_chars=250)
        for n in range(4):
            index.upsert([doc(f"k{n}", f"D{n}", "sınav " + "x" * 94)])
        self.assertEqual(sorted(item["title"] for item in index.search("sınav")["results"]), ["D2", "D3"])

    def test_clear_and_empty_index(self) -> None:
        self.assertEqual(self.index.search("sınav")["status"], "empty_index")
        self.assertFalse(self.index.path.exists())  # searching does not create the file
        self.index.upsert([doc("a", "Vize", "sınav")])
        self.assertEqual(self.index.clear(), 1)
        self.assertEqual(self.index.search("sınav")["status"], "empty_index")
        with contextlib.closing(sqlite3.connect(self.index.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM docs_folded").fetchone()[0], 0)


class FeedingTests(unittest.TestCase):
    def test_snapshot_documents_use_the_stored_fields(self) -> None:
        documents = snapshot_documents(COURSE, snapshot(
            announcements=[{"title": "Vize", "url": "u1", "summary": "Sınav salonu D5102", "published_at_iso": "2026-10-01T10:00:00+03:00"}],
            assignments=[{"title": "Ödev 1", "url": "u2", "description": "Dijkstra uygulayın", "submission_end": "20 Ekim 2026 23:59"}],
            message_board={"topics": [{"title": "Proje grupları", "url": "u3", "last_message_text": "Grup arıyorum"}]},
            class_files=[{"name": "Hafta1.pdf", "path": "/Slaytlar/Hafta1.pdf", "url": "u4", "entry_type": "file"},
                         {"name": "Slaytlar", "path": "/Slaytlar", "url": "u5", "entry_type": "folder"}],
        ))
        self.assertEqual([item["kind"] for item in documents], ["announcement", "assignment", "message_topic", "file_name"])
        self.assertEqual(documents[0]["date"], "2026-10-01T10:00:00+03:00")
        self.assertIn("Son teslim: 20 Ekim 2026 23:59", documents[1]["body"])
        self.assertEqual(len({item["source_key"] for item in documents}), 4)
        self.assertTrue(all(item["course_code"] == "BLG 335E" for item in documents))

    def make_app(self) -> NinovaMcpApp:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = NinovaMcpApp.__new__(NinovaMcpApp)
        app.state_dir = Path(directory.name)
        app.tracking_state_path = app.state_dir / "tracking-state.json"
        app.list_courses = Mock(return_value={"courses": [COURSE], "enrollment_coverage": {"status": "complete"}})
        self.announcements = [{"title": "Vize tarihi", "url": "u1", "summary": "Sınav 12 Kasım"}]
        app._collect_course_snapshot = Mock(side_effect=lambda item, **kwargs: snapshot(announcements=list(self.announcements)))
        return app

    def test_sync_feeds_the_index_incrementally_and_reports_changes(self) -> None:
        app = self.make_app()
        first = app.sync_all_courses()
        self.assertEqual(first["search_index"], {"indexed": 1, "unchanged": 0, "deleted": 0})
        self.assertEqual(first["change_summary"]["headline"], "Değişiklik yok.")
        self.announcements.append({"title": "Final tarihi", "url": "u2", "summary": "Sınav 5 Ocak"})
        second = app.sync_all_courses()
        self.assertEqual(second["search_index"], {"indexed": 1, "unchanged": 1, "deleted": 0})
        self.assertEqual(second["change_summary"]["courses"][0]["lines"][0]["text"], "Yeni duyuru: Final tarihi")
        self.assertEqual(len(second["updates"]), 1)
        found = app.search_content("sinav", course="blg335e")
        self.assertEqual((found["status"], found["total_count"]), ("ok", 2))
        del self.announcements[0]
        self.assertEqual(app.sync_all_courses()["search_index"]["deleted"], 1)

    def test_a_course_that_left_the_list_leaves_the_index(self) -> None:
        app = self.make_app()
        app.sync_all_courses()
        app.list_courses.return_value = {"courses": [OTHER], "enrollment_coverage": {"status": "complete"}}
        app.sync_all_courses()
        self.assertEqual(app.search_content("sinav", course="BLG 335E")["total_count"], 0)
        self.assertEqual(app.search_content("sinav", course="MAT 281")["total_count"], 1)

    def test_a_broken_index_does_not_fail_the_sync(self) -> None:
        app = self.make_app()
        with patch("ninova_mcp.app_search.SearchIndex", side_effect=sqlite3.OperationalError("disk I/O error")):
            result = app.sync_all_courses()
        self.assertEqual(result["search_index"], {"error": "disk I/O error"})
        self.assertEqual(result["course_count"], 1)

    def test_pdf_pages_are_handed_over_without_changing_the_result(self) -> None:
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)
        pages: list = []
        with patch("pypdf._page.PageObject.extract_text", side_effect=["Birinci sayfa", "", "Üçüncü sayfa"]), \
                patch("pypdf.PdfReader.pages", new=[writer.pages[0]] * 3):
            plain = extract_text_from_bytes(buffer.getvalue(), extension=".pdf")
        with patch("pypdf._page.PageObject.extract_text", side_effect=["Birinci sayfa", "", "Üçüncü sayfa"]), \
                patch("pypdf.PdfReader.pages", new=[writer.pages[0]] * 3):
            fed = extract_text_from_bytes(buffer.getvalue(), extension=".pdf", pages_out=pages)
        self.assertEqual(plain, fed)
        self.assertEqual(pages, [(1, "Birinci sayfa"), (3, "Üçüncü sayfa")])
        text_pages: list = []
        extract_text_from_bytes("uzun metin".encode(), extension=".txt", max_chars=4, pages_out=text_pages)
        self.assertEqual(text_pages, [(None, "uzun metin")])


class FileToolTests(unittest.TestCase):
    def test_read_and_download_index_text_and_the_index_file_is_not_readable(self) -> None:
        body = "Vize sınavı 12 Kasım Çarşamba günü yapılacaktır.".encode("utf-8")
        app, adapter = offline_app(self, {
            BASE + "/Sinif/1.1/SinifDosyalari/duyuru.txt": (200, {"Content-Type": "text/plain"}, body),
            BASE + "/f/ek.txt": (200, {"Content-Type": "text/plain"}, "Bütünleme sınavı takvimi".encode("utf-8")),
        })
        empty = app.search_content("sınav")
        self.assertEqual(empty["status"], "empty_index")
        self.assertIn("sync_all_courses", empty["message"])
        self.assertIn("read_resource_text", empty["message"])

        result = app.read_resource_text(url="/Sinif/1.1/SinifDosyalari/duyuru.txt")
        self.assertNotIn("search_index", result)
        saved = app.download_resource("/f/ek.txt")
        requests_before = len(adapter.requests) if hasattr(adapter, "requests") else None
        found = app.search_content("SINAV")
        self.assertEqual(found["total_count"], 2)
        self.assertEqual({item["kind"] for item in found["results"]}, {"file_text"})
        self.assertEqual(found["index"]["doc_count"], 2)
        if requests_before is not None:
            self.assertEqual(len(adapter.requests), requests_before)
        # Reading the saved copy by path adds a third document, a second read adds nothing.
        app.read_resource_text(path=saved["path"])
        app.read_resource_text(path=saved["path"])
        self.assertEqual(app.search_content("bütünleme")["total_count"], 2)

        index_path = app.state_dir / "search.sqlite3"
        self.assertTrue(index_path.is_file())
        for path in (str(index_path), "../search.sqlite3"):
            with self.subTest(path=path), self.assertRaises(NinovaError):
                app.read_resource_text(path=path)

    def test_unknown_kind_is_refused(self) -> None:
        app, _ = offline_app(self)
        with self.assertRaises(NinovaError):
            app.search_content("sınav", kinds=["grades"])


class RegistrationTests(unittest.TestCase):
    def test_tool_is_registered_like_get_updates(self) -> None:
        names = [tool["name"] for tool in TOOLS]
        self.assertIn("search_content", names)
        self.assertIn("search_content", TOOLSETS["ninova"][1])
        self.assertEqual("search_content" in REMOTE_TOOL_NAMES, "get_updates" in REMOTE_TOOL_NAMES)
        self.assertNotIn("search_content", STATEFUL_TOOL_NAMES)

    def test_published_schema_has_types_and_descriptions(self) -> None:
        server = FastMCP("search")
        register_tools(server, NinovaMcpApp.__new__(NinovaMcpApp), ["search_content", "get_updates"])
        tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        schema = tools["search_content"].inputSchema
        self.assertEqual(schema["required"], ["query"])
        self.assertEqual(set(schema["properties"]), {"query", "course", "kinds", "limit", "offset"})
        for name, prop in schema["properties"].items():
            with self.subTest(parameter=name):
                self.assertTrue(prop.get("description"))
        self.assertEqual(schema["properties"]["limit"]["maximum"], 25)
        self.assertTrue(tools["search_content"].annotations.readOnlyHint)
        updates = tools["get_updates"].inputSchema["properties"]
        self.assertEqual(updates["summary"]["type"], "boolean")
        self.assertTrue(updates["summary"]["description"] and updates["since"]["description"])


if __name__ == "__main__":
    unittest.main()
