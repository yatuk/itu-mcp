"""Regression cases found by an independent review of tracking completeness."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

import test_tracking_completeness as fixtures

COURSE, ROOT, snapshot = fixtures.COURSE, fixtures.ROOT, fixtures.snapshot
from ninova_mcp.tracking_coverage import page_coverage


class TrackingReviewTests(unittest.TestCase):
    # Reuse only fixture setup/helpers, without rerunning the inherited suite.
    setUp = fixtures.TrackingCompletenessTests.setUp
    seed = fixtures.TrackingCompletenessTests.seed
    stored = fixtures.TrackingCompletenessTests.stored
    def test_same_path_folder_redirect_cannot_erase_previously_seen_child(self):
        self.app.sync_all_courses()
        before = self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"]["class_files"]
        original = self.app._client.get_html
        def redirected(url):
            if url == ROOT + "?folder=1":
                return self.app._client.pages[ROOT], SimpleNamespace(url=ROOT)
            return original(url)
        self.app._client.get_html = redirected
        result = self.app.sync_all_courses()
        self.assertFalse(any(item["action"] == "removed" for item in result["updates"]))
        self.assertEqual(result["courses"][0]["coverage"]["class_files"]["status"], "partial")
        self.assertTrue(any(error.get("error") == "unexpected_resource_query" for error in result["errors"]))
        self.assertEqual(self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"]["class_files"], before)

    def test_one_good_assignment_does_not_certify_an_unparsed_second_row(self):
        before = snapshot()
        before["overview"]["assignments"] = [
            {"title": "Assignment A", "url": COURSE["url"] + "/Odev/1"},
            {"title": "Assignment B", "url": COURSE["url"] + "/Odev/2"},
        ]
        self.seed(before)
        self.app._client.pages[COURSE["url"] + "/Odevler"] = (
            '<table id="gvOdevListesi"><tr><td><h2><a href="' + COURSE["url"] + '/Odev/1">Assignment A</a></h2></td></tr>'
            '<tr><td><h3><a href="' + COURSE["url"] + '/Odev/2">Assignment B</a></h3></td></tr></table>'
        )
        result = self.app.sync_all_courses(include_files=False)
        self.assertFalse(any(item["action"] == "removed" for item in result["updates"]))
        self.assertEqual(result["courses"][0]["coverage"]["assignments"]["status"], "partial")
        self.assertEqual(self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"]["assignments"], before["overview"]["assignments"])
        self.assertTrue(any(error.get("error") == "unparsed_assignment_rows" for error in result["errors"]))

    def test_unsupported_card_in_same_row_is_also_incomplete(self):
        before = snapshot()
        before["overview"]["assignments"] = [{"title": "Assignment B", "url": COURSE["url"] + "/Odev/2"}]
        self.seed(before)
        self.app._client.pages[COURSE["url"] + "/Odevler"] = (
            '<table id="gvOdevListesi"><tr><td><h2><a href="' + COURSE["url"] + '/Odev/1">Assignment A</a></h2></td>'
            '<td><h3><a href="' + COURSE["url"] + '/Odev/2">Assignment B</a></h3></td></tr></table>'
        )
        result = self.app.sync_all_courses(include_files=False)
        self.assertFalse(any(item["action"] == "removed" for item in result["updates"]))
        self.assertTrue(any(error.get("error") == "unparsed_assignment_links" for error in result["errors"]))


    def test_complete_assignment_inventory_still_reports_real_removal_once(self):
        def assignments(ids):
            return '<table id="gvOdevListesi">' + ''.join('<tr><td><h2><a href="' + COURSE["url"] + f'/Odev/{number}">Assignment {number}</a></h2></td></tr>' for number in ids) + '</table>'
        path = COURSE["url"] + "/Odevler"
        self.app._client.pages[path] = assignments([1, 2])
        self.app.sync_all_courses(include_files=False)
        self.app._client.pages[path] = assignments([1])
        result = self.app.sync_all_courses(include_files=False)
        removed = [item for item in result["updates"] if item["entity_type"] == "assignments" and item["action"] == "removed"]
        self.assertEqual([item["before"]["title"] for item in removed], ["Assignment 2"])
        self.assertEqual(self.app.sync_all_courses(include_files=False)["updates"], [])

    def test_missing_remote_container_cannot_remove_its_previous_sessions(self):
        before = snapshot()
        before["overview"]["remote_learning"]["past_sessions"] = [{"title": "Recorded session", "text": "Recorded session"}]
        self.seed(before)
        self.app._client.pages[COURSE["url"] + "/UzaktanEgitim"] = '<h2>Aktif Uzaktan Eğitim Oturumlarınız</h2><table><tr><th>Başlık</th></tr><tr><td>Current session</td></tr></table>'
        result = self.app.sync_all_courses(include_files=False)
        self.assertFalse(any(item["entity_type"] == "past_remote_sessions" and item["action"] == "removed" for item in result["updates"]))
        self.assertEqual(self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"]["remote_learning"], before["overview"]["remote_learning"])
        self.assertEqual(result["courses"][0]["coverage"]["remote_learning"]["status"], "unknown")

    def test_malformed_grade_and_topic_rows_cannot_remove_last_known_items(self):
        for scope, path, header, key in (("grades", "/Notlar", "Not", "grades"), ("message_board", "/MesajPanosu", "Mesaj Başlığı</th><th>Son Mesaj", "topics")):
            with self.subTest(scope=scope):
                before = snapshot()
                before["overview"][scope][key] = [{"title": "Earlier item"}]
                self.seed(before)
                self.app._client.pages[COURSE["url"] + path] = '<table><tr><th>' + header + '</th></tr><tr><td>Valid item</td><td>20</td></tr><tr><td>Earlier item</td></tr></table>'
                result = self.app.sync_all_courses(include_files=False)
                self.assertEqual(result["courses"][0]["coverage"][scope]["status"], "partial")
                self.assertEqual(self.stored()["courses"][COURSE["url"]]["snapshot"]["overview"][scope], before["overview"][scope])


class ResourceIdentityTests(unittest.TestCase):
    def test_query_order_and_fragment_do_not_change_resource_identity(self):
        self.assertEqual(page_coverage("<p>Read</p>", ROOT + "?mode=a&folder=1#top", ROOT + "?folder=1&mode=a")["status"], "complete")

    def test_bare_folder_key_and_origin_are_part_of_identity(self):
        self.assertEqual(page_coverage("<p>Read</p>", ROOT, ROOT + "?g123")["status"], "failed")
        self.assertEqual(page_coverage("<p>Read</p>", ROOT.replace("ninova.itu.edu.tr", "other.example"), ROOT)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
