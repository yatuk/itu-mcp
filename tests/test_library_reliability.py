from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from ninova_mcp.library_client import LibraryClient, LibraryError


class LibraryAvailabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.client = LibraryClient()

    def availability(self, copies: list[object], **extra: object) -> dict[str, object]:
        item = {
            "record_id": "b1179767",
            "title": "Example Book",
            "copies": copies,
            "url": "https://divit.library.itu.edu.tr/record=b1179767",
            **extra,
        }
        with patch.object(self.client, "get_item", return_value=item):
            return self.client.check_availability("b1179767")

    def test_unavailable_is_not_matched_as_available(self) -> None:
        result = self.availability([{"status": "UNAVAILABLE"}])
        self.assertIs(result["available"], False)
        self.assertEqual(result["available_copy_count"], 0)
        self.assertEqual(result["unavailable_copy_count"], 1)
        self.assertEqual(result["unknown_copy_count"], 0)

    def test_title_location_and_notes_do_not_override_copy_status(self) -> None:
        result = self.availability([{
            "title": "Available Materials",
            "location": "Check Shelf Learning Center",
            "notes": "Available on request",
            "status": "ON LOAN",
        }])
        self.assertIs(result["available"], False)
        self.assertEqual(result["available_copy_count"], 0)

    def test_positive_statuses_are_normalized_and_use_status_fields(self) -> None:
        for key, status in (("status", "  CHECK   SHELF "), ("statusu", "Available"), ("durum", "RAFTA")):
            with self.subTest(key=key, status=status):
                result = self.availability([{key: status}])
                self.assertIs(result["available"], True)
                self.assertEqual(result["available_copy_count"], 1)

    def test_explicit_negative_statuses_are_unavailable(self) -> None:
        for status in ("NOT AVAILABLE", "CHECKED OUT", "ON LOAN", "ödünçte"):
            with self.subTest(status=status):
                self.assertIs(self.availability([{"status": status}])["available"], False)

    def test_unrecognized_statuses_remain_unknown(self) -> None:
        for status in ("Searching...", "Arıyor...", "Available on request", "DUE 20-09-2026", ""):
            with self.subTest(status=status):
                result = self.availability([{"status": status}])
                self.assertIsNone(result["available"])
                self.assertEqual(result["unavailable_copy_count"], 0)
                self.assertEqual(result["unknown_copy_count"], 1)
                self.assertIn("availability_warning", result)

    def test_missing_or_malformed_status_is_unknown(self) -> None:
        for copy in ({"location": "Available Collection"}, {"status": None}, {"status": 1}, "CHECK SHELF"):
            with self.subTest(copy=copy):
                result = self.availability([copy])
                self.assertIsNone(result["available"])
                self.assertEqual(result["unknown_copy_count"], 1)

    def test_conflicting_status_fields_are_unknown(self) -> None:
        result = self.availability([{"status": "AVAILABLE", "durum": "ödünçte"}])
        self.assertIsNone(result["available"])
        self.assertEqual(result["unknown_copy_count"], 1)

    def test_no_copy_evidence_is_not_a_negative_availability_result(self) -> None:
        result = self.availability([], parse_warning="The catalog copy table was not found.")
        self.assertIsNone(result["available"])
        self.assertEqual(result["copy_count"], 0)
        self.assertEqual(result["available_copy_count"], 0)
        self.assertIn("parse_warning", result)
        self.assertIn("availability_warning", result)

    def test_unavailable_and_unknown_copies_do_not_prove_unavailability(self) -> None:
        result = self.availability([{"status": "UNAVAILABLE"}, {"status": "Searching..."}])
        self.assertIsNone(result["available"])
        self.assertEqual(result["unavailable_copy_count"], 1)
        self.assertEqual(result["unknown_copy_count"], 1)

    def test_one_available_copy_proves_availability_despite_unknown_copies(self) -> None:
        copies = [{"status": "CHECK SHELF"}, {"status": "Searching..."}]
        result = self.availability(copies)
        self.assertIs(result["available"], True)
        self.assertEqual(result["available_copy_count"], 1)
        self.assertEqual(result["unknown_copy_count"], 1)
        self.assertEqual(result["copies"], copies)
        self.assertEqual(result["record_id"], "b1179767")
        self.assertEqual(result["title"], "Example Book")
        self.assertTrue(result["untrusted_external_content"])


class LibraryConnectionTests(unittest.TestCase):
    def test_certificate_failure_keeps_verification_and_does_not_retry_new_platform(self) -> None:
        session = Mock(spec=requests.Session)
        session.headers = {}
        session.request.side_effect = requests.exceptions.SSLError("certificate has expired")
        with patch.dict(os.environ, {}, clear=True):
            client = LibraryClient(session=session)
            with self.assertRaises(LibraryError) as raised:
                client.search("python")
        self.assertIn("TLS verification remains enabled", str(raised.exception))
        self.assertIn("https://katalog.kutuphane.itu.edu.tr/client/tr_TR/default/", str(raised.exception))
        self.assertIn("different catalog platform", str(raised.exception))
        session.request.assert_called_once()
        self.assertIs(session.request.call_args.kwargs["verify"], True)
        self.assertIs(session.request.call_args.kwargs["allow_redirects"], False)

    def test_custom_ca_bundle_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "library-ca.pem"
            bundle.write_text("test certificate placeholder", encoding="utf-8")
            with patch.dict(os.environ, {"NINOVA_LIBRARY_CA_BUNDLE": str(bundle)}, clear=True):
                client = LibraryClient()
                self.assertEqual(client._verify_value(), str(bundle.resolve()))

    def test_modern_catalog_does_not_silently_accept_legacy_routes(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LibraryError):
                LibraryClient(base_url="https://katalog.kutuphane.itu.edu.tr")


if __name__ == "__main__":
    unittest.main()
