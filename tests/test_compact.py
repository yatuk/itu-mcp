from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ninova_mcp.compact import compact_value, maybe_compact


class CompactTests(unittest.TestCase):
    def test_truncates_long_strings_and_lists(self) -> None:
        payload = {
            "title": "x" * 2000,
            "items": [{"n": i, "summary": "y" * 1000} for i in range(50)],
            "links": [{"url": f"u{i}", "text": "t"} for i in range(100)],
        }
        result = compact_value(payload, list_limit=5, text_chars=20, link_limit=3)
        self.assertEqual(len(result["title"]), 20)
        self.assertEqual(len(result["items"]), 6)  # 5 + omitted marker
        self.assertEqual(result["items"][-1]["_omitted"], 45)
        self.assertLessEqual(len(result["links"]), 3)
        self.assertEqual(result["links_omitted"], 97)

    def test_maybe_compact_respects_flag(self) -> None:
        payload = {"description": "a" * 5000, "count": 1}
        full = maybe_compact(payload, compact=False)
        self.assertEqual(len(full["description"]), 5000)
        self.assertNotIn("compact", full)

        small = maybe_compact(payload, compact=True, text_chars=50)
        self.assertTrue(small["compact"])
        self.assertLessEqual(len(small["description"]), 50)

    def test_env_default(self) -> None:
        payload = {"summary": "z" * 2000}
        with patch.dict(os.environ, {"NINOVA_COMPACT_DEFAULT": "1"}):
            result = maybe_compact(payload, compact=None, text_chars=30)
        self.assertTrue(result["compact"])
        self.assertLessEqual(len(result["summary"]), 30)


if __name__ == "__main__":
    unittest.main()
