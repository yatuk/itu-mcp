"""A stored tracking snapshot must say how old it is, and an old one must not read as an answer."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from ninova_mcp.cache import TtlCache
from ninova_mcp.server import NinovaMcpApp
from ninova_mcp.tracking import SNAPSHOT_SCOPES, load_tracking_state, save_tracking_state

BASE = "https://ninova.itu.edu.tr"
NOW = datetime.now(tz=UTC)


def iso(*, hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def course(identifier: int) -> dict:
    return {"code": f"ZZT {100 + identifier}", "title": f"Sentetik Ders {identifier}", "url": f"{BASE}/Sinif/9.{identifier}"}


def assignment(item: dict, *, due_in_days: float) -> dict:
    due = (NOW + timedelta(days=due_in_days)).astimezone().replace(microsecond=0)
    return {
        "title": "Sentetik Odev", "url": item["url"] + "/Odev/1",
        "submission_end": "ignored", "submission_end_iso": due.isoformat(),
        "requested_file_count": 1, "uploaded_file_count": 0,
    }


def snapshot(item: dict, assignments: list[dict] | None = None, *, captured_hours_ago: float = 0.0) -> dict:
    return {
        "course": item, "captured_at": iso(hours_ago=captured_hours_ago), "errors": [],
        "coverage": {scope: {"status": "complete"} for scope in SNAPSHOT_SCOPES},
        "overview": {
            "sections": [], "info": {}, "announcements": [], "assignments": assignments or [],
            "class_files": [], "lesson_files": [], "grades": {"grades": []},
            "message_board": {"topics": []}, "attendance": {"weeks": []},
            "remote_learning": {"active_sessions": [], "past_sessions": []},
        },
    }


class StalenessCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("NINOVA_TRACKING_MAX_AGE_SECONDS", None)
        self.app = NinovaMcpApp.__new__(NinovaMcpApp)
        self.app.tracking_state_path = Path(directory.name) / "tracking-state.json"
        self.old_course, self.new_course = course(1), course(2)
        self.app.list_courses = Mock(return_value={
            "courses": [self.new_course], "enrollment_coverage": {"status": "complete"},
        })
        self.app._collect_course_snapshot = Mock(
            side_effect=lambda item, **kwargs: snapshot(item, [assignment(item, due_in_days=3)])
        )

    def seed(self, *, hours_ago: float, courses: list[dict] | None = None, assignments: list[dict] | None = None) -> None:
        stamp = iso(hours_ago=hours_ago)
        save_tracking_state(self.app.tracking_state_path, {
            "version": 2, "last_sync_at": stamp, "updates": [],
            "enrollment_coverage": {"status": "complete", "baseline_complete": True, "last_complete_at": stamp},
            "courses": {
                item["url"]: {
                    "course": item, "synced_at": stamp,
                    "snapshot": snapshot(item, assignments, captured_hours_ago=hours_ago),
                }
                for item in (courses if courses is not None else [self.old_course])
            },
        })


class UpcomingDeadlinesStalenessTests(StalenessCase):
    def test_old_snapshot_is_refreshed_and_the_result_says_so(self) -> None:
        self.seed(hours_ago=24 * 64)
        result = self.app.get_upcoming_deadlines(days=14)
        self.app.list_courses.assert_called_once_with(refresh=True)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["stale"])
        self.assertTrue(result["refreshed"])
        self.assertEqual(result["refresh"]["reason"], "stale_snapshot")
        self.assertLess(result["snapshot_age_hours"], 1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["deadlines"][0]["course"]["code"], self.new_course["code"])
        self.assertEqual([item["code"] for item in result["courses_covered"]], [self.new_course["code"]])

    def test_automatic_refresh_skips_the_file_inventories(self) -> None:
        self.seed(hours_ago=24 * 64)
        self.app.get_upcoming_deadlines()
        self.assertFalse(self.app._collect_course_snapshot.call_args.kwargs["include_files"])

    def test_failed_refresh_returns_the_old_data_flagged_as_stale(self) -> None:
        self.seed(hours_ago=24 * 64)
        self.app.list_courses.side_effect = RuntimeError("Synthetic login failure")
        result = self.app.get_upcoming_deadlines(days=14)
        self.assertEqual(result["status"], "stale_snapshot")
        self.assertTrue(result["stale"])
        self.assertFalse(result["refreshed"])
        self.assertIn("Synthetic login failure", result["refresh"]["error"])
        self.assertGreater(result["snapshot_age_hours"], 24 * 63)
        self.assertEqual(result["count"], 0)
        self.assertIn("refresh=true", result["message"])
        self.assertEqual(result["stale_after_hours"], 12)

    def test_fresh_snapshot_is_answered_without_a_refresh(self) -> None:
        self.seed(hours_ago=1, assignments=[assignment(self.old_course, due_in_days=2)])
        result = self.app.get_upcoming_deadlines(days=14)
        self.app.list_courses.assert_not_called()
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["stale"])
        self.assertFalse(result["refreshed"])
        self.assertAlmostEqual(result["snapshot_age_hours"], 1, delta=0.1)
        self.assertEqual(result["count"], 1)
        self.assertNotIn("message", result)

    def test_missing_snapshot_with_a_failed_refresh_is_not_an_empty_answer(self) -> None:
        self.app.list_courses.side_effect = RuntimeError("Synthetic login failure")
        result = self.app.get_upcoming_deadlines()
        self.assertEqual(result["status"], "no_snapshot")
        self.assertTrue(result["stale"])
        self.assertIsNone(result["snapshot_age_hours"])
        self.assertIsNone(result["last_sync_at"])
        self.assertIn("message", result)

    def test_threshold_is_tunable(self) -> None:
        os.environ["NINOVA_TRACKING_MAX_AGE_SECONDS"] = "3600"
        self.seed(hours_ago=2)
        result = self.app.get_upcoming_deadlines()
        self.app.list_courses.assert_called_once()
        self.assertEqual(result["stale_after_hours"], 1)
        self.assertTrue(result["refreshed"])

    def test_a_failed_automatic_refresh_is_not_repeated_on_every_call(self) -> None:
        self.seed(hours_ago=24 * 64)
        self.app.list_courses.side_effect = RuntimeError("Synthetic login failure")
        self.app.get_upcoming_deadlines()
        second = self.app.get_upcoming_deadlines()
        self.assertEqual(self.app.list_courses.call_count, 1)
        self.assertEqual(second["status"], "stale_snapshot")
        self.assertEqual(second["refresh"]["skipped"], "recent_automatic_refresh")
        self.app.get_upcoming_deadlines(refresh=True)
        self.assertEqual(self.app.list_courses.call_count, 2)

    def test_snapshot_of_other_courses_than_the_cached_course_list_is_refreshed(self) -> None:
        self.seed(hours_ago=1)
        self.app._course_cache = TtlCache(60)
        self.app._course_cache.set("courses", [self.new_course])
        result = self.app.get_upcoming_deadlines()
        self.assertEqual(result["refresh"]["reason"], "course_list_changed")
        self.assertTrue(result["refreshed"])
        self.assertEqual(set(load_tracking_state(self.app.tracking_state_path)["courses"]), {self.new_course["url"]})

    def test_course_set_difference_is_reported_when_it_cannot_be_refreshed(self) -> None:
        self.seed(hours_ago=1)
        self.app._course_cache = TtlCache(60)
        self.app._course_cache.set("courses", [self.new_course])
        self.app.list_courses.side_effect = RuntimeError("Synthetic login failure")
        result = self.app.get_upcoming_deadlines()
        self.assertEqual(result["status"], "stale_snapshot")
        self.assertTrue(result["stale"])
        check = result["course_set_check"]
        self.assertEqual([item["code"] for item in check["missing_from_snapshot"]], [self.new_course["code"]])
        self.assertEqual([item["code"] for item in check["not_in_course_list"]], [self.old_course["code"]])

    def test_without_a_cached_course_list_the_course_set_is_reported_as_unchecked(self) -> None:
        self.seed(hours_ago=1)
        result = self.app.get_upcoming_deadlines()
        self.assertEqual(result["course_set_check"], {"checked": False, "reason": "course_list_not_cached"})

    def test_a_course_the_last_sync_could_not_read_keeps_the_result_stale(self) -> None:
        self.seed(hours_ago=24 * 64, courses=[self.old_course, self.new_course])
        self.app.list_courses.return_value = {
            "courses": [self.old_course, self.new_course], "enrollment_coverage": {"status": "complete"},
        }

        def collect(item: dict, **kwargs: object) -> dict:
            if item["url"] == self.old_course["url"]:
                raise RuntimeError("Synthetic course page failure")
            return snapshot(item)

        self.app._collect_course_snapshot.side_effect = collect
        result = self.app.get_upcoming_deadlines()
        self.assertTrue(result["refreshed"])
        self.assertEqual(result["status"], "stale_snapshot")
        self.assertEqual([item["code"] for item in result["stale_courses"]], [self.old_course["code"]])

    def test_unread_assignment_list_is_reported_as_incomplete(self) -> None:
        self.seed(hours_ago=1)
        state = load_tracking_state(self.app.tracking_state_path)
        entry = state["courses"][self.old_course["url"]]["snapshot"]
        entry["coverage"]["assignments"] = {"status": "failed", "reason": "fetch_or_parse_failed"}
        save_tracking_state(self.app.tracking_state_path, state)
        result = self.app.get_upcoming_deadlines()
        self.assertEqual(result["status"], "incomplete_snapshot")
        self.assertEqual(result["incomplete_courses"][0]["code"], self.old_course["code"])
        self.assertIn("message", result)


class SiblingToolStalenessTests(StalenessCase):
    def test_get_updates_flags_an_old_history(self) -> None:
        self.seed(hours_ago=24 * 64)
        result = self.app.get_updates()
        self.assertEqual(result["status"], "stale_snapshot")
        self.assertTrue(result["stale"])
        self.assertGreater(result["snapshot_age_hours"], 24 * 63)
        self.assertIn("sync_all_courses", result["message"])
        self.assertEqual(result["count"], 0)
        self.app.list_courses.assert_not_called()

    def test_get_updates_on_a_fresh_history_is_not_flagged(self) -> None:
        self.seed(hours_ago=1)
        result = self.app.get_updates()
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["stale"])
        self.assertNotIn("message", result)

    def test_get_updates_without_any_sync_says_so(self) -> None:
        result = self.app.get_updates()
        self.assertEqual(result["status"], "no_snapshot")
        self.assertTrue(result["stale"])

    def test_stored_course_overview_carries_its_age(self) -> None:
        self.seed(hours_ago=24 * 64)
        self.app._resolve_course = Mock(return_value=self.old_course)
        self.app._out = lambda payload, compact=False: payload
        result = self.app.get_course_overview(self.old_course["code"])
        self.assertEqual(result["source"], "tracked_state")
        self.assertTrue(result["stale"])
        self.assertGreater(result["snapshot_age_hours"], 24 * 63)
        self.assertIn("refresh=true", result["stale_note"])


if __name__ == "__main__":
    unittest.main()
