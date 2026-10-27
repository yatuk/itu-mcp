"""Open article index search, with a canned OpenAlex response."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ninova_mcp import article_search
from ninova_mcp.article_search import (
    ArticleSearchClient,
    ArticleSearchError,
    itu_access_url,
    rebuild_abstract,
)

WORK = {
    "id": "https://openalex.org/W1",
    "doi": "https://doi.org/10.1007/s41105-023-00457-1",
    "title": "Sleep quality and   academic performance",
    "publication_year": 2023,
    "type": "article",
    "language": "en",
    "cited_by_count": 27,
    "authorships": [{"author": {"display_name": f"Author {i}"}} for i in range(8)],
    "primary_location": {"source": {"display_name": "Sleep and Biological Rhythms"}},
    "open_access": {"is_oa": True, "oa_url": "https://link.springer.com/x.pdf"},
    "abstract_inverted_index": {"Sleep": [0], "matters": [1], "for": [2], "students": [3]},
}


class FakeResponse:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class ArticleSearchTests(unittest.TestCase):
    def make(self, payload=None, status: int = 200) -> ArticleSearchClient:
        payload = {"meta": {"count": 30}, "results": [WORK]} if payload is None else payload
        self.calls: list[tuple[str, dict]] = []

        def fake(session, method, url, **kwargs):
            self.calls.append((url, kwargs))
            return FakeResponse(payload, status)

        patcher = patch.object(article_search, "request_with_safe_redirects", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return ArticleSearchClient()

    def test_result_shape_and_itu_link(self) -> None:
        result = self.make().search("sleep students", limit=10)
        work = result["articles"][0]
        self.assertEqual(work["title"], "Sleep quality and academic performance")
        self.assertEqual(work["journal"], "Sleep and Biological Rhythms")
        self.assertEqual(work["abstract"], "Sleep matters for students")
        self.assertEqual(len(work["authors"]), 6)
        self.assertEqual(work["more_authors"], 2)
        self.assertEqual(
            work["itu_access_url"],
            "https://go.openathens.net/redirector/itu.edu.tr?url=https://doi.org/10.1007/s41105-023-00457-1",
        )
        self.assertTrue(result["has_more"])
        self.assertTrue(result["untrusted_external_content"])

    def test_filters_and_sort_reach_the_query(self) -> None:
        client = self.make()
        client.search("sleep", from_year=2020, to_year=2024, open_access_only=True,
                      article_type="review", sort="most_cited", limit=5, page=2)
        params = self.calls[0][1]["params"]
        self.assertIn("type:review", params["filter"])
        self.assertIn("from_publication_date:2020-01-01", params["filter"])
        self.assertIn("to_publication_date:2024-12-31", params["filter"])
        self.assertIn("is_oa:true", params["filter"])
        self.assertEqual(params["sort"], "cited_by_count:desc")
        self.assertEqual((params["per_page"], params["page"]), (5, 2))

    def test_results_are_cached_and_api_key_is_optional(self) -> None:
        client = self.make()
        with patch.dict(os.environ, {"NINOVA_OPENALEX_API_KEY": "k123"}):
            client.search("sleep")
            client.search("sleep")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1]["params"]["api_key"], "k123")

    def test_bad_input_is_refused_before_any_request(self) -> None:
        client = self.make()
        for kwargs in ({"query": "x"}, {"query": "sleep", "sort": "bogus"},
                       {"query": "sleep", "article_type": "poem"}, {"query": "a" * 301}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ArticleSearchError):
                client.search(**kwargs)
        self.assertEqual(self.calls, [])

    def test_rate_limit_message(self) -> None:
        with self.assertRaisesRegex(ArticleSearchError, "NINOVA_OPENALEX_API_KEY"):
            self.make(payload={}, status=429).search("sleep")

    def test_helpers(self) -> None:
        self.assertEqual(rebuild_abstract({"b": [1], "a": [0]}), "a b")
        self.assertEqual(rebuild_abstract(None), "")
        self.assertIsNone(itu_access_url(None))
        self.assertIsNone(itu_access_url("https://evil.example.com/x"))

    def test_only_openalex_host_is_allowed(self) -> None:
        with self.assertRaises(ArticleSearchError):
            article_search._validate_url("https://evil.example.com/works")
        article_search._validate_url(article_search.WORKS_URL)


if __name__ == "__main__":
    unittest.main()
