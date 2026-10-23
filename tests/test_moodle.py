"""Moodle client and pool course site client, with no network access."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ninova_mcp import moodle_client, pool_client
from ninova_mcp.client import NinovaCredentials
from ninova_mcp.moodle_client import MoodleAuthError, MoodleClient, MoodleError, parse_course_label
from ninova_mcp.pool_client import PoolSiteClient, PoolSiteError, normalize_pool_code

LOGIN_PAGE = '<form id="login"><input name="logintoken" value="tok123"></form>'
HOME_PAGE = '<script>M.cfg = {"sesskey":"abcDEF123456"};</script>'

COURSE_PAGE = """
<h1>FIZ 102E Physics II</h1>
<ul>
<li class="section" data-sectionname="General">
  <ul>
    <li class="activity modtype_forum"><a href="https://moodle.itu.edu.tr/mod/forum/view.php?id=11">
      <span class="instancename">Announcements <span class="accesshide">Forum</span></span></a></li>
    <li class="activity modtype_resource"><a href="/mod/resource/view.php?id=22">
      <span class="instancename">Guide</span></a></li>
    <li class="activity modtype_url"><a href="https://evil.example.com/mod/url/view.php?id=33">
      <span class="instancename">Offsite</span></a></li>
  </ul>
</li>
</ul>
"""

FORUM_PAGE = """
<table><tr class="discussion"><td><a href="https://moodle.itu.edu.tr/mod/forum/discuss.php?d=5">Attendance</a></td></tr></table>
"""

DISCUSS_PAGE = """
<article class="forum-post-container"><header>
<h3 data-region-content="forum-post-core-subject">Attendance</h3>
by <a href="https://moodle.itu.edu.tr/user/view.php?id=1&amp;course=2">Ada Hoca</a> -
<time datetime="2026-10-21T13:00:20+03:00">Wednesday</time></header>
<div class="post-content-container"><p>Bring your ID card.</p>
<a href="https://moodle.itu.edu.tr/pluginfile.php/1/mod_forum/attachment/1/list.pdf">list.pdf</a>
<a href="https://evil.example.com/pluginfile.php/x">bad</a></div></article>
"""


class FakeResponse:
    def __init__(self, text: str = "", url: str = "", status: int = 200, payload=None, content: bytes = b"") -> None:
        self.text = text
        self.url = url
        self.status_code = status
        self._payload = payload
        self._content = content
        self.headers = {"Content-Type": "application/pdf"}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def iter_content(self, chunk_size: int = 0):
        yield self._content


class FakeMoodle:
    """Routes the client's requests to canned pages and records them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.login_ok = True
        self.expire_next_ajax = False
        self.ajax_payload = [{"error": False, "data": {"courses": []}}]

    def __call__(self, session, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if "/login/index.php" in url and method == "GET":
            return FakeResponse(LOGIN_PAGE, url)
        if "/login/index.php" in url and method == "POST":
            if self.login_ok:
                return FakeResponse(HOME_PAGE, "https://moodle.itu.edu.tr/my/")
            return FakeResponse('<div class="alert-danger">Invalid login</div>', url)
        if "/lib/ajax/service.php" in url:
            if self.expire_next_ajax:
                self.expire_next_ajax = False
                return FakeResponse(payload=[{"error": True, "exception": {"errorcode": "servicerequireslogin"}}])
            return FakeResponse(payload=self.ajax_payload)
        if "/course/view.php" in url:
            return FakeResponse(COURSE_PAGE, url)
        if "/mod/forum/view.php" in url:
            return FakeResponse(FORUM_PAGE, url)
        if "/mod/forum/discuss.php" in url:
            return FakeResponse(DISCUSS_PAGE, url)
        if "/pluginfile.php" in url:
            return FakeResponse(url=url, content=b"hello")
        return FakeResponse("", url, 404)


def make_client(fake: FakeMoodle) -> MoodleClient:
    creds = NinovaCredentials(username="student@itu.edu.tr", password="secret-pass")
    with patch.object(moodle_client, "request_with_safe_redirects", fake):
        client = MoodleClient(credentials=creds)
    client._min_request_interval = 0
    return client


class MoodleClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeMoodle()
        patcher = patch.object(moodle_client, "request_with_safe_redirects", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = make_client(self.fake)

    def test_login_sends_plain_username_and_token_to_moodle_only(self) -> None:
        self.client.login()
        post = next(c for c in self.fake.calls if c[0] == "POST")
        self.assertTrue(post[1].startswith("https://moodle.itu.edu.tr/"))
        self.assertEqual(post[2]["data"]["username"], "student")
        self.assertEqual(post[2]["data"]["logintoken"], "tok123")
        self.assertEqual(self.client._sesskey, "abcDEF123456")
        self.assertEqual(self.client.auth_status(), {"authenticated": True, "host": "moodle.itu.edu.tr"})

    def test_failed_login_reports_moodle_message_without_password(self) -> None:
        self.fake.login_ok = False
        with self.assertRaises(MoodleAuthError) as ctx:
            self.client.login()
        self.assertIn("Invalid login", str(ctx.exception))
        self.assertNotIn("secret-pass", str(ctx.exception))
        status = self.client.auth_status()
        self.assertFalse(status["authenticated"])

    def test_request_to_another_host_is_refused(self) -> None:
        with self.assertRaises(MoodleError):
            moodle_client._validate_url("https://ninova.itu.edu.tr/")
        with self.assertRaises(MoodleError):
            moodle_client._validate_url("http://moodle.itu.edu.tr/")

    def test_parse_course_label(self) -> None:
        label = parse_course_label("CEN 322E Computer Architecture (CRN: 15570)", "202710-15570-CEN-322E")
        self.assertEqual(label, {"title": "CEN 322E Computer Architecture", "crn": "15570", "term_code": "202710"})

    def test_list_courses_and_cache(self) -> None:
        self.fake.ajax_payload = [{"error": False, "data": {"courses": [
            {"id": 7, "fullname": "MAT 210E Engineering Mathematics (CRN: 15431)",
             "shortname": "202710-15431-MAT-210E", "startdate": 1, "enddate": 2, "courseimage": "data:..."},
        ]}}]
        result = self.client.list_courses()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["courses"][0]["crn"], "15431")
        self.assertNotIn("courseimage", result["courses"][0])
        before = len(self.fake.calls)
        self.client.list_courses()
        self.assertEqual(len(self.fake.calls), before)

    def test_invalid_classification_and_course_id(self) -> None:
        with self.assertRaises(MoodleError):
            self.client.list_courses("bogus")
        with self.assertRaises(MoodleError):
            self.client.get_course("1; DROP")

    def test_expired_session_logs_in_once_more(self) -> None:
        self.client.login()
        self.fake.expire_next_ajax = True
        self.client.list_courses("past")
        logins = [c for c in self.fake.calls if c[0] == "POST" and "/login/index.php" in c[1]]
        self.assertEqual(len(logins), 2)

    def test_get_course_lists_only_moodle_activities(self) -> None:
        course = self.client.get_course("18302")
        names = [(a["type"], a["name"]) for s in course["sections"] for a in s["activities"]]
        self.assertEqual(names, [("forum", "Announcements"), ("resource", "Guide")])
        self.assertTrue(course["untrusted_external_content"])

    def test_announcements_parse_post_and_keep_moodle_attachments_only(self) -> None:
        result = self.client.get_announcements("18302", limit=3)
        post = result["announcements"][0]
        self.assertEqual(post["subject"], "Attendance")
        self.assertEqual(post["author"], "Ada Hoca")
        self.assertEqual(post["posted"], "2026-10-21T13:00:20+03:00")
        self.assertIn("Bring your ID card.", post["body"])
        self.assertEqual([a["name"] for a in post["attachments"]], ["list.pdf"])

    def test_upcoming_events_shape(self) -> None:
        self.fake.ajax_payload = [{"error": False, "data": {"events": [
            {"name": "Quiz 1", "timesort": 100, "modulename": "quiz", "url": "https://moodle.itu.edu.tr/mod/quiz/view.php?id=1",
             "course": {"fullname": "FIZ 102E"}},
        ]}}]
        result = self.client.get_upcoming_events(days=7)
        self.assertEqual(result["events"][0]["name"], "Quiz 1")
        self.assertEqual(result["events"][0]["course"], "FIZ 102E")
        body = json.dumps(self.fake.calls[-1][2]["json"])
        self.assertIn("timesortto", body)

    def test_download_resource_only_reads_moodle_files(self) -> None:
        data, url, _ctype, name = self.client.download_resource(
            "https://moodle.itu.edu.tr/pluginfile.php/1/mod_resource/content/1/My%20Guide.pdf"
        )
        self.assertEqual(data, b"hello")
        self.assertEqual(name, "My Guide.pdf")
        with self.assertRaises(MoodleError):
            self.client.download_resource("https://moodle.itu.edu.tr/user/profile.php?id=1")
        with self.assertRaises(MoodleError):
            self.client.download_resource("https://evil.example.com/pluginfile.php/1/a.pdf")


POOL_HOME = """
<a href="/docs/syllabus/FIZ102E.pdf">FIZ102E - Physics II</a>
<a href="/docs/syllabus/FIZ101.pdf">FIZ101 - Fizik I</a>
<a href="https://fefhavuz.itu.edu.tr/files/resources/guide.pdf">Guide</a>
<a href="https://www.youtube.com/watch?v=1">video</a>
<a href="/login.php">Giris</a>
"""

MAT_HOME = """
<a href="/mat/catalog.php?code=MAT103">MAT103/E</a>
<a href="/mat/catalog.php?code=MAT104">MAT104/E</a>
<a href="/files/optical_form.pdf">optik form</a>
"""


class PoolSiteTests(unittest.TestCase):
    def fake_get(self, session, method, url, **kwargs):
        pages = {"fizikhavuz.itu.edu.tr": POOL_HOME, "mathavuz.itu.edu.tr": MAT_HOME, "kimyahavuz.itu.edu.tr": ""}
        host = url.split("/")[2]
        response = FakeResponse(pages.get(host, "page text"), url)
        response.encoding = "utf-8"
        response.apparent_encoding = "utf-8"
        return response

    def setUp(self) -> None:
        patcher = patch.object(pool_client, "request_with_safe_redirects", self.fake_get)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = PoolSiteClient()

    def test_normalize_code(self) -> None:
        self.assertEqual(normalize_pool_code("fiz 102e"), ("FIZ", "FIZ102E"))
        self.assertEqual(normalize_pool_code("MAT103"), ("MAT", "MAT103"))
        with self.assertRaises(PoolSiteError):
            normalize_pool_code("CEN 322E")

    def test_list_documents_for_course_puts_titled_syllabus_first(self) -> None:
        result = self.client.list_documents("FIZ 102E")
        self.assertEqual(result["sites"], ["fizikhavuz.itu.edu.tr"])
        urls = [d["url"] for d in result["documents"]]
        self.assertEqual(urls[0], "https://fizikhavuz.itu.edu.tr/docs/syllabus/FIZ102E.pdf")
        self.assertIn("https://fefhavuz.itu.edu.tr/files/resources/guide.pdf", urls)
        self.assertFalse(any("youtube" in u or "login" in u for u in urls))

    def test_math_course_page_is_filtered_by_code(self) -> None:
        result = self.client.list_documents("MAT 103")
        kinds = [(d["kind"], d["title"]) for d in result["documents"]]
        self.assertIn(("course_page", "MAT103/E"), kinds)
        self.assertNotIn(("course_page", "MAT104/E"), kinds)

    def test_without_code_reads_all_three_sites(self) -> None:
        result = self.client.list_documents()
        self.assertEqual(len(result["sites"]), 3)

    def test_read_document_refuses_other_hosts(self) -> None:
        with self.assertRaises(PoolSiteError):
            self.client.read_document("https://evil.example.com/a.pdf")
        with self.assertRaises(PoolSiteError):
            self.client.read_document("http://fizikhavuz.itu.edu.tr/a.pdf")

    def test_read_document_extracts_text(self) -> None:
        with patch.object(pool_client, "extract_text_from_bytes", return_value={"ok": True, "text": "syllabus"}) as ex:
            result = self.client.read_document("https://fizikhavuz.itu.edu.tr/docs/syllabus/FIZ102E.pdf", max_chars=2000)
        self.assertEqual(result["text"], "syllabus")
        self.assertEqual(ex.call_args.kwargs["extension"], ".pdf")
        self.assertTrue(result["untrusted_external_content"])


class MoodleToolWiringTests(unittest.TestCase):
    def test_tools_are_registered_and_read_resource_stays_local(self) -> None:
        from ninova_mcp.server import LOCAL_TOOL_NAMES, REMOTE_EXCLUDED_TOOLS

        for name in ("moodle_list_courses", "moodle_get_course", "moodle_get_announcements",
                     "moodle_get_upcoming_events", "moodle_read_resource",
                     "pool_list_documents", "pool_read_document"):
            self.assertIn(name, LOCAL_TOOL_NAMES)
        self.assertIn("moodle_read_resource", REMOTE_EXCLUDED_TOOLS)

    def test_read_resource_uses_filename_extension(self) -> None:
        from ninova_mcp.moodle_tools import MoodleToolsMixin

        app = MoodleToolsMixin()
        app.moodle = SimpleNamespace(
            download_resource=lambda url: (b"x", url, "application/pdf", "guide.pdf")
        )
        with patch("ninova_mcp.moodle_tools.extract_text_from_bytes", return_value={"ok": True, "text": "t"}) as ex:
            result = app.moodle_read_resource("https://moodle.itu.edu.tr/pluginfile.php/1/a/guide.pdf")
        self.assertEqual(ex.call_args.kwargs["extension"], ".pdf")
        self.assertEqual(result["filename"], "guide.pdf")


if __name__ == "__main__":
    unittest.main()
