"""Library database list search, with a canned service response."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from ninova_mcp import library_databases
from ninova_mcp.library_databases import LibraryDatabasesClient, LibraryDatabasesError, fold, plain_text


def _flt(type_id: int, name: str) -> dict:
    return {"Id": 1, "Adi": name, "Adi_en": name, "FiltreTurId": type_id}


ROWS = [
    {
        "Id": 1, "Adi": "IEEE Xplore Digital Library", "Link": "https://ieeexplore.ieee.org/",
        "KampusDisiLink": "https://go.openathens.net/redirector/itu.edu.tr?url=x",
        "Aciklama": "<p>Mühendislik dergileri &amp; standartlar</p>", "Aciklama_en": "<p>Engineering journals</p>",
        "Icerik": "Tam Metin", "YeniMi": False, "AcikErisim": False,
        "Filtreler": [_flt(1, "Electrical-Electronics Engineering"), _flt(3, "IEEE")],
    },
    {
        "Id": 2, "Adi": "JSTOR", "Link": "https://www.jstor.org/", "KampusDisiLink": None,
        "Aciklama": "<p>Humanities and literature archive</p>", "Aciklama_en": None,
        "Icerik": "Tam Metin", "YeniMi": True, "AcikErisim": False,
        "Filtreler": [_flt(1, "Language and Literature"), _flt(3, "ITHAKA")],
    },
    {
        "Id": 3, "Adi": "Turnitin", "Link": "https://turnitin.com/", "Aciklama": "Plagiarism check",
        "Filtreler": [_flt(1, "Education and Teaching")],
    },
]


class FakeResponse:
    status_code = 200

    def __init__(self, payload) -> None:
        self._payload = payload
        self.content = b"{}"

    def json(self):
        return self._payload


class LibraryDatabasesTests(unittest.TestCase):
    def make(self, payload=None) -> LibraryDatabasesClient:
        payload = {"Toplam": len(ROWS), "Liste": ROWS} if payload is None else payload
        self.calls: list[str] = []

        def fake(session, method, url, **kwargs):
            self.calls.append(url)
            return FakeResponse(payload)

        patcher = patch.object(library_databases, "request_with_safe_redirects", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return LibraryDatabasesClient()

    def test_search_ranks_name_and_subject_matches(self) -> None:
        result = self.make().search("literature")
        self.assertEqual([d["name"] for d in result["databases"]], ["JSTOR"])
        self.assertEqual(result["databases"][0]["subjects"], ["Language and Literature"])
        self.assertTrue(result["databases"][0]["is_new"])

    def test_any_word_matches_and_more_words_rank_higher(self) -> None:
        result = self.make().search("engineering journals jstor")
        names = [d["name"] for d in result["databases"]]
        self.assertEqual(set(names), {"IEEE Xplore Digital Library", "JSTOR"})

    def test_turkish_diacritics_are_ignored(self) -> None:
        result = self.make().search("muhendislik")
        self.assertEqual([d["name"] for d in result["databases"]], ["IEEE Xplore Digital Library"])
        self.assertEqual(fold("Mühendislik İSTANBUL"), "muhendislik istanbul")

    def test_subject_filter_and_subject_list(self) -> None:
        client = self.make()
        self.assertEqual([d["name"] for d in client.search(subject="education")["databases"]], ["Turnitin"])
        listing = client.search()
        self.assertEqual(listing["total_databases"], 3)
        self.assertEqual(len(listing["subjects"]), 3)

    def test_description_is_plain_text_and_links_are_kept(self) -> None:
        record = self.make().search("ieee")["databases"][0]
        self.assertEqual(record["description"], "Engineering journals")
        self.assertEqual(record["url"], "https://ieeexplore.ieee.org/")
        self.assertIn("openathens", record["off_campus_url"])
        self.assertEqual(plain_text("<b>a</b>&nbsp;&amp; b"), "a & b")
        self.assertTrue(record["page_url"].endswith("#1"))
        self.assertNotIn("_haystack", record)

    def test_limit_and_truncation_and_cache(self) -> None:
        client = self.make()
        result = client.search("a", limit=1)
        self.assertLessEqual(len(result["databases"]), 1)
        client.search("jstor")
        self.assertEqual(len(self.calls), 1)

    def test_only_web_links_are_returned(self) -> None:
        rows = [dict(ROWS[0], Link="javascript:alert(1)", KampusDisiLink="data:text/html,x")]
        record = self.make({"Liste": rows}).search("ieee")["databases"][0]
        self.assertIsNone(record["url"])
        self.assertIsNone(record["off_campus_url"])

    def test_unreadable_payload_is_an_error(self) -> None:
        with self.assertRaises(LibraryDatabasesError):
            self.make(payload={"unexpected": True}).search("x")

    def test_only_the_library_service_host_is_allowed(self) -> None:
        with self.assertRaises(LibraryDatabasesError):
            library_databases._validate_url("https://evil.example.com/web/api/Veritabanlari/FullList")
        library_databases._validate_url(library_databases.FULL_LIST_URL)


if __name__ == "__main__":
    unittest.main()
