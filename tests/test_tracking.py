from __future__ import annotations

import unittest

from ninova_mcp.tracking import diff_course_snapshots, merge_updates


COURSE = {
    "code": "BBF 201E",
    "title": "Olasılık ve İstatistik",
    "url": "https://ninova.itu.edu.tr/Sinif/36851.118733",
}


def _snapshot(*, announcement_title: str = "Quiz", assignment_uploaded: int = 0) -> dict:
    return {
        "course": COURSE,
        "captured_at": "2026-04-21T10:00:00+00:00",
        "overview": {
            "sections": [],
            "info": {
                "identity": {"Ders Kodu": "BBF 201E"},
                "class_meta": {},
                "course_details": {},
                "weekly_schedule": [],
            },
            "announcements": [
                {
                    "title": announcement_title,
                    "url": "https://ninova.itu.edu.tr/Sinif/36851.118733/Duyuru/1",
                    "published_at": "20 Nisan 2026 10:00",
                    "published_at_iso": "2026-04-20T10:00:00+03:00",
                }
            ],
            "assignments": [
                {
                    "title": "Homework 1",
                    "url": "https://ninova.itu.edu.tr/Sinif/36851.118733/Odev/1",
                    "submission_end": "25 Nisan 2026 23:59",
                    "submission_end_iso": "2026-04-25T23:59:00+03:00",
                    "requested_file_count": 1,
                    "uploaded_file_count": assignment_uploaded,
                }
            ],
            "class_files": [],
            "lesson_files": [],
            "grades": {"grades": []},
            "message_board": {"topics": []},
            "attendance": {"weeks": []},
            "remote_learning": {"active_sessions": [], "past_sessions": []},
        },
        "errors": [],
    }


class TrackingTests(unittest.TestCase):
    def test_diff_course_snapshots_detects_changes(self) -> None:
        previous = _snapshot(announcement_title="Quiz", assignment_uploaded=0)
        current = _snapshot(announcement_title="Updated Quiz", assignment_uploaded=1)

        updates = diff_course_snapshots(
            course=COURSE,
            previous_snapshot=previous,
            current_snapshot=current,
            detected_at="2026-04-21T12:00:00+00:00",
        )

        entity_types = {(item["entity_type"], item["action"]) for item in updates}
        self.assertIn(("announcements", "changed"), entity_types)
        self.assertIn(("assignments", "changed"), entity_types)

    def test_merge_updates_deduplicates_by_id(self) -> None:
        updates = diff_course_snapshots(
            course=COURSE,
            previous_snapshot=_snapshot(announcement_title="Quiz"),
            current_snapshot=_snapshot(announcement_title="Updated Quiz"),
            detected_at="2026-04-21T12:00:00+00:00",
        )
        merged = merge_updates(updates, updates)
        self.assertEqual(len(merged), len(updates))


if __name__ == "__main__":
    unittest.main()
