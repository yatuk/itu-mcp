"""Partial reads cannot erase complete tracking data or fabricate changes."""

from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ninova_mcp.cache import TtlCache
from ninova_mcp.client import NinovaError
from ninova_mcp.server import NinovaMcpApp
from ninova_mcp.tracking import (
    SNAPSHOT_SCOPES, diff_course_snapshots, load_tracking_state,
    merge_course_snapshot, save_tracking_state,
)
from ninova_mcp.tracking_coverage import enrollment_coverage, scope_coverage


BASE = "https://ninova.itu.edu.tr"
COURSE = {"code": "UCK 358E", "title": "Machine Learning", "url": BASE + "/Sinif/1.1", "context": "UCK 358E"}
ROOT = COURSE["url"] + "/SinifDosyalari"


def file_table(*rows: tuple[str, str, str]) -> str:
    return '<table><tr><th>Dosyalar</th><th>Boyut</th><th>Tarih</th></tr>' + "".join(
        f'<tr><td><img src="/img/{kind}.png"><a href="{url}">{name}</a></td><td>1 KB</td><td>1 Eylül 2026</td></tr>'
        for name, url, kind in rows
    ) + '</table>'


def snapshot(files: list[dict] | None = None) -> dict:
    return {
        "course": COURSE, "captured_at": "2026-09-01T00:00:00+00:00", "errors": [],
        "coverage": {scope: {"status": "complete"} for scope in SNAPSHOT_SCOPES},
        "overview": {
            "info": {}, "sections": [], "announcements": [], "assignments": [],
            "class_files": files or [], "lesson_files": [], "grades": {"grades": []},
            "message_board": {"topics": []}, "attendance": {"weeks": []},
            "remote_learning": {"active_sessions": [], "past_sessions": []},
        },
    }


class SyntheticClient:
    base_url = BASE

    def __init__(self):
        self.pages = {
            COURSE["url"]: '<a href="' + COURSE["url"] + '/SinifBilgileri">Sınıf Bilgileri</a>',
            ROOT: file_table(("Syllabus.pdf", ROOT + "?file=1", "pdf"), ("Lecture Notes", ROOT + "?folder=1", "folder")),
            ROOT + "?folder=1": file_table(("Lecture1.pdf", ROOT + "?file=2", "pdf")),
            COURSE["url"] + "/DersDosyalari": file_table(),
        }

    def get_html(self, url):
        value = self.pages.get(url, "<html><body>Unrecognized synthetic page</body></html>")
        if isinstance(value, Exception):
            raise value
        return value, SimpleNamespace(url=url)


