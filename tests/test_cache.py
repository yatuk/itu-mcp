from __future__ import annotations

import time
import unittest

from ninova_mcp.cache import DEFAULT_MAX_ENTRIES, TtlCache, parse_ttl_seconds


class TtlCacheTests(unittest.TestCase):
    def test_set_get_and_expire(self) -> None:
        cache: TtlCache[str] = TtlCache(default_ttl_seconds=0.05)
        cache.set("k", "v")
        self.assertEqual(cache.get("k"), "v")
        time.sleep(0.07)
        self.assertIsNone(cache.get("k"))

    def test_invalidate_and_clear(self) -> None:
        cache: TtlCache[int] = TtlCache(default_ttl_seconds=60)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.invalidate("a")
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 2)
        cache.clear()
        self.assertIsNone(cache.get("b"))

    def test_oldest_entry_is_evicted_at_the_size_limit(self) -> None:
        cache: TtlCache[int] = TtlCache(default_ttl_seconds=60, max_entries=3)
        for index in range(5):
            cache.set(f"k{index}", index)
        self.assertEqual(len(cache), 3)
        self.assertIsNone(cache.get("k0"))
        self.assertIsNone(cache.get("k1"))
        self.assertEqual([cache.get(f"k{i}") for i in (2, 3, 4)], [2, 3, 4])

    def test_rewriting_a_key_does_not_evict_and_makes_it_newest(self) -> None:
        cache: TtlCache[int] = TtlCache(default_ttl_seconds=60, max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("a", 10)
        self.assertEqual(len(cache), 2)
        cache.set("c", 3)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.get("a"), 10)
        self.assertEqual(cache.get("c"), 3)

    def test_expired_entries_are_dropped_before_live_ones(self) -> None:
        cache: TtlCache[int] = TtlCache(default_ttl_seconds=60, max_entries=2)
        cache.set("live", 1)
        cache.set("short", 2, ttl_seconds=0.02)
        time.sleep(0.04)
        cache.set("new", 3)
        self.assertEqual(cache.get("live"), 1)
        self.assertEqual(cache.get("new"), 3)

    def test_default_limit_is_bounded_and_generous(self) -> None:
        cache: TtlCache[int] = TtlCache(default_ttl_seconds=60)
        self.assertEqual(cache.max_entries, DEFAULT_MAX_ENTRIES)
        self.assertGreaterEqual(DEFAULT_MAX_ENTRIES, 1024)
        for index in range(DEFAULT_MAX_ENTRIES + 50):
            cache.set(str(index), index)
        self.assertEqual(len(cache), DEFAULT_MAX_ENTRIES)

    def test_zero_ttl_disables_cache(self) -> None:
        cache: TtlCache[str] = TtlCache(default_ttl_seconds=0)
        cache.set("k", "v")
        self.assertIsNone(cache.get("k"))

    def test_parse_ttl_seconds(self) -> None:
        self.assertEqual(parse_ttl_seconds(None, 60), 60)
        self.assertEqual(parse_ttl_seconds("30", 60), 30.0)
        self.assertEqual(parse_ttl_seconds("bad", 60), 60)
        self.assertEqual(parse_ttl_seconds("-5", 60), 0.0)


if __name__ == "__main__":
    unittest.main()
