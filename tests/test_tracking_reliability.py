"""A bounded synchronization must preserve other still-enrolled courses."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from ninova_mcp.server import NinovaMcpApp
from ninova_mcp.tracking import SNAPSHOT_SCOPES, load_tracking_state, save_tracking_state


def course(identifier: int) -> dict:
    return {"code": f"CEN {100 + identifier}", "title": "Synthetic Course", "url": f"https://ninova.itu.edu.tr/Sinif/1.{identifier}"}


def snapshot(item: dict, title: str = "Original announcement") -> dict:
    return {"course": item, "coverage": {scope: {"status": "complete"} for scope in SNAPSHOT_SCOPES}, "overview": {
        "announcements": [{"url": item["url"] + "/Duyuru/1", "title": title}],
        "assignments": [], "class_files": [], "lesson_files": [], "info": {},
        "grades": {"grades": []}, "message_board": {"topics": []},
        "attendance": {"weeks": []},
        "remote_learning": {"active_sessions": [], "past_sessions": []},
    }}


class BoundedCourseSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = NinovaMcpApp.__new__(NinovaMcpApp)
        self.app.tracking_state_path = Path(self.directory.name) / "tracking-state.json"
        self.courses = [course(1), course(2), course(3)]
        self.state = {
            "version": 1, "last_sync_at": "2026-01-01T00:00:00+00:00", "updates": [],
            "enrollment_coverage": {"status": "complete"},
            "courses": {item["url"]: {"course": item, "synced_at": "previous", "snapshot": snapshot(item)} for item in self.courses},
        }
        save_tracking_state(self.app.tracking_state_path, self.state)
        self.app.list_courses = Mock(return_value={"courses": self.courses, "enrollment_coverage": {"status": "complete"}})
        self.app._collect_course_snapshot = Mock(side_effect=lambda item, **kwargs: snapshot(item, "Updated announcement"))

    def test_course_limit_preserves_unsynchronized_courses_and_their_snapshots(self) -> None:
        previous = copy.deepcopy(self.state["courses"])
        result = self.app.sync_all_courses(course_limit=1, include_files=False)
        saved = load_tracking_state(self.app.tracking_state_path)
        self.assertEqual(set(saved["courses"]), set(previous))
        for item in self.courses[1:]:
            self.assertEqual(saved["courses"][item["url"]], previous[item["url"]])
        self.assertEqual(result["course_count"], 1)
        self.app._collect_course_snapshot.assert_called_once()
        self.assertEqual(result["errors"], [])
        self.assertTrue(any(update["action"] == "changed" for update in result["updates"]))
        self.assertFalse(any(update["entity_type"] == "course" and update["action"] == "removed" for update in result["updates"]))

    def test_limited_sync_still_removes_courses_absent_from_the_full_enrollment_list(self) -> None:
        self.app.list_courses.return_value = {"courses": self.courses[:2], "enrollment_coverage": {"status": "complete"}}
        result = self.app.sync_all_courses(course_limit=1, include_files=False)
        saved = load_tracking_state(self.app.tracking_state_path)
        self.assertEqual(set(saved["courses"]), {item["url"] for item in self.courses[:2]})
        removals = [update for update in result["updates"] if update["entity_type"] == "course" and update["action"] == "removed"]
        self.assertEqual([update["course"]["url"] for update in removals], [self.courses[2]["url"]])
        self.assertEqual(saved["courses"][self.courses[1]["url"]], self.state["courses"][self.courses[1]["url"]])

    def test_failed_selected_course_does_not_remove_still_enrolled_courses(self) -> None:
        self.app._collect_course_snapshot.side_effect = RuntimeError("Synthetic unavailable course page.")
        result = self.app.sync_all_courses(course_limit=1, include_files=False)
        saved = load_tracking_state(self.app.tracking_state_path)
        self.assertEqual(saved["courses"], self.state["courses"])
        self.assertEqual(result["course_count"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["updates"], [])


if __name__ == "__main__":
    unittest.main()
