"""Pins what the parsers of ``ninova_mcp.parsing`` return, page by page.

The pages are the HTML fixtures, the synthetic pages of ``parsing_corpus`` and
malformed versions of both (no tables, a cell short, a cell extra). The
expected outputs were recorded before the module was split and simplified;
``python tests/parsing_corpus.py`` rewrites them, which is only right when a
behaviour change is intended.
"""

from __future__ import annotations

import json
import sys
import unittest

from lxml import etree

import parsing_corpus as corpus

# Whole-page outputs are stored as hashes, and lxml builds differ in details
# such as whitespace inside text nodes. The hashes only mean something on the
# build they were recorded with; elsewhere the per-parser tests still run.
RECORDED_WITH = {"python": (3, 14), "libxml": (2, 11, 9)}
SAME_BUILD = sys.version_info[:2] == RECORDED_WITH["python"] and etree.LIBXML_VERSION == RECORDED_WITH["libxml"]
BUILD_NOTE = "recorded with Python 3.14 and libxml2 2.11.9; this build parses a few pages differently"


class ParsingCharacterizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.golden = json.loads(corpus.GOLDEN_PATH.read_text(encoding="utf-8"))

    @unittest.skipUnless(SAME_BUILD, BUILD_NOTE)
    def test_every_parser_on_every_page(self) -> None:
        pages = self.golden["pages"]
        seen: set[tuple[str, str]] = set()
        for name, parser_name, value, is_empty in corpus.page_cases():
            expected = pages.get(name, {}).get(parser_name)
            with self.subTest(page=name, parser=parser_name):
                if expected is None:
                    self.assertTrue(is_empty, f"expected the empty-document output, got {corpus._dump(value)[:400]}")
                    continue
                seen.add((name, parser_name))
                recorded = corpus.record(value, page=name, parser=parser_name)
                self.assertEqual(corpus._dump(recorded), corpus._dump(expected))
        recorded_pairs = {(name, parser_name) for name, parsers in pages.items() for parser_name in parsers}
        self.assertEqual(recorded_pairs - seen, set())

    def test_empty_document(self) -> None:
        for parser_name, parser in corpus.PAGE_PARSERS.items():
            with self.subTest(parser=parser_name):
                recorded = corpus.record(corpus._call(parser, ""))
                self.assertEqual(corpus._dump(recorded), corpus._dump(self.golden["empty_document"][parser_name]))

    @unittest.skipUnless(SAME_BUILD, BUILD_NOTE)
    def test_helpers(self) -> None:
        expected = self.golden["helpers"]
        actual = json.loads(corpus._dump(corpus.helper_cases()))
        self.assertEqual(list(actual), list(expected))
        for key, value in actual.items():
            with self.subTest(case=key):
                self.assertEqual(corpus._dump(value), corpus._dump(expected[key]))


if __name__ == "__main__":
    unittest.main()
