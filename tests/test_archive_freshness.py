"""The quota note states the real age of the data, never a refresh cadence.

The archive pauses outside registration and add/drop weeks, so "refreshed once
a day" was false whenever the last snapshot was days old. Synthetic fixtures.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

from test_archive_datasets import BASE, FakeArchiveSession

from ninova_mcp.archive import snapshot_freshness
from ninova_mcp.archive_client import ItuArchiveClient
from ninova_mcp.server import NinovaMcpApp

LAST = "2026-10-05T09:43:32Z"
SCRAPED = "2026-10-05T09:05:14Z"


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


INDEX = {
    "currentTerm": "2026-2027 Güz Dönemi",
    "currentSlug": "2026-2027-guz",
    "scrapedAt": SCRAPED,
    "terms": [
        {"slug": "2026-2027-guz", "label": "2026-2027 Güz Dönemi", "source": "itu-archive"},
        {"slug": "2025-2026-bahar", "label": "2025-2026 Bahar Dönemi", "source": "itu-archive"},
    ],
}

DOCUMENTS: dict[str, Any] = {
    "/index.json": INDEX,
    "/quota/2026-2027-guz.json": {
        "slug": "2026-2027-guz", "snapshots": 63, "first": "2026-09-01T00:30:46Z", "last": LAST,
        "courses": [{"crn": "40001", "capacity": 50, "enrolled": 49}],
    },
    "/quota/2025-2026-bahar.json": {
        "slug": "2025-2026-bahar", "snapshots": 40, "first": "2026-02-01T00:00:00Z",
        "last": "2026-03-01T00:00:00Z",
        "courses": [{"crn": "40001", "capacity": 50, "enrolled": 50, "filledAt": "2026-02-03T00:00:00Z"}],
    },
}


def make_app() -> tuple[NinovaMcpApp, FakeArchiveSession]:
    session = FakeArchiveSession(DOCUMENTS)
    client = ItuArchiveClient(session=session, base_url=BASE, cache_ttl=60)  # type: ignore[arg-type]
    client._min_request_interval = 0
    app = NinovaMcpApp()
    app._archive = client
    return app, session


class FreshnessTests(unittest.TestCase):
    def test_six_day_old_snapshot_is_reported_as_such(self) -> None:
        result = snapshot_freshness(LAST, scraped_at=SCRAPED, now=at("2026-10-11T12:00:00"))
        self.assertEqual(result["last_snapshot_age_days"], 6.1)
        self.assertIs(result["scan_active"], False)
        self.assertIn("yaklaşık 6 gün önce alındı", result["note"])
        self.assertIn("etkin görünmüyor", result["note"])
        self.assertIn(LAST, result["note"])

    def test_recent_snapshot_means_scanning_is_active(self) -> None:
        result = snapshot_freshness(LAST, scraped_at=SCRAPED, now=at("2026-10-05T15:43:32"))
        self.assertEqual(result["last_snapshot_age_days"], 0.2)
        self.assertIs(result["scan_active"], True)
        self.assertIn("yaklaşık 6 saat önce alındı", result["note"])
        self.assertIn("tarama etkin görünüyor", result["note"])

    def test_a_newer_archive_scrape_keeps_scanning_active(self) -> None:
        result = snapshot_freshness(LAST, scraped_at="2026-10-11T06:00:00Z", now=at("2026-10-11T12:00:00"))
        self.assertIs(result["scan_active"], True)
        self.assertIn("yaklaşık 6 gün önce alındı", result["note"])

    def test_a_term_that_is_no_longer_scanned(self) -> None:
        result = snapshot_freshness(
            "2026-03-01T00:00:00Z", scraped_at=SCRAPED, is_current_term=False, now=at("2026-10-05T12:00:00"),
        )
        self.assertIs(result["scan_active"], False)
        self.assertIn("şu an taradığı dönem değil", result["note"])

    def test_unknown_times_claim_nothing(self) -> None:
        result = snapshot_freshness(None, now=at("2026-10-05T12:00:00"))
        self.assertIsNone(result["last_snapshot_age_days"])
        self.assertIsNone(result["scan_active"])
        self.assertIn("kayıtlı değil", result["note"])
        self.assertIn("bilinmiyor", result["note"])

    def test_no_note_promises_a_cadence(self) -> None:
        cases = [
            snapshot_freshness(LAST, scraped_at=SCRAPED, now=at("2026-10-11T12:00:00")),
            snapshot_freshness(LAST, scraped_at=SCRAPED, now=at("2026-10-05T15:43:32")),
            snapshot_freshness(None, now=at("2026-10-05T12:00:00")),
            snapshot_freshness(LAST, is_current_term=False, now=at("2026-12-05T12:00:00")),
        ]
        for case in cases:
            self.assertNotIn("günde bir", case["note"])
            self.assertIn("anlık değildir", case["note"])


class FillRateNoteTests(unittest.TestCase):
    def test_stale_series_is_not_called_daily(self) -> None:
        app, session = make_app()
        with patch("ninova_mcp.archive._utc_now", return_value=at("2026-10-11T12:00:00")):
            result = app.archive_fill_rate(crn="40001")
        self.assertNotIn("günde bir", result["note"])
        self.assertIn("yaklaşık 6 gün önce", result["note"])
        self.assertIs(result["scan_active"], False)
        self.assertEqual(result["last_snapshot_age_days"], 6.1)
        self.assertEqual(result["last_snapshot"], LAST)
        self.assertEqual(result["archive_scraped_at"], SCRAPED)
        # The index and the quota file: nothing else is fetched for the note.
        self.assertEqual(session.requested, ["/index.json", "/quota/2026-2027-guz.json"])

    def test_active_scanning_is_reported_while_it_lasts(self) -> None:
        app, _ = make_app()
        with patch("ninova_mcp.archive._utc_now", return_value=at("2026-10-05T12:00:00")):
            result = app.archive_fill_rate(crn=40001)
        self.assertIs(result["scan_active"], True)
        self.assertIn("tarama etkin görünüyor", result["note"])

    def test_past_term_series(self) -> None:
        app, _ = make_app()
        with patch("ninova_mcp.archive._utc_now", return_value=at("2026-10-05T12:00:00")):
            result = app.archive_fill_rate(crn="40001", term="2025-2026 Bahar")
        self.assertIs(result["scan_active"], False)
        self.assertIn("şu an taradığı dönem değil", result["note"])
        self.assertTrue(result["quota"]["is_full"])


if __name__ == "__main__":
    unittest.main()
