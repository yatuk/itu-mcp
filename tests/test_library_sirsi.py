from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from ninova_mcp.library_catalog import CATALOG_URL, parse_record, parse_search, record_url
from ninova_mcp.library_client import LibraryClient, LibraryError


def fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / f"library_sirsi_{name}.html").read_text(encoding="utf-8")


def response(name: str, url: str, status: int = 200) -> requests.Response:
    result = requests.Response()
    result._content = fixture(name).encode()
    result._content_consumed = True
    result.encoding = "utf-8"
    result.url = url
    result.status_code = status
    return result


class SirsiCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = Mock(spec=requests.Session)
        self.session.headers = {}
        with patch.dict(os.environ, {"NINOVA_REQUEST_DELAY_SECONDS": "0"}, clear=True):
            self.client = LibraryClient(session=self.session)
        self.client._min_request_interval = 0

    def test_current_search_fixture_returns_ids_metadata_and_next_page(self) -> None:
        self.session.request.return_value = response("search", CATALOG_URL + "search/results?qu=thermodynamics")
        result = self.client.search("thermodynamics", limit=12)
        self.assertEqual((result["count"], result["total_count"], result["next_offset"]), (12, 5921, 12))
        first = result["records"][0]
        self.assertEqual(first["record_id"], "SD_ILS:69360")
        self.assertEqual(first["title"], "Thermodynamics")
        self.assertEqual(first["isbn"], ["9780070682856"])
        self.assertEqual(first["call_number"], "QC311 .W37 1983A")
        self.assertIn("Wark, Kenneth", first["author"][0])
        self.assertEqual(first["url"], record_url(first["record_id"]))
        self.assertTrue(result["untrusted_external_content"])
        args = self.session.request.call_args
        self.assertEqual(args.args[:2], ("GET", CATALOG_URL + "search/results"))
        self.assertEqual(args.kwargs["params"], {"qu": "thermodynamics", "ps": 12, "rw": 0})

    def test_verified_rw_pagination_has_no_duplicate_first_page(self) -> None:
        first = parse_search(fixture("search"), CATALOG_URL + "search/results")
        self.session.request.return_value = response("search_page2", CATALOG_URL + "search/results?rw=12")
        second = self.client.search("thermodynamics", limit=12, offset=12)
        self.assertEqual(second["first_result"], 13)
        self.assertEqual(second["next_offset"], 24)
        self.assertFalse({r["record_id"] for r in first["records"]} & {r["record_id"] for r in second["records"]})
        self.assertEqual(self.session.request.call_args.kwargs["params"]["rw"], 12)
        self.session.request.return_value = response("search", CATALOG_URL + "search/results")
        with self.assertRaisesRegex(LibraryError, "different result offset"):
            self.client.search("thermodynamics", offset=12)

    def test_isbn_search_follows_current_single_record_redirect(self) -> None:
        redirect = response("empty", CATALOG_URL + "search/results", status=302)
        redirect.headers["Location"] = record_url("SD_ILS:69360")
        self.session.request.side_effect = [redirect, response("detail", record_url("SD_ILS:69360"))]
        result = self.client.search("9780070682856", search_type="isbn")
        self.assertEqual((result["count"], result["total_count"]), (1, 1))
        self.assertEqual(result["records"][0]["record_id"], "SD_ILS:69360")
        self.assertIsNone(result["next_offset"])
        self.assertEqual(self.session.request.call_args_list[0].kwargs["params"]["rt"], "false|||ISBN|||ISBN")
        self.assertEqual([call.args[0] for call in self.session.request.call_args_list], ["GET", "GET"])

    def test_ui_search_fields_have_separate_queries(self) -> None:
        for kind, code in (("title", "TITLE"), ("author", "AUTHOR"), ("subject", "SUBJECT"), ("call_number", "CALLNUMBER")):
            with self.subTest(kind=kind):
                self.session.request.return_value = response("empty", CATALOG_URL + "search/results")
                self.client.search("example", search_type=kind)
                self.assertIn(f"|||{code}|||", self.session.request.call_args.kwargs["params"]["rt"])

    def test_explicit_no_results_is_empty_but_unrecognized_html_is_error(self) -> None:
        self.session.request.return_value = response("empty", CATALOG_URL + "search/results")
        result = self.client.search("nonexistentbook")
        self.assertEqual((result["count"], result["total_count"]), (0, 0))
        self.assertIsNone(result["next_offset"])
        self.session.request.return_value._content = b"<html><h1>Login or retry later</h1></html>"
        with self.assertRaisesRegex(LibraryError, "not an empty search result"):
            self.client.search("example")

    def test_malformed_result_is_not_silently_dropped(self) -> None:
        html = fixture("search").replace("SD_ILS:69360", "UNSUPPORTED:69360")
        with self.assertRaisesRegex(ValueError, "unrecognized result"):
            parse_search(html, CATALOG_URL + "search/results")

    def test_current_detail_preserves_copies_with_unknown_async_status(self) -> None:
        self.session.request.return_value = response("detail", record_url("SD_ILS:69360"))
        item = self.client.get_item("SD_ILS:69360")
        self.assertEqual(item["fields"]["publication"], "New York : McGraw-Hill, c1983.")
        self.assertEqual(item["copy_count"], 1)
        copy = item["copies"][0]
        self.assertEqual(copy["barcode"], "003003607001")
        self.assertTrue(copy["status_pending"])
        self.assertIsNone(copy["status"])
        result = self.client.check_availability("SD_ILS:69360")
        self.assertIsNone(result["available"])
        self.assertEqual(result["unknown_copy_count"], 1)
        self.assertIn("asynchronously", result["availability_warning"])
        self.assertEqual(result["platform"], "sirsi_portfolio")
        self.session.request.assert_called_once()  # No JavaScript, account, or availability POST.

    def test_detail_identity_is_checked_before_caching(self) -> None:
        self.session.request.return_value = response("detail", record_url("SD_ILS:69361"))
        with self.assertRaisesRegex(LibraryError, "different record"):
            self.client.get_item("SD_ILS:69361")
        with self.assertRaises(ValueError):
            parse_record("<h1>Catalog error</h1>", record_url("SD_ILS:69360"))

    def test_transport_error_is_not_an_empty_or_unavailable_book(self) -> None:
        self.session.request.return_value = response("empty", CATALOG_URL + "search/results", status=403)
        with self.assertRaisesRegex(LibraryError, "HTTP 403.*transport is unavailable"):
            self.client.search("example")
        self.assertTrue(self.session.request.call_args.kwargs["verify"])

    def test_current_account_operations_never_read_credentials_or_submit(self) -> None:
        with patch.object(self.client, "_credentials", side_effect=AssertionError("Credentials must not be accessed")):
            for operation in (
                self.client.get_account, self.client.list_loans,
                lambda: self.client.renew_loan("123", confirm=True),
                lambda: self.client.reserve_item("SD_ILS:69360", confirm=True),
                lambda: self.client.reserve_item("SD_ILS:69360", confirm=False),
            ):
                with self.subTest(operation=operation), self.assertRaisesRegex(LibraryError, "not implemented"):
                    operation()
        self.session.request.assert_not_called()

    def test_untrusted_urls_and_record_ids_cannot_choose_network_targets(self) -> None:
        for url in ("https://katalog.kutuphane.itu.edu.tr.attacker.example", "https://user@katalog.kutuphane.itu.edu.tr", "http://katalog.kutuphane.itu.edu.tr", "https://katalog.kutuphane.itu.edu.tr:8443"):
            with self.subTest(url=url), self.assertRaises(LibraryError):
                LibraryClient(base_url=url)
        for rid in ("b1179767", "https://attacker.example/", "SD_ILS:1/../2", "SD_ILS:1234567890123"):
            with self.subTest(rid=rid), self.assertRaises(LibraryError):
                self.client.get_item(rid)
        self.session.request.assert_not_called()

    def test_cross_host_redirect_is_rejected_before_request(self) -> None:
        redirect = response("empty", CATALOG_URL + "search/results", status=302)
        redirect.headers["Location"] = "https://attacker.example/"
        self.session.request.return_value = redirect
        with self.assertRaises(LibraryError):
            self.client.search("example")
        self.session.request.assert_called_once()

    def test_tool_wrapper_forwards_pagination(self) -> None:
        from ninova_mcp.server import NinovaMcpApp, TOOLS
        app = object.__new__(NinovaMcpApp)
        app._library = Mock()
        app.library.search.return_value = {"count": 0}
        self.assertEqual(app.library_search("example", limit=12, offset=24), {"count": 0})
        app.library.search.assert_called_once_with("example", search_type="keyword", limit=12, offset=24)
        definitions = {tool["name"]: tool for tool in TOOLS}
        self.assertIn("offset", definitions["library_search"]["inputSchema"]["properties"])
        self.assertIn("SD_ILS", definitions["library_get_item"]["inputSchema"]["properties"]["record_id"]["pattern"])


if __name__ == "__main__":
    unittest.main()
