"""The readable change summary built from tracking change records."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ninova_mcp.change_summary import summarize_updates
from ninova_mcp.client import NinovaError
from ninova_mcp.server import NinovaMcpApp
from ninova_mcp.tracking import SNAPSHOT_SCOPES, diff_course_snapshots, save_tracking_state, utc_now_iso

BASE = "https://ninova.itu.edu.tr"
COURSE = {"code": "ZZT 101", "title": "Sentetik Ders", "url": BASE + "/Sinif/9.1"}
OTHER = {"code": "ZZT 102", "title": "İkinci Ders", "url": BASE + "/Sinif/9.2"}
WHEN = "2026-10-05T09:00:00+00:00"


def record(entity_type: str, action: str, before=None, after=None, *, course=COURSE, when=WHEN) -> dict:
    payload = after if after is not None else before
    return {
        "id": f"{entity_type}-{action}-{(payload or {}).get('title') or (payload or {}).get('name')}",
        "detected_at": when, "course": course, "entity_type": entity_type, "action": action,
        "entity_id": "x", "summary": f"{entity_type}:{action}:x", "before": before, "after": after,
    }


def texts(summary: dict) -> list[str]:
    return [line["text"] for group in summary["courses"] for line in group["lines"]]


class LineTests(unittest.TestCase):
    def test_each_record_type_becomes_its_line(self) -> None:
        homework = {"title": "Ödev 2", "url": COURSE["url"] + "/Odev/2", "submission_end": "20 Ekim 2026 23:59",
                    "submission_end_iso": "2026-10-20T23:59:00+03:00"}
        cases = [
            (record("announcements", "added", None, {"title": "Vize tarihi", "url": "u1"}), "announcement",
             "Yeni duyuru: Vize tarihi"),
            (record("class_files", "added", None, {"name": "Hafta3.pdf", "entry_type": "file", "url": "u2"}), "file",
             "Yeni dosya: Hafta3.pdf"),
            (record("lesson_files", "added", None, {"name": "Notlar.pdf", "url": "u3"}), "file", "Yeni dosya: Notlar.pdf"),
            (record("assignments", "added", None, homework), "assignment",
             "Yeni ödev: Ödev 2 (son teslim 20 Ekim 2026 23:59)"),
            (record("assignments", "changed", {**homework, "uploaded_file_count": 0}, {**homework, "uploaded_file_count": 1}),
             "assignment", "Ödev güncellendi: Ödev 2"),
            (record("grades", "added", None, {"title": "Vize", "score": "75"}), "grade", "Not girildi: Vize (75)"),
            (record("grades", "changed", {"title": "Vize", "score": "75"}, {"title": "Vize", "score": "80"}), "grade",
             "Not değişti: Vize (75 → 80)"),
            (record("course", "added", None, COURSE), "course", "Yeni ders: ZZT 101 Sentetik Ders"),
            (record("course", "removed", COURSE, None), "course", "Ders listeden çıktı: ZZT 101 Sentetik Ders"),
        ]
        for update, line_type, text in cases:
            with self.subTest(text=text):
                summary = summarize_updates([update])
                line = summary["courses"][0]["lines"][0]
                self.assertEqual((line["type"], line["text"], line["date"]), (line_type, text, WHEN))
                self.assertEqual(summary["total"], 1)
                self.assertEqual(summary["omitted_count"], 0)

    def test_moved_deadline_names_old_and_new_date(self) -> None:
        before = {"title": "Proje", "url": "u", "submission_end": "20 Ekim 2026 23:59",
                  "submission_end_iso": "2026-10-20T23:59:00+03:00"}
        after = {**before, "submission_end": "27 Ekim 2026 23:59", "submission_end_iso": "2026-10-27T23:59:00+03:00"}
        summary = summarize_updates([record("assignments", "changed", before, after)])
        line = summary["courses"][0]["lines"][0]
        self.assertEqual(line["type"], "deadline_changed")
        self.assertEqual(line["text"], "Teslim tarihi değişti: Proje (20 Ekim 2026 23:59 → 27 Ekim 2026 23:59)")
        self.assertEqual(line["url"], "u")
        self.assertEqual(summary["courses"][0]["counts"], {"assignment": 1})

    def test_attendance_info_and_removals_are_only_counted(self) -> None:
        summary = summarize_updates([
            record("attendance_weeks", "changed", {"week": "1"}, {"week": "1", "x": 1}),
            record("course_info", "changed", {"identity": {}}, {"identity": {"a": 1}}),
            record("past_remote_sessions", "added", None, {"text": "Oturum"}),
            record("announcements", "removed", {"title": "Eski duyuru"}, None),
            record("class_files", "removed", {"name": "eski.pdf"}, None),
            record("class_files", "added", None, {"name": "Klasör", "entry_type": "folder"}),
        ])
        self.assertEqual(texts(summary), [])
        self.assertEqual(summary["courses"][0]["counts"], {"other": 4, "removed": 2})
        self.assertEqual(summary["headline"], "1 derste 6 değişiklik: 4 diğer, 2 silinen.")
        self.assertNotIn("Eski duyuru", str(summary))

    def test_real_diff_records_are_understood(self) -> None:
        def snap(announcements: list[dict]) -> dict:
            return {
                "coverage": {scope: {"status": "complete"} for scope in SNAPSHOT_SCOPES},
                "overview": {
                    "info": {}, "announcements": announcements, "assignments": [], "class_files": [],
                    "lesson_files": [], "grades": {"grades": []}, "message_board": {"topics": []},
                    "attendance": {"weeks": []}, "remote_learning": {"active_sessions": [], "past_sessions": []},
                },
            }

        updates = diff_course_snapshots(
            course=COURSE, previous_snapshot=snap([]),
            current_snapshot=snap([{"title": "Sınav salonları", "url": COURSE["url"] + "/Duyuru/1"}]), detected_at=WHEN,
        )
        summary = summarize_updates(updates)
        self.assertEqual(texts(summary), ["Yeni duyuru: Sınav salonları"])
        self.assertEqual(summary["courses"][0]["course"], {"code": "ZZT 101", "title": "Sentetik Ders"})
        self.assertEqual((summary["since"], summary["until"]), (WHEN, WHEN))


class BoundsTests(unittest.TestCase):
    def test_empty_list(self) -> None:
        summary = summarize_updates([])
        self.assertEqual(summary, {
            "since": None, "until": None, "headline": "Değişiklik yok.", "total": 0, "courses": [], "omitted_count": 0,
        })

    def test_lines_per_course_are_bounded_and_important_ones_come_first(self) -> None:
        updates = [record("class_files", "added", None, {"name": f"f{n}.pdf"}) for n in range(12)]
        updates.append(record("grades", "added", None, {"title": "Final", "score": "90"}))
        summary = summarize_updates(updates)
        lines = summary["courses"][0]["lines"]
        self.assertEqual(len(lines), 8)
        self.assertEqual(lines[0]["text"], "Not girildi: Final (90)")
        self.assertEqual(summary["omitted_count"], 5)
        self.assertEqual(summary["courses"][0]["counts"], {"file": 12, "grade": 1})
        self.assertEqual(summary["headline"], "1 derste 13 değişiklik: 12 dosya, 1 not.")

    def test_courses_are_bounded(self) -> None:
        updates = []
        for n in range(4):
            item = {"code": f"ZZT {n}", "title": "Ders", "url": f"{BASE}/Sinif/{n}"}
            updates += [record("announcements", "added", None, {"title": f"d{n}-{k}"}, course=item) for k in range(n + 1)]
        summary = summarize_updates(updates, max_courses=2, max_lines_per_course=3)
        self.assertEqual([group["course"]["code"] for group in summary["courses"]], ["ZZT 3", "ZZT 2"])
        # ZZT 3 loses one line, ZZT 1 and ZZT 0 lose all three of theirs.
        self.assertEqual(summary["omitted_count"], 4)
        self.assertEqual(summary["total"], 10)

    def test_since_filters_and_rejects_nonsense(self) -> None:
        updates = [
            record("announcements", "added", None, {"title": "eski"}, when="2026-10-01T08:00:00+00:00"),
            record("announcements", "added", None, {"title": "yeni"}, course=OTHER, when="2026-10-06T08:00:00+03:00"),
        ]
        summary = summarize_updates(updates, since="2026-10-05")
        self.assertEqual(texts(summary), ["Yeni duyuru: yeni"])
        self.assertEqual(summary["since"], "2026-10-05")
        self.assertEqual(summary["headline"], "1 derste 1 değişiklik: 1 duyuru.")
        with self.assertRaises(ValueError):
            summarize_updates(updates, since="geçen pazartesi")


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = NinovaMcpApp.__new__(NinovaMcpApp)
        self.app.tracking_state_path = Path(directory.name) / "tracking-state.json"
        stamp = utc_now_iso()
        save_tracking_state(self.app.tracking_state_path, {
            "version": 2, "last_sync_at": stamp, "courses": {},
            "enrollment_coverage": {"status": "complete"},
            "updates": [
                record("announcements", "added", None, {"title": "Vize tarihi"}, when=stamp),
                record("grades", "added", None, {"title": "Quiz", "score": "8"}, when="2026-01-01T00:00:00+00:00"),
            ],
        })

    def test_get_updates_keeps_the_raw_list_by_default(self) -> None:
        result = self.app.get_updates()
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["updates"]), 2)
        self.assertNotIn("change_summary", result)

    def test_get_updates_summary_replaces_the_raw_list(self) -> None:
        result = self.app.get_updates(summary=True, since="2026-06-01")
        self.assertNotIn("updates", result)
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(texts(result["change_summary"]), ["Yeni duyuru: Vize tarihi"])
        self.assertEqual(result["status"], "ok")

    def test_since_also_filters_the_raw_list_and_bad_since_is_a_tool_error(self) -> None:
        self.assertEqual(self.app.get_updates(since="2026-06-01")["count"], 1)
        with self.assertRaises(NinovaError):
            self.app.get_updates(since="dün")


if __name__ == "__main__":
    unittest.main()