class TrackingCompletenessTests(unittest.TestCase):
    def setUp(self):
        network_guard = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected HTTP in offline tracking test"))
        network_guard.start()
        self.addCleanup(network_guard.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = NinovaMcpApp.__new__(NinovaMcpApp)
        self.app.tracking_state_path = Path(directory.name) / "tracking.json"
        self.app._client = SyntheticClient()
        self.app.list_courses = Mock(return_value={"courses": [COURSE], "enrollment_coverage": {"status": "complete"}})

    def stored(self):
        return load_tracking_state(self.app.tracking_state_path)

    def seed(self, previous=None, *, legacy=False):
        previous = previous or snapshot([{"name": "Syllabus.pdf", "url": ROOT + "?file=1"}])
        if legacy:
            previous.pop("coverage", None)
        state = {
            "version": 1 if legacy else 2, "last_sync_at": "previous", "updates": [],
            "courses": {COURSE["url"]: {"course": COURSE, "synced_at": "previous", "snapshot": previous}},
        }
        if not legacy:
            state["enrollment_coverage"] = {"status": "complete", "last_complete_at": "previous"}
        save_tracking_state(self.app.tracking_state_path, state)

    def test_actual_collector_skip_failure_depth_and_recovery_preserve_files(self):
        self.app.sync_all_courses()
        original = copy.deepcopy(self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"]["class_files"])
        self.assertEqual(len(original), 3)

        skipped = self.app.sync_all_courses(include_files=False)
        self.assertEqual(skipped["updates"], [])
        self.assertEqual(skipped["courses"][0]["coverage"]["class_files"]["status"], "skipped")

        self.app._client.pages[ROOT + "?folder=1"] = TimeoutError("Synthetic folder timeout")
        failed = self.app.sync_all_courses()
        self.assertEqual(failed["updates"], [])
        self.assertTrue(any(e.get("scope") == "class_files" and "timeout" in e["error"] for e in failed["errors"]))
        self.assertEqual(failed["courses"][0]["coverage"]["class_files"]["status"], "partial")
        self.app._client.pages[ROOT + "?folder=1"] = file_table(("Lecture1.pdf", ROOT + "?file=2", "pdf"))

        shallow = self.app.sync_all_courses(file_max_depth=0)
        self.assertEqual(shallow["updates"], [])
        self.assertEqual(shallow["courses"][0]["coverage"]["class_files"]["status"], "partial")
        self.assertEqual(self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"]["class_files"], original)

        recovered = self.app.sync_all_courses()
        self.assertEqual(recovered["updates"], [])
        self.assertEqual(recovered["courses"][0]["coverage"]["class_files"]["status"], "complete")
        self.app._client.pages[ROOT + "?folder=1"] = file_table()
        removed = self.app.sync_all_courses()
        self.assertEqual([(u["action"], u["before"]["name"]) for u in removed["updates"]], [("removed", "Lecture1.pdf")])
        self.assertEqual(self.app.sync_all_courses()["updates"], [])

    def test_root_failure_preserves_files_and_reports_scope(self):
        self.app.sync_all_courses()
        old = self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"]["class_files"]
        self.app._client.pages[ROOT] = RuntimeError("Synthetic root failure")
        result = self.app.sync_all_courses()
        self.assertEqual(result["updates"], [])
        self.assertEqual(result["courses"][0]["coverage"]["class_files"]["status"], "failed")
        self.assertEqual(self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"]["class_files"], old)
        self.assertTrue(any(e.get("scope") == "class_files" for e in result["errors"]))

    def test_failed_enrollment_and_partial_enrollment_cannot_remove_courses(self):
        self.seed()
        previous = copy.deepcopy(self.stored()["courses"])
        for listing in (
            {"courses": [], "parse_warning": "Synthetic failed course parse"},
            {"courses": [], "enrollment_coverage": {"status": "partial", "reason": "pagination_not_traversed"}},
        ):
            self.app.list_courses.return_value = listing
            result = self.app.sync_all_courses()
            self.assertEqual(result["updates"], [])
            self.assertEqual(self.stored()["courses"], previous)
            self.assertTrue(any(e.get("scope") == "enrollment" for e in result["errors"]))
        self.app.list_courses.return_value = {"courses": [], "enrollment_coverage": {"status": "complete", "reason": "explicit_empty_enrollment"}}
        self.assertEqual(self.app.sync_all_courses()["updates"][0]["action"], "removed")
        self.assertEqual(self.stored()["courses"], {})

    def test_legacy_state_rebaseline_does_not_fabricate_adds_or_removals(self):
        self.seed(snapshot(), legacy=True)
        current = snapshot([{"name": "Syllabus.pdf", "url": ROOT + "?file=1"}])
        self.app._collect_course_snapshot = Mock(return_value=current)
        result = self.app.sync_all_courses()
        self.assertEqual(result["updates"], [])
        self.assertEqual(self.stored()["version"], 2)
        self.assertEqual(self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"]["class_files"], current["overview"]["class_files"])
        self.app._collect_course_snapshot.return_value = snapshot()
        self.assertEqual(self.app.sync_all_courses()["updates"][0]["action"], "removed")

    def test_legacy_absent_course_retained_until_second_complete_enrollment(self):
        self.seed(legacy=True)
        self.app.list_courses.return_value = {"courses": [], "enrollment_coverage": {"status": "complete"}}
        self.assertEqual(self.app.sync_all_courses()["updates"], [])
        self.assertIn(COURSE["url"], self.stored()["courses"])
        self.assertEqual(self.app.sync_all_courses()["updates"][0]["action"], "removed")

    def test_incomplete_initial_scope_is_silent_when_first_completed(self):
        previous = snapshot()
        previous["coverage"]["class_files"] = {"status": "skipped"}
        current = snapshot([{"url": "synthetic-file", "name": "Syllabus"}])
        self.assertEqual(diff_course_snapshots(course=COURSE, previous_snapshot=previous, current_snapshot=current), [])

    def test_all_failed_or_partial_scopes_retain_their_last_observation(self):
        previous = snapshot()
        previous["overview"]["announcements"] = [{"url": "notice-1", "title": "Keep me"}]
        previous["overview"]["assignments"] = [{"url": "assignment-1", "title": "Keep me"}]
        for status in ("failed", "partial", "skipped", "unknown"):
            current = snapshot()
            current["coverage"]["announcements"] = {"status": status}
            current["coverage"]["assignments"] = {"status": status}
            self.assertEqual(diff_course_snapshots(course=COURSE, previous_snapshot=previous, current_snapshot=current), [])
            retained = merge_course_snapshot(previous, current)
            self.assertEqual(retained["overview"]["assignments"], previous["overview"]["assignments"])
            self.assertTrue(retained["coverage"]["assignments"]["last_complete_at"])
            self.assertFalse(retained["snapshot_complete"])

    def test_skipping_assignment_details_keeps_the_last_detail_without_change_event(self):
        previous = snapshot()
        previous["overview"]["assignments"] = [{"url": "assignment-1", "title": "Assignment", "description": "Earlier detail", "upload_url": "upload-1"}]
        current = snapshot()
        current["overview"]["assignments"] = [{"url": "assignment-1", "title": "Assignment", "upload_url": None}]
        current["coverage"]["assignments"]["details_included"] = False
        retained = merge_course_snapshot(previous, current)
        self.assertEqual(retained["overview"]["assignments"], previous["overview"]["assignments"])
        self.assertTrue(retained["coverage"]["assignments"]["details_retained"])
        self.assertEqual(diff_course_snapshots(course=COURSE, previous_snapshot=previous, current_snapshot=retained), [])

    def test_unrecognized_file_html_is_not_a_complete_empty_inventory(self):
        self.app.sync_all_courses()
        self.app._client.pages[ROOT] = "<html><table><tr><td>New unknown markup</td></tr></table></html>"
        result = self.app.sync_all_courses()
        self.assertEqual(result["updates"], [])
        self.assertEqual(len(self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"]["class_files"]), 3)

    def test_one_valid_file_does_not_hide_an_unparsed_row(self):
        self.app.sync_all_courses()
        self.app._client.pages[ROOT] = file_table(("Syllabus.pdf", ROOT + "?file=1", "pdf")).replace('</table>', '<tr><td>Unsupported folder markup</td></tr></table>')
        result = self.app.sync_all_courses()
        self.assertEqual(result["updates"], [])
        self.assertTrue(any(e.get("error") == "unparsed_file_rows" for e in result["errors"]))
        self.assertEqual(len(self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"]["class_files"]), 3)

    def test_actual_assignment_limit_retains_old_inventory_and_reports_partial(self):
        previous = snapshot()
        previous["overview"]["assignments"] = [{"title": "Earlier assignment", "url": COURSE["url"] + "/Odev/old"}]
        self.seed(previous)
        self.app._client.pages[COURSE["url"] + "/Odevler"] = '<table id="gvOdevListesi">' + "".join(
            f'<tr><td><h2><a href="{COURSE["url"]}/Odev/{number}">Assignment {number}</a></h2></td></tr>'
            for number in range(201)
        ) + '</table>'
        result = self.app.sync_all_courses(include_files=False)
        self.assertEqual(result["updates"], [])
        self.assertTrue(any(e.get("scope") == "assignments" and e["error"] == "item_limit" for e in result["errors"]))
        self.assertEqual(self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"]["assignments"], previous["overview"]["assignments"])

    def test_assignment_detail_failure_is_partial_and_preserves_other_scopes(self):
        previous = snapshot()
        previous["overview"]["assignments"] = [{"title": "Earlier assignment", "url": COURSE["url"] + "/Odev/1"}]
        self.seed(previous)
        self.app._client.pages[COURSE["url"] + "/Odevler"] = f'<table id="gvOdevListesi"><tr><td><h2><a href="{COURSE["url"]}/Odev/1">Earlier assignment</a></h2></td></tr></table>'
        self.app._merge_assignment_detail = Mock(side_effect=RuntimeError("Synthetic detail unavailable"))
        result = self.app.sync_all_courses(include_files=False, include_assignment_details=True)
        self.assertEqual(result["course_count"], 1)
        self.assertEqual(result["updates"], [])
        self.assertEqual(result["courses"][0]["coverage"]["assignments"]["status"], "partial")
        self.assertTrue(any(e.get("scope") == "assignments" for e in result["errors"]))
        self.assertEqual(self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"]["assignments"], previous["overview"]["assignments"])


class CoverageParsingTests(unittest.TestCase):
    def test_empty_enrollment_needs_a_scope_specific_empty_statement(self):
        for html in ("<h1>Welcome</h1>", "<p>Herhangi bir ödev bulunmamaktadır.</p>", '<input type="password">'):
            self.assertNotEqual(enrollment_coverage(html, BASE + "/Kampus1", BASE + "/Kampus1", [])["status"], "complete")
        self.assertEqual(enrollment_coverage("<p>Kayıtlı olduğunuz herhangi bir sınıf bulunmamaktadır.</p>", BASE + "/Kampus1", BASE + "/Kampus1", [])["status"], "complete")

    def test_item_limit_and_pagination_are_partial_even_with_valid_rows(self):
        payload = {"assignments": [{"title": "Synthetic"}] * 201}
        self.assertEqual(scope_coverage("<h1>Ödevler</h1>", ROOT, ROOT, "assignments", payload)["status"], "partial")
        payload["assignments"] = payload["assignments"][:1]
        for html in ('<a href="javascript:__doPostBack(\'grid\',\'Page$2\')">2</a>', '<a href="?page=2">Next</a>'):
            self.assertEqual(scope_coverage(html, ROOT, ROOT, "assignments", payload)["status"], "partial")
            self.assertEqual(enrollment_coverage(html, ROOT, ROOT, [COURSE])["status"], "partial")

    def test_unexpected_redirect_is_incomplete_despite_content(self):
        self.assertEqual(scope_coverage("<h1>Other page</h1>", BASE + "/login", ROOT, "class_files", {"entries": [{}]})["status"], "failed")


class CourseResolutionTests(unittest.TestCase):
    def setUp(self):
        self.app = NinovaMcpApp.__new__(NinovaMcpApp)
        self.app.list_courses = Mock(return_value={"courses": [COURSE]})

    def test_compact_spaced_case_and_nbsp_codes_use_the_same_course(self):
        for code in ("UCK358E", "UCK 358E", "uck 358e", " UCK\u00a0358 E "):
            self.assertEqual(self.app._resolve_course(code), COURSE)

    def test_exact_suffix_and_ambiguous_sections_remain_distinct(self):
        lab = {**COURSE, "code": "UCK 358EL", "url": BASE + "/Sinif/1.2"}
        self.app.list_courses.return_value = {"courses": [lab, COURSE]}
        self.assertEqual(self.app._resolve_course("UCK358E"), COURSE)
        self.app.list_courses.return_value = {"courses": [lab]}
        with self.assertRaisesRegex(NinovaError, "not found"):
            self.app._resolve_course("UCK 358E")
        self.app.list_courses.return_value = {"courses": [COURSE, {**COURSE, "url": BASE + "/Sinif/1.3"}]}
        with self.assertRaisesRegex(NinovaError, "Ambiguous"):
            self.app._resolve_course("UCK358E")

    def test_title_lookup_is_not_changed_by_code_normalization(self):
        self.assertEqual(self.app._resolve_course("machine learning"), COURSE)


class DashboardEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.app = NinovaMcpApp.__new__(NinovaMcpApp)
        self.app._course_cache = TtlCache(60)
        self.app._course_cache_ttl_seconds = 60
        self.app._client = Mock(base_url=BASE)

    def test_compact_default_does_not_truncate_internal_enrollment(self):
        html = '<h1>Dersler</h1>' + "".join(f'<a href="/Sinif/1.{n}">UCK {100+n}E</a>' for n in range(25))
        self.app._client.get_html.return_value = (html, SimpleNamespace(url=BASE + "/Kampus1"))
        with patch.dict(os.environ, {"NINOVA_COMPACT_DEFAULT": "1"}):
            result = self.app.list_courses(refresh=True)
        self.assertEqual(result["count"], 25)
        self.assertTrue(all("url" in c for c in result["courses"]))
        self.assertEqual(result["enrollment_coverage"]["status"], "unknown")

    def test_unknown_dashboard_does_not_overwrite_good_cached_courses(self):
        self.app._course_cache.set("courses", [COURSE])
        self.app._client.get_html.return_value = ("<h1>Unexpected page</h1>", SimpleNamespace(url=BASE + "/Kampus1"))
        live = self.app.list_courses(refresh=True)
        self.assertIn("parse_warning", live)
        self.assertEqual(self.app._course_cache.get("courses"), [COURSE])


if __name__ == "__main__":
    unittest.main()
