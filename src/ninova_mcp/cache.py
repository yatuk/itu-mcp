"""Simple in-process TTL cache with a size limit, for hot reads."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

# Far above normal use: the largest consumer, the public OBS client, holds a
# few hundred branch pages. The limit only matters for a server that runs for
# a long time against ever-changing keys.
DEFAULT_MAX_ENTRIES = 2048


@dataclass(slots=True)
class CacheEntry(Generic[T]):
    value: T
    expires_at: float


class TtlCache(Generic[T]):
    """Single-process cache with a shared TTL per key and a size limit.

    At most ``max_entries`` values are kept. When a new key would exceed the
    limit, expired entries are dropped first and then the oldest written
    ones. Keys include caller-supplied text (course codes, search terms), so
    without a limit a long-running server could be made to hold an unbounded
    number of pages.
    """

    def __init__(
        self,
        default_ttl_seconds: float,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.default_ttl_seconds = max(0.0, float(default_ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        # Insertion order is write order: a rewritten key moves to the end.
        self._entries: dict[str, CacheEntry[T]] = {}
        # The remote transport runs tool calls on a thread pool.
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> T | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= time.monotonic():
                self._entries.pop(key, None)
                return None
            return entry.value

    def set(self, key: str, value: T, *, ttl_seconds: float | None = None) -> T:
        ttl = self.default_ttl_seconds if ttl_seconds is None else max(0.0, float(ttl_seconds))
        if ttl <= 0:
            return value
        now = time.monotonic()
        with self._lock:
            self._entries.pop(key, None)
            if len(self._entries) >= self.max_entries:
                self._evict(now)
            self._entries[key] = CacheEntry(value=value, expires_at=now + ttl)
        return value

    def _evict(self, now: float) -> None:
        for stale in [k for k, entry in self._entries.items() if entry.expires_at <= now]:
            del self._entries[stale]
        while len(self._entries) >= self.max_entries:
            del self._entries[next(iter(self._entries))]

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._entries.clear()
                return
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def parse_ttl_seconds(env_value: str | None, default: float) -> float:
    if env_value is None or not str(env_value).strip():
        return default
    try:
        return max(0.0, float(env_value))
    except ValueError:
        return default
