"""Seat watch tools against a faked schedule source and a temporary state dir."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

from ninova_mcp.archive_client import ItuArchiveError
from ninova_mcp.obs_client import ObsError, ObsPublicClient
from ninova_mcp.seat_watch import (
    MAX_WATCHED_SECTIONS,
    SEAT_WATCH_STATE_VERSION,
    SEAT_WATCH_STATEFUL_TOOL_NAMES,
    SEAT_WATCH_TOOLS,
    diff_observation,
    load_seat_watch_state,
    observe_section,
)
from ninova_mcp.server import (
    DESTRUCTIVE_TOOL_NAMES,
    LOCAL_TOOL_NAMES,
    REMOTE_TOOL_NAMES,
    STATEFUL_TOOL_NAMES,
    NinovaMcpApp,
    register_tools,
)

TERM = "2026-2027 Güz Dönemi"
NEXT_TERM = "2026-2027 Bahar Dönemi"


def section(crn: str, code: str, capacity: Any, enrolled: Any, **extra: Any) -> dict[str, Any]:
    return {
        "crn": crn, "code": code, "name": f"{code} dersi", "instructor": "Örnek Hoca",
        "capacity": capacity, "enrolled": enrolled, "reservation": "-", **extra,
    }


class FakeSchedules:
    """Stands in for ObsPublicClient: serves whatever the test last put in ``branches``."""

    def __init__(self) -> None:
        self.term: str | None = TERM
        self.branches: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.fetched_at: dict[tuple[str, str], str] = {}
        self.extra: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail: set[tuple[str, str]] = set()
        self._schedule_cache_ttl = 60.0

    def set(self, branch: str, rows: list[dict[str, Any]], level: str = "LS") -> None:
        self.branches[(level, branch)] = rows
        self.fetched_at.pop((level, branch), None)

    def get_active_semester(self, program_type: str) -> str:
        if self.term is None:
            raise ObsError("dönem okunamadı")
        return self.term

    def get_course_schedule(self, program_type: str, department: str) -> dict[str, Any]:
        key = (program_type, department)
        self.calls.append(key)
        if key in self.fail:
            raise ObsError("OBS public endpoint returned HTTP 500")
        if key not in self.branches:
            raise ObsError(f"Bölüm kodu bulunamadı: {department!r}")
        rows = self.branches[key]
        # A real fetch is stamped "now"; a test can pin an older stamp to model a cache hit.
        stamp = self.fetched_at.get(key) or datetime.now(tz=UTC).isoformat()
        return {
            "count": len(rows), "courses": rows, "program_type": program_type,
            "department_code": department, "semester": self.term, "fetched_at": stamp,
            **self.extra.get(key, {}),
        }


class FakeArchive:
    def __init__(self, rows: list[list[Any]] | None, term: str = TERM) -> None:
        self.rows = rows
        self.term = term
        self.fail = False

    def get_index(self) -> dict[str, Any]:
        if self.fail:
            raise ItuArchiveError("arşiv kapalı")
        return {"currentSlug": "2026-2027-guz", "currentTerm": self.term}

    def get_term_search(self, slug: str) -> list[list[Any]] | None:
        return self.rows


ARCHIVE_ROWS = [
    ["12469", "BLG 223E", "Data Structures", "BLG", "A", "", 80, 80, "LS", "", []],
    ["20001", "MAT 103", "Matematik I", "MAT", "B", "", 60, 10, "LS", "", []],
    ["30001", "BLG 501", "Lisansüstü", "BLG", "C", "", 20, 5, "LU", "", []],
    ["short"],
]


class SeatWatchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ, {"NINOVA_STATE_DIR": self._tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.app = NinovaMcpApp()
        self.schedules = FakeSchedules()
        self.app._obs_public = self.schedules  # type: ignore[assignment]
        self.app._archive = FakeArchive(ARCHIVE_ROWS)  # type: ignore[assignment]
        self.schedules.set("BLG", [
            section("12469", "BLG 223E", 80, 80),
            section("12472", "BLG 223E", 80, 43),
            section("12478", "BLG 231E", 0, 0),
        ])
        self.schedules.set("MAT", [section("20001", "MAT 103", 60, 10)])

    def check(self) -> dict[str, Any]:
        return self.app.check_watched_course_sections()

    def by_crn(self, result: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {item["crn"]: item for item in result["sections"]}


class WatchListTests(SeatWatchTestCase):
    def test_add_list_remove(self) -> None:
        added = self.app.watch_course_sections(["12469", "12472"], department_code="blg")
        self.assertEqual([item["crn"] for item in added["added"]], ["12469", "12472"])
        self.assertEqual(added["added"][0]["term"], TERM)
        self.assertEqual(added["added"][0]["department_code"], "BLG")
        self.assertEqual(added["added"][0]["current"]["status"], "full")
        self.assertEqual(added["watch_count"], 2)
        self.assertIn("arka planda çalışmaz", added["background_note"])

        calls_before = list(self.schedules.calls)
        listed = self.app.list_watched_course_sections()
        self.assertEqual(self.schedules.calls, calls_before, "listing must not read OBS")
        self.assertEqual([item["crn"] for item in listed["sections"]], ["12469", "12472"])
        self.assertIsNone(listed["sections"][0]["last_observation"])
        self.assertEqual(listed["terms"], [TERM])

        removed = self.app.unwatch_course_sections(crns=["12469", "99999"])
        self.assertEqual([item["crn"] for item in removed["removed"]], ["12469"])
        self.assertEqual(removed["not_watched"], ["99999"])
        self.assertEqual(self.app.list_watched_course_sections()["watch_count"], 1)

        self.assertEqual(self.app.unwatch_course_sections(clear_all=True)["watch_count"], 0)

    def test_adding_twice_keeps_the_stored_observation(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.check()
        again = self.app.watch_course_sections(["12469"], department_code="BLG")
        self.assertEqual(again["added"], [])
        self.assertEqual([item["crn"] for item in again["already_watched"]], ["12469"])
        stored = self.app.list_watched_course_sections()["sections"][0]
        self.assertEqual(stored["last_observation"]["status"], "full")

    def test_crn_missing_from_the_department_is_not_added(self) -> None:
        result = self.app.watch_course_sections(["12469", "55555"], department_code="BLG")
        self.assertEqual([item["crn"] for item in result["added"]], ["12469"])
        self.assertEqual(result["not_found"][0]["crn"], "55555")
        self.assertEqual(result["not_found"][0]["reason"], "not_in_schedule")

    def test_input_is_validated_before_any_request(self) -> None:
        for bad in (["abc"], ["123"], ["12469", "12469"], [], [True], [12469, 12469]):
            with self.subTest(crns=bad), self.assertRaises(ObsError):
                self.app.watch_course_sections(bad, department_code="BLG")  # type: ignore[arg-type]
        with self.assertRaises(ObsError):
            self.app.watch_course_sections(["12469"], department_code="BLG", program_type="doktora")
        with self.assertRaises(ObsError):
            self.app.watch_course_sections(["12469"], department_code="  ")
        with self.assertRaises(ObsError):
            self.app.unwatch_course_sections()
        with self.assertRaises(ObsError):
            self.app.unwatch_course_sections(crns=["12469"], clear_all=True)
        self.assertEqual(self.schedules.calls, [])

    def test_fuzzy_department_resolution_is_refused(self) -> None:
        self.schedules.extra[("LS", "BL")] = {"department_code": "BLG"}
        self.schedules.branches[("LS", "BL")] = self.schedules.branches[("LS", "BLG")]
        with self.assertRaisesRegex(ObsError, "BLG"):
            self.app.watch_course_sections(["12469"], department_code="BL")

    def test_watch_limit_is_enforced_before_any_request(self) -> None:
        rows = [section(str(40000 + i), "BLG 100", 10, 5) for i in range(MAX_WATCHED_SECTIONS + 1)]
        self.schedules.set("BLG", rows)
        crns = [row["crn"] for row in rows]
        self.app.watch_course_sections(crns[:12], department_code="BLG")
        self.app.watch_course_sections(crns[12:MAX_WATCHED_SECTIONS], department_code="BLG")
        self.assertEqual(self.app.list_watched_course_sections()["watch_count"], MAX_WATCHED_SECTIONS)
        calls = len(self.schedules.calls)
        with self.assertRaisesRegex(ObsError, str(MAX_WATCHED_SECTIONS)):
            self.app.watch_course_sections([crns[-1]], department_code="BLG")
        self.assertEqual(len(self.schedules.calls), calls)
        # Re-adding a watched CRN does not count against the limit.
        self.app.watch_course_sections([crns[0]], department_code="BLG")


class DiscoveryTests(SeatWatchTestCase):
    def test_branch_and_level_come_from_the_archive_index(self) -> None:
        self.schedules.set("BLG", [section("30001", "BLG 501", 20, 5)], level="LU")
        result = self.app.watch_course_sections(["12469", "20001", "30001"])
        self.assertEqual(result["resolved_by"], "archive_search_index")
        found = {item["crn"]: (item["program_type"], item["department_code"]) for item in result["added"]}
        self.assertEqual(found, {
            "12469": ("LS", "BLG"), "20001": ("LS", "MAT"), "30001": ("LU", "BLG"),
        })

    def test_crn_unknown_to_the_archive_is_reported(self) -> None:
        result = self.app.watch_course_sections(["12469", "77777"])
        self.assertEqual([item["crn"] for item in result["added"]], ["12469"])
        self.assertEqual(result["not_found"][0]["reason"], "not_in_archive_index")

    def test_archive_hit_is_confirmed_against_the_live_schedule(self) -> None:
        self.schedules.set("MAT", [section("20002", "MAT 104", 60, 10)])
        result = self.app.watch_course_sections(["20001"])
        self.assertEqual(result["added"], [])
        self.assertEqual(result["not_found"][0]["reason"], "not_in_schedule")

    def test_archive_on_another_term_is_not_trusted(self) -> None:
        self.app._archive = FakeArchive(ARCHIVE_ROWS, term="2025-2026 Yaz Dönemi")  # type: ignore[assignment]
        with self.assertRaisesRegex(ObsError, "department_code"):
            self.app.watch_course_sections(["12469"])
        self.assertEqual(self.schedules.calls, [])

    def test_missing_index_or_archive_failure_asks_for_the_department(self) -> None:
        self.app._archive = FakeArchive(None)  # type: ignore[assignment]
        with self.assertRaisesRegex(ObsError, "department_code"):
            self.app.watch_course_sections(["12469"])
        self.app._archive.fail = True  # type: ignore[attr-defined]
        with self.assertRaisesRegex(ObsError, "department_code"):
            self.app.watch_course_sections(["12469"])


class CheckTests(SeatWatchTestCase):
    def test_first_check_is_a_baseline_and_claims_no_change(self) -> None:
        self.app.watch_course_sections(["12469", "12472"], department_code="BLG")
        result = self.check()
        items = self.by_crn(result)
        self.assertEqual(items["12469"]["status"], "full")
        self.assertEqual(items["12469"]["seats_free"], 0)
        self.assertEqual(items["12472"]["status"], "open")
        self.assertEqual(
            (items["12472"]["capacity"], items["12472"]["enrolled"], items["12472"]["seats_free"]),
            (80, 43, 37),
        )
        for item in items.values():
            self.assertEqual(item["changes"], ["baseline"])
            self.assertIsNone(item["compared_with"])
            self.assertIsNone(item["last_change"])
        self.assertEqual(result["summary"]["opened"], [])
        self.assertEqual(result["summary"]["baseline_established"], ["12469", "12472"])
        self.assertEqual(result["summary"]["open_now"], ["12472"])
        self.assertIn("arka planda çalışmaz", result["background_note"])

    def test_full_to_open_and_open_to_full(self) -> None:
        self.app.watch_course_sections(["12469", "12472"], department_code="BLG")
        self.check()
        self.schedules.set("BLG", [
            section("12469", "BLG 223E", 80, 78),
            section("12472", "BLG 223E", 80, 80),
        ])
        result = self.check()
        items = self.by_crn(result)
        self.assertEqual(items["12469"]["changes"], ["opened"])
        self.assertEqual(items["12469"]["seats_free"], 2)
        self.assertEqual(items["12469"]["compared_with"]["status"], "full")
        self.assertEqual(items["12472"]["changes"], ["filled"])
        self.assertEqual(result["summary"]["opened"], ["12469"])
        self.assertEqual(result["summary"]["filled"], ["12472"])
        self.assertEqual(items["12469"]["last_change"]["changes"], ["opened"])

        # Nothing moved: the next check reports no change but keeps the last one visible.
        self.schedules.set("BLG", self.schedules.branches[("LS", "BLG")])
        quiet = self.by_crn(self.check())
        self.assertEqual(quiet["12469"]["changes"], [])
        self.assertEqual(quiet["12469"]["last_change"]["changes"], ["opened"])

    def test_capacity_raised_opens_a_full_section(self) -> None:
        self.app.watch_course_sections(["12469", "12478"], department_code="BLG")
        self.check()
        self.schedules.set("BLG", [
            section("12469", "BLG 223E", 90, 80),
            section("12478", "BLG 231E", 0, 0),
            section("12472", "BLG 223E", 80, 43),
        ])
        result = self.check()
        items = self.by_crn(result)
        self.assertEqual(items["12469"]["changes"], ["opened", "capacity_raised"])
        self.assertEqual(result["summary"]["capacity_raised"], ["12469"])
        # Capacity 0 is a declared quota of zero: no seat, and no change claimed.
        self.assertEqual(items["12478"]["status"], "no_capacity")
        self.assertEqual(items["12478"]["changes"], [])

    def test_seat_count_change_without_a_transition(self) -> None:
        self.app.watch_course_sections(["12472"], department_code="BLG")
        self.check()
        self.schedules.set("BLG", [section("12472", "BLG 223E", 80, 50)])
        item = self.by_crn(self.check())["12472"]
        self.assertEqual(item["changes"], ["seats_changed"])
        self.assertEqual(item["compared_with"]["seats_free"], 37)
        self.assertEqual(item["seats_free"], 30)

    def test_missing_enrolled_is_unknown_not_a_seat(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.check()  # baseline: full
        self.schedules.set("BLG", [
            section("12469", "BLG 223E", 80, None),
            section("12472", "BLG 223E", 80, 43),
        ])
        result = self.check()
        item = self.by_crn(result)["12469"]
        self.assertEqual(item["status"], "unknown")
        self.assertEqual(item["unknown_reason"], "enrolled_missing")
        self.assertIsNone(item["seats_free"])
        self.assertEqual(item["changes"], ["data_unavailable"])
        self.assertEqual(result["summary"]["opened"], [])
        self.assertEqual(result["summary"]["open_now"], [])
        self.assertEqual(result["summary"]["unknown"], ["12469"])

        # Still full once the numbers return: no opening is invented.
        self.schedules.set("BLG", [section("12469", "BLG 223E", 80, 80)])
        back = self.by_crn(self.check())["12469"]
        self.assertEqual(back["changes"], ["data_available"])

    def test_unpopulated_enrolled_column_is_unknown(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.check()  # baseline: full
        # OBS reset the column: every section shows 0 enrolled.
        self.schedules.set("BLG", [
            section("12469", "BLG 223E", 80, 0),
            section("12472", "BLG 223E", 80, 0),
        ])
        result = self.check()
        item = self.by_crn(result)["12469"]
        self.assertEqual(item["status"], "unknown")
        self.assertEqual(item["unknown_reason"], "enrollment_not_populated")
        self.assertIsNone(item["enrolled"])
        self.assertEqual(result["summary"]["opened"], [])
        self.assertFalse(result["sources"][0]["enrollment_populated"])

    def test_first_real_numbers_after_unknown_are_a_baseline(self) -> None:
        self.schedules.set("BLG", [section("12469", "BLG 223E", 80, 0)])
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.assertEqual(self.by_crn(self.check())["12469"]["status"], "unknown")
        self.schedules.set("BLG", [
            section("12469", "BLG 223E", 80, 40),
            section("12472", "BLG 223E", 80, 43),
        ])
        result = self.check()
        self.assertEqual(self.by_crn(result)["12469"]["changes"], ["baseline"])
        self.assertEqual(result["summary"]["opened"], [])

    def test_placeholder_capacity_is_unknown(self) -> None:
        for capacity in (None, -1, "80"):
            with self.subTest(capacity=capacity):
                observation = observe_section(section("1", "X", capacity, 5), populated=True)
                self.assertEqual(observation["status"], "unknown")
                self.assertEqual(observation["unknown_reason"], "capacity_missing")
        over = observe_section(section("1", "X", 30, 35), populated=True)
        self.assertEqual((over["status"], over["seats_free"], over["over_capacity"]), ("full", 0, 5))

    def test_crn_not_listed_is_not_full(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.check()
        self.schedules.set("BLG", [section("12472", "BLG 223E", 80, 43)])
        result = self.check()
        item = self.by_crn(result)["12469"]
        self.assertEqual(item["status"], "not_listed")
        self.assertEqual(item["changes"], ["delisted"])
        self.assertIsNone(item["seats_free"])
        self.assertIn("'dolu' demek değildir", item["note"])
        self.assertEqual(result["summary"]["not_listed"], ["12469"])
        self.assertEqual(result["summary"]["delisted"], ["12469"])

        # It comes back open: compared with the last real numbers (full).
        self.schedules.set("BLG", [section("12469", "BLG 223E", 80, 70)])
        back = self.by_crn(self.check())["12469"]
        self.assertEqual(back["changes"], ["relisted", "opened"])

    def test_empty_or_unparsed_schedule_does_not_delist(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.check()
        for extra in ({"parse_warning": "tablo yok"}, {}):
            with self.subTest(extra=extra):
                self.schedules.set("BLG", [] if not extra else [section("1", "X", 1, 1)])
                self.schedules.extra[("LS", "BLG")] = extra
                result = self.check()
                item = self.by_crn(result)["12469"]
                self.assertEqual(item["status"], "schedule_unavailable")
                self.assertEqual(item["last_observation"]["status"], "full")
                self.assertEqual(result["summary"]["not_listed"], [])
                self.assertEqual(result["summary"]["not_checked"], ["12469"])

    def test_one_fetch_per_branch(self) -> None:
        self.app.watch_course_sections(["12469", "12472", "12478"], department_code="BLG")
        self.app.watch_course_sections(["20001"], department_code="MAT")
        self.schedules.calls.clear()
        result = self.check()
        self.assertEqual(sorted(self.schedules.calls), [("LS", "BLG"), ("LS", "MAT")])
        self.assertEqual(result["branches_read"], 2)
        self.assertEqual(len(result["sections"]), 4)

    def test_failed_branch_keeps_its_observation_and_others_still_check(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.app.watch_course_sections(["20001"], department_code="MAT")
        self.check()
        self.schedules.fail.add(("LS", "BLG"))
        self.schedules.set("MAT", [section("20001", "MAT 103", 60, 60)])
        result = self.check()
        items = self.by_crn(result)
        self.assertEqual(items["12469"]["status"], "fetch_failed")
        self.assertEqual(items["12469"]["last_observation"]["status"], "full")
        self.assertEqual(items["20001"]["changes"], ["filled"])

    def test_empty_watch_list(self) -> None:
        result = self.check()
        self.assertEqual(result["sections"], [])
        self.assertEqual(self.schedules.calls, [])
        self.assertIn("watch_course_sections", result["note"])

    def test_reserved_seats_are_flagged(self) -> None:
        self.schedules.set("BLG", [section("12472", "BLG 223E", 80, 43, reservation="BLG/40/40")])
        self.app.watch_course_sections(["12472"], department_code="BLG")
        item = self.by_crn(self.check())["12472"]
        self.assertEqual(item["reservation"], "BLG/40/40")
        self.assertIn("reservation_note", item)


class FreshnessTests(SeatWatchTestCase):
    def test_fresh_read_is_not_marked_cached(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        result = self.check()
        self.assertFalse(result["freshness"]["all_from_cache"])
        self.assertEqual(result["freshness"]["cache_ttl_seconds"], 60.0)
        self.assertNotIn("retry_after_seconds", result["freshness"])
        self.assertFalse(result["sources"][0]["from_cache"])

    def test_repeat_within_ttl_reports_cache_age_and_does_not_recompare(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        stamp = (datetime.now(tz=UTC) - timedelta(seconds=20)).isoformat()
        self.schedules.fetched_at[("LS", "BLG")] = stamp
        first = self.check()
        self.assertTrue(first["freshness"]["all_from_cache"])
        self.assertEqual(self.by_crn(first)["12469"]["changes"], ["baseline"])
        self.assertFalse(self.by_crn(first)["12469"]["repeat_of_last_check"])

        second = self.check()
        freshness = second["freshness"]
        self.assertTrue(freshness["all_from_cache"])
        self.assertTrue(20 <= freshness["oldest_data_age_seconds"] <= 25)
        self.assertTrue(35 <= freshness["retry_after_seconds"] <= 40)
        self.assertIn("yeniden okunmadı", freshness["note"])
        source = second["sources"][0]
        self.assertTrue(source["from_cache"])
        self.assertTrue(20 <= source["age_seconds"] <= 25)
        item = self.by_crn(second)["12469"]
        self.assertTrue(item["repeat_of_last_check"])
        self.assertEqual(item["changes"], [])
        self.assertEqual(item["status"], "full")

    def test_repeat_does_not_rewrite_state(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.schedules.fetched_at[("LS", "BLG")] = (
            datetime.now(tz=UTC) - timedelta(seconds=5)
        ).isoformat()
        self.check()
        path = self.app.seat_watch_state_path
        before = path.read_text(encoding="utf-8")
        self.check()
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_schedule_without_a_stamp_is_checked_normally(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        real = self.schedules.get_course_schedule

        def unstamped(level: str, branch: str) -> dict[str, Any]:
            schedule = real(level, branch)
            schedule.pop("fetched_at")
            return schedule

        self.schedules.get_course_schedule = unstamped  # type: ignore[method-assign]
        self.check()
        self.schedules.branches[("LS", "BLG")] = [section("12469", "BLG 223E", 80, 70)]
        result = self.check()
        self.assertEqual(self.by_crn(result)["12469"]["changes"], ["opened"])
        self.assertIsNone(result["sources"][0]["from_cache"])
        self.assertFalse(result["freshness"]["all_from_cache"])


class TermTests(SeatWatchTestCase):
    def test_entry_from_a_past_term_is_stale_and_not_compared(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.check()  # full in the old term
        self.schedules.term = NEXT_TERM
        # The same CRN now belongs to an open section of another course.
        self.schedules.set("BLG", [section("12469", "BLG 999", 50, 1)])
        self.schedules.calls.clear()
        result = self.check()
        item = self.by_crn(result)["12469"]
        self.assertEqual(item["status"], "stale_term")
        self.assertEqual(item["term"], TERM)
        self.assertEqual(item["active_term"], NEXT_TERM)
        self.assertEqual(item["changes"], [])
        self.assertEqual(result["summary"]["opened"], [])
        self.assertEqual(result["summary"]["stale_term"], ["12469"])
        self.assertIn("stale_only", result["stale_note"])
        self.assertEqual(self.schedules.calls, [], "a stale entry costs no schedule request")
        stored = self.app.list_watched_course_sections()["sections"][0]
        self.assertEqual(stored["last_observation"]["status"], "full")
        self.assertEqual(stored["course_code"], "BLG 223E")

    def test_stale_entries_can_be_cleared(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.schedules.term = NEXT_TERM
        self.app.watch_course_sections(["20001"], department_code="MAT")
        removed = self.app.unwatch_course_sections(stale_only=True)
        self.assertEqual([item["crn"] for item in removed["removed"]], ["12469"])
        self.assertEqual(
            [item["crn"] for item in self.app.list_watched_course_sections()["sections"]],
            ["20001"],
        )

    def test_stale_clear_removes_nothing_when_the_term_is_unreadable(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.schedules.term = None
        with self.assertRaises(ObsError):
            self.app.unwatch_course_sections(stale_only=True)
        self.assertEqual(self.app.list_watched_course_sections()["watch_count"], 1)

    def test_re_adding_in_the_new_term_starts_a_fresh_baseline(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.check()
        self.schedules.term = NEXT_TERM
        self.schedules.set("BLG", [section("12469", "BLG 999", 50, 1)])
        result = self.app.watch_course_sections(["12469"], department_code="BLG")
        self.assertEqual(result["replaced_stale"], ["12469"])
        item = self.by_crn(self.check())["12469"]
        self.assertEqual(item["term"], NEXT_TERM)
        self.assertEqual(item["course_code"], "BLG 999")
        self.assertEqual(item["changes"], ["baseline"])

    def test_unreadable_term_is_not_compared(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.check()
        self.schedules.term = None
        self.schedules.set("BLG", [section("12469", "BLG 223E", 80, 10)])
        result = self.check()
        self.assertEqual(self.by_crn(result)["12469"]["status"], "term_unverified")
        self.assertEqual(result["summary"]["opened"], [])
        with self.assertRaises(ObsError):
            self.app.watch_course_sections(["12472"], department_code="BLG")


class StateFileTests(SeatWatchTestCase):
    def test_state_file_has_a_version_and_leaves_no_temp_file(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        self.check()
        path = self.app.seat_watch_state_path
        self.assertEqual(path.parent, self.app.state_dir)
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["version"], SEAT_WATCH_STATE_VERSION)
        self.assertEqual(document["watches"]["12469"]["term"], TERM)
        self.assertEqual(document["watches"]["12469"]["last_observation"]["status"], "full")
        self.assertEqual([p.name for p in path.parent.glob("*.tmp")], [])
        self.assertFalse(self.app.tracking_state_path.exists(), "Ninova tracking state is untouched")

    def test_missing_file_is_an_empty_list(self) -> None:
        state, warning = load_seat_watch_state(self.app.seat_watch_state_path)
        self.assertEqual(state["watches"], {})
        self.assertIsNone(warning)

    def test_corrupt_file_is_tolerated_by_every_tool(self) -> None:
        path = self.app.seat_watch_state_path
        for body in ('{"version": 1, "watches": {"12469": {"crn"', "[]", '{"watches": []}', ""):
            with self.subTest(body=body):
                path.write_text(body, encoding="utf-8")
                listed = self.app.list_watched_course_sections()
                self.assertEqual(listed["watch_count"], 0)
                self.assertIn("state_warning", listed)
                self.assertIn("state_warning", self.check())
                self.assertIn("state_warning", self.app.unwatch_course_sections(clear_all=True))
                added = self.app.watch_course_sections(["12469"], department_code="BLG")
                self.assertEqual(added["watch_count"], 1)
                self.assertNotIn("state_warning", self.app.list_watched_course_sections())

    def test_malformed_entries_are_dropped_and_the_rest_kept(self) -> None:
        self.app.watch_course_sections(["12469"], department_code="BLG")
        path = self.app.seat_watch_state_path
        document = json.loads(path.read_text(encoding="utf-8"))
        document["watches"]["bad"] = "not an entry"
        document["watches"]["12472"] = {"crn": "99999"}
        document["watches"]["12469"]["last_observation"] = "oops"
        path.write_text(json.dumps(document), encoding="utf-8")
        listed = self.app.list_watched_course_sections()
        self.assertEqual([item["crn"] for item in listed["sections"]], ["12469"])
        self.assertIn("2 geçersiz", listed["state_warning"])
        self.assertEqual(self.by_crn(self.check())["12469"]["changes"], ["baseline"])


class DiffTests(unittest.TestCase):
    def obs(self, status: str, capacity: int | None = None, enrolled: int | None = None) -> dict[str, Any]:
        free = None if capacity is None or enrolled is None else max(capacity - enrolled, 0)
        return {"status": status, "capacity": capacity, "enrolled": enrolled, "seats_free": free}

    def test_transitions(self) -> None:
        full, open_ = self.obs("full", 80, 80), self.obs("open", 80, 70)
        unknown, gone = self.obs("unknown"), self.obs("not_listed")
        cases = [
            (None, None, full, ["baseline"]),
            (full, full, open_, ["opened"]),
            (open_, open_, full, ["filled"]),
            (full, full, full, []),
            (full, full, self.obs("full", 80, 82), ["enrollment_changed"]),
            (open_, open_, self.obs("open", 70, 60), ["capacity_lowered"]),
            (open_, open_, self.obs("open", 70, 65), ["capacity_lowered", "seats_changed"]),
            (self.obs("no_capacity", 0, 0), self.obs("no_capacity", 0, 0), self.obs("open", 30, 0),
             ["opened", "capacity_raised"]),
            (full, full, unknown, ["data_unavailable"]),
            (unknown, None, unknown, []),
            (unknown, None, open_, ["baseline"]),
            (unknown, full, open_, ["data_available", "opened"]),
            (full, full, gone, ["delisted"]),
            (gone, full, gone, []),
            (gone, None, open_, ["relisted", "baseline"]),
            (gone, None, unknown, ["relisted"]),
        ]
        for previous, last_known, current, expected in cases:
            with self.subTest(previous=previous, current=current):
                self.assertEqual(diff_observation(previous, last_known, current), expected)


class RegistrationTests(unittest.TestCase):
    NAMES = [tool["name"] for tool in SEAT_WATCH_TOOLS]

    def test_tools_are_registered_locally_and_remotely(self) -> None:
        self.assertEqual(self.NAMES, [
            "watch_course_sections", "unwatch_course_sections",
            "list_watched_course_sections", "check_watched_course_sections",
        ])
        for name in self.NAMES:
            with self.subTest(tool=name):
                self.assertIn(name, LOCAL_TOOL_NAMES)
                # Same as sync_all_courses: local state on the server, served on both transports.
                self.assertIn(name, REMOTE_TOOL_NAMES)
                self.assertNotIn(name, DESTRUCTIVE_TOOL_NAMES)
        self.assertIn("sync_all_courses", REMOTE_TOOL_NAMES)

    def test_writers_are_stateful_and_listing_is_read_only(self) -> None:
        self.assertEqual(
            SEAT_WATCH_STATEFUL_TOOL_NAMES,
            {"watch_course_sections", "unwatch_course_sections", "check_watched_course_sections"},
        )
        self.assertTrue(SEAT_WATCH_STATEFUL_TOOL_NAMES <= STATEFUL_TOOL_NAMES)

        class FakeMcp:
            def __init__(self) -> None:
                self.registered: dict[str, Any] = {}

            def add_tool(self, fn: object, **kwargs: Any) -> None:
                del fn
                self.registered[str(kwargs["name"])] = kwargs["annotations"]

        fake = FakeMcp()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"NINOVA_STATE_DIR": tmp}):
            register_tools(fake, NinovaMcpApp(), self.NAMES)
        for name in SEAT_WATCH_STATEFUL_TOOL_NAMES:
            self.assertFalse(fake.registered[name].readOnlyHint)
            self.assertFalse(fake.registered[name].idempotentHint)
            self.assertFalse(fake.registered[name].destructiveHint)
        self.assertTrue(fake.registered["list_watched_course_sections"].readOnlyHint)

    def test_descriptions_state_the_background_limit(self) -> None:
        by_name = {tool["name"]: tool["description"] for tool in SEAT_WATCH_TOOLS}
        for name in ("watch_course_sections", "check_watched_course_sections"):
            self.assertIn("cannot run in the background", by_name[name])
        self.assertIn("scheduled task on the client", by_name["check_watched_course_sections"])

    def test_no_background_machinery(self) -> None:
        import ninova_mcp.seat_watch as module

        source = open(module.__file__, encoding="utf-8").read()
        for word in ("Thread(", "Timer(", "sched.", "asyncio", "time.sleep"):
            self.assertNotIn(word, source)


class ScheduleCacheTests(unittest.TestCase):
    """The public schedule honours its own short TTL and stamps each real fetch."""

    def make_client(self) -> tuple[ObsPublicClient, list[str]]:
        client = ObsPublicClient(cache_ttl=3600.0)
        fetched: list[str] = []

        def fake_get_html(path: str, *, params: dict[str, Any] | None = None) -> tuple[str, str]:
            fetched.append(path)
            if path.endswith("SearchBransKoduByProgramSeviye"):
                return json.dumps([{"bransKoduId": 3, "dersBransKodu": "BLG"}]), "https://obs.itu.edu.tr/x"
            if path.endswith("GetAktifDonemByProgramSeviye"):
                return json.dumps({"aktifDonem": TERM}), "https://obs.itu.edu.tr/x"
            return "<html><body>kayit bulunamadi</body></html>", "https://obs.itu.edu.tr/x"

        client._get_html = fake_get_html  # type: ignore[method-assign]
        return client, fetched

    def test_schedule_uses_the_schedule_ttl_and_keeps_its_stamp_while_cached(self) -> None:
        with patch.dict(os.environ, {"NINOVA_PUBLIC_SCHEDULE_CACHE_TTL_SECONDS": "60"}):
            client, fetched = self.make_client()
        clock = [1000.0]
        with patch("ninova_mcp.cache.time.monotonic", lambda: clock[0]):
            first = client.get_course_schedule("LS", "BLG")
            self.assertIsNotNone(datetime.fromisoformat(first["fetched_at"]).tzinfo)
            clock[0] += 30
            again = client.get_course_schedule("LS", "BLG")
            self.assertEqual(again["fetched_at"], first["fetched_at"])
            self.assertEqual(sum(path.endswith("DersProgramSearch") for path in fetched), 1)
            clock[0] += 31
            client.get_course_schedule("LS", "BLG")
            self.assertEqual(sum(path.endswith("DersProgramSearch") for path in fetched), 2)
            # The department list and active term stay on the long catalog TTL.
            self.assertEqual(sum(path.endswith("SearchBransKoduByProgramSeviye") for path in fetched), 1)


if __name__ == "__main__":
    unittest.main()
