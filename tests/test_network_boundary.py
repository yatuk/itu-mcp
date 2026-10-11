"""Which hosts the server can be made to contact, checked without any network.

Every request goes to a recording adapter. A test that says a URL is refused
asserts that the recorder saw no request for the foreign host at all, because
a check that fires after the request has left is too late.
"""

from __future__ import annotations

import os
import sys
import types
import unittest
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import requests

from ninova_mcp.archive_client import ItuArchiveClient, ItuArchiveError
from ninova_mcp.client import NinovaAuthError, NinovaError
from ninova_mcp.http_security import (
    UnsafeUrlError,
    request_target,
    request_with_safe_redirects,
    require_https_host,
)
from ninova_mcp.operator_settings import OperatorSettingError, extra_sso_hosts
from ninova_mcp.parsing import normalize_url
from ninova_mcp.public_client import ItuPublicClient, ItuPublicError

from recording import HTML_OK, RecordingAdapter, offline_app, redirect

NINOVA = "https://ninova.itu.edu.tr"
ATTACKER = "attacker.example"

# (label, URL). Each one names a foreign host to the HTTP library, or is text
# that URL parsers read differently, and must be refused before anything is sent.
TRICK_URLS = [
    ("backslash-at", "https://attacker.example\\@ninova.itu.edu.tr/x"),
    ("backslash-dot", "https://attacker.example\\.ninova.itu.edu.tr/x"),
    ("backslash-path", "https://ninova.itu.edu.tr\\@attacker.example/x"),
    ("tab-in-host", "https://attacker.example\t@ninova.itu.edu.tr/x"),
    ("tab-in-path", "https://ninova.itu.edu.tr/a\tb"),
    ("newline", "https://ninova.itu.edu.tr/a\nHost: attacker.example"),
    ("carriage-return", "https://ninova.itu.edu.tr/a\rb"),
    ("space", "https://ninova.itu.edu.tr/a b"),
    ("leading-space", " https://ninova.itu.edu.tr/a"),
    ("nul", "https://ninova.itu.edu.tr/a\x00b"),
    ("delete-char", "https://ninova.itu.edu.tr/a\x7fb"),
    ("userinfo", "https://user@ninova.itu.edu.tr/x"),
    ("userinfo-password", "https://user:pass@ninova.itu.edu.tr/x"),
    ("userinfo-foreign", "https://ninova.itu.edu.tr@attacker.example/x"),
    ("trailing-dot", "https://ninova.itu.edu.tr./x"),
    ("lookalike-suffix", "https://ninova.itu.edu.tr.attacker.example/x"),
    ("lookalike-prefix", "https://attacker-ninova.itu.edu.tr/x"),
    ("port", "https://ninova.itu.edu.tr:8443/x"),
    ("http", "http://ninova.itu.edu.tr/x"),
    ("ftp", "ftp://ninova.itu.edu.tr/x"),
    ("scheme-relative", "//attacker.example/x"),
    ("foreign", "https://attacker.example/Sinif/1.2"),
]

# Hosts under itu.edu.tr that the old suffix match let a tool argument reach.
OTHER_ITU_URLS = [
    "https://yardim.itu.edu.tr/itubilet.aspx?id=123456",
    "https://obs.itu.edu.tr/ogrenci/",
    "https://portal.itu.edu.tr/apps/default/",
    "https://girisv3.itu.edu.tr/Login.aspx",
    "https://x.itu.edu.tr/Sinif/1.2",
    "https://itu.edu.tr/Sinif/1.2",
]


class ParserDifferentialTests(unittest.TestCase):
    def test_the_two_parsers_really_disagree(self) -> None:
        """The premise of the finding, so the tests below cannot pass by accident."""
        url = "https://attacker.example\\@ninova.itu.edu.tr/x"
        self.assertEqual(urlparse(url).hostname, "ninova.itu.edu.tr")
        adapter = RecordingAdapter()
        session = requests.Session()
        session.mount("https://", adapter)
        session.get(url, allow_redirects=False)
        self.assertEqual(adapter.hosts, [ATTACKER])

    def test_request_target_refuses_each_trick(self) -> None:
        for label, url in TRICK_URLS:
            with self.subTest(label):
                with self.assertRaises(UnsafeUrlError):
                    require_https_host(url, {"ninova.itu.edu.tr"})

    def test_host_is_compared_as_the_http_library_sends_it(self) -> None:
        target = require_https_host("HTTPS://NiNoVa.ITU.edu.TR/Kampus1", {"ninova.itu.edu.tr"})
        self.assertEqual((target.scheme, target.host, target.port), ("https", "ninova.itu.edu.tr", None))
        self.assertEqual(target.url, "https://ninova.itu.edu.tr/Kampus1")
        self.assertEqual(require_https_host("https://ninova.itu.edu.tr:443/x", {"ninova.itu.edu.tr"}).port, 443)
        # An internationalised name is judged by the ASCII form that is sent.
        self.assertEqual(request_target("https://bücher.example/").host, "xn--bcher-kva.example")
        with self.assertRaises(UnsafeUrlError):
            require_https_host("https://nınova.itu.edu.tr/x", {"ninova.itu.edu.tr"})

    def test_normalize_url_refuses_unsafe_text(self) -> None:
        self.assertEqual(normalize_url("/Kampus1", NINOVA), NINOVA + "/Kampus1")
        self.assertEqual(normalize_url("Sinif/1.2?a=b@c", NINOVA), NINOVA + "/Sinif/1.2?a=b@c")
        for value in (
            "https://attacker.example\\@ninova.itu.edu.tr/x", "/a\\b", "/a b", "/a\tb", "/a\nb",
            "https://user@ninova.itu.edu.tr/x", "\\\\attacker.example\\share",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_url(value, NINOVA)


class SafeRedirectTests(unittest.TestCase):
    @staticmethod
    def only_safe(url: str) -> None:
        require_https_host(url, {"safe.example"})

    def session(self, script: dict[str, Any]) -> tuple[requests.Session, RecordingAdapter]:
        adapter = RecordingAdapter(script)
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session, adapter

    def test_initial_url_tricks_send_nothing(self) -> None:
        for label, url in TRICK_URLS:
            with self.subTest(label):
                session, adapter = self.session({})
                with self.assertRaises(ValueError):
                    request_with_safe_redirects(
                        session, "GET", url.replace("ninova.itu.edu.tr", "safe.example"),
                        validate_url=self.only_safe,
                    )
                self.assertEqual(adapter.hosts, [])

    def test_redirect_location_tricks_are_not_followed(self) -> None:
        locations = [
            "https://attacker.example\\@safe.example/x",
            "https://attacker.example\\.safe.example/x",
            "//attacker.example\\@safe.example/x",
            "/\\attacker.example/x",
            "\\\\attacker.example/x",
            "https://attacker.example\t@safe.example/x",
            "https://safe.example/a\nb",
            "https://user@safe.example/x",
            "https://user:pass@safe.example/x",
            "https://safe.example@attacker.example/x",
            "https://safe.example./x",
            "https://safe.example:8443/x",
            "http://safe.example/x",
            "https://attacker.example/x",
            "https://safe .example/x",
        ]
        for location in locations:
            with self.subTest(location=location):
                session, adapter = self.session({"https://safe.example/start": redirect(location)})
                with self.assertRaises(ValueError):
                    request_with_safe_redirects(
                        session, "GET", "https://safe.example/start", validate_url=self.only_safe,
                    )
                self.assertEqual(adapter.hosts, ["safe.example"])

    def test_mixed_case_redirect_and_space_in_path_are_followed_to_the_same_host(self) -> None:
        session, adapter = self.session({
            "https://safe.example/start": redirect("HTTPS://SAFE.Example/a b?q=c d"),
        })
        response = request_with_safe_redirects(
            session, "GET", "https://safe.example/start", validate_url=self.only_safe,
        )
        self.assertEqual(adapter.hosts, ["safe.example", "safe.example"])
        self.assertEqual(response.url, "https://safe.example/a%20b?q=c%20d")

    def test_validator_sees_the_url_that_is_sent_including_the_query(self) -> None:
        seen: list[str] = []
        session, adapter = self.session({})
        request_with_safe_redirects(
            session, "GET", "https://safe.example/search", validate_url=seen.append,
            params={"q": "a b", "n": 2},
        )
        self.assertEqual(adapter.urls, ["https://safe.example/search?q=a+b&n=2"])
        self.assertEqual(seen[0], adapter.urls[0])

    def test_body_is_not_replayed_to_another_host(self) -> None:
        def both(url: str) -> None:
            require_https_host(url, {"safe.example", "other.example"})

        session, adapter = self.session({
            "https://safe.example/login": redirect("https://other.example/collect", status=307),
        })
        with self.assertRaises(UnsafeUrlError):
            request_with_safe_redirects(
                session, "POST", "https://safe.example/login", validate_url=both,
                data={"password": "secret"}, body_hosts={"safe.example"},
            )
        self.assertEqual(adapter.hosts, ["safe.example"])
        # A 302 turns the POST into a GET without a body, which may cross hosts.
        session, adapter = self.session({
            "https://safe.example/login": redirect("https://other.example/home"),
        })
        request_with_safe_redirects(
            session, "POST", "https://safe.example/login", validate_url=both,
            data={"password": "secret"}, body_hosts={"safe.example"},
        )
        self.assertEqual([item.method for item in adapter.requests], ["POST", "GET"])
        self.assertIsNone(adapter.requests[1].body)


class ToolUrlTests(unittest.TestCase):
    """Tool arguments never select the host."""

    def tools(self, app: Any) -> dict[str, Any]:
        return {
            "read_page": lambda url: app.read_page(url),
            "download_resource": lambda url: app.download_resource(url),
            "read_resource_text": lambda url: app.read_resource_text(url=url),
            "snapshot_page": lambda url: app.snapshot_page(url),
            "diff_snapshot": lambda url: app.diff_snapshot(url),
            "crawl_course": lambda url: app.crawl_course(url, max_pages=1),
            "get_assignment_upload_slots": lambda url: app.get_assignment_upload_slots(upload_url=url),
            "submit_assignment": lambda url: app.submit_assignment(
                file_path=__file__, upload_url=url, slot_index=1, confirm=True,
            ),
        }

    def assert_refused_without_a_request(self, urls: list[tuple[str, str]]) -> None:
        app, adapter = offline_app(self)
        for name, tool in self.tools(app).items():
            for label, url in urls:
                with self.subTest(tool=name, url=label):
                    with self.assertRaises(NinovaError):
                        tool(url)
                    self.assertEqual(adapter.hosts, [])

    def test_every_url_tool_refuses_parser_tricks_without_sending(self) -> None:
        self.assert_refused_without_a_request(TRICK_URLS)

    def test_every_url_tool_refuses_other_itu_hosts_without_sending(self) -> None:
        self.assert_refused_without_a_request([(url, url) for url in OTHER_ITU_URLS])

    def test_ninova_urls_and_paths_still_work(self) -> None:
        app, adapter = offline_app(self)
        for url in ("/Kampus1", NINOVA + "/Kampus1", "HTTPS://NINOVA.ITU.EDU.TR/Kampus1", "Kampus1"):
            with self.subTest(url=url):
                result = app.read_page(url)
                self.assertEqual(result["url"], NINOVA + "/Kampus1")
        self.assertEqual(set(adapter.hosts), {"ninova.itu.edu.tr"})

    def test_redirect_from_ninova_to_another_host_is_not_followed(self) -> None:
        targets = [
            "https://attacker.example\\@obs.itu.edu.tr/x",
            "https://attacker.example/x",
            "https://yardim.itu.edu.tr/itubilet.aspx?id=1",
            "https://obs.itu.edu.tr/ogrenci/",
            "https://portal.itu.edu.tr/apps/default/",
            "https://x.itu.edu.tr/",
        ]
        for target in targets:
            with self.subTest(target=target):
                app, adapter = offline_app(self, {NINOVA + "/a": redirect(target)})
                with self.assertRaises(NinovaError):
                    app.read_page("/a")
                self.assertEqual(adapter.hosts, ["ninova.itu.edu.tr"])

    def test_crawl_requires_the_ninova_host_and_stays_on_it(self) -> None:
        page = (
            b'<html><body><a href="/Sinif/1.2/Duyurular">a</a>'
            b'<a href="https://x.itu.edu.tr/Sinif/1.2/Odevler">b</a>'
            b'<a href="https://attacker.example/Sinif/1.2/Notlar">c</a></body></html>'
        )
        html = (200, {"Content-Type": "text/html"}, page)
        app, adapter = offline_app(self, {NINOVA + "/Sinif/1.2": html})
        result = app.crawl_course("/Sinif/1.2", max_pages=5)
        self.assertEqual(result["pages_crawled"], 2)
        self.assertEqual(set(adapter.hosts), {"ninova.itu.edu.tr"})

    def test_page_read_that_ends_on_the_login_host_is_never_returned(self) -> None:
        """A dead session: the login page is detected and a re-login is attempted."""
        app, adapter = offline_app(self, {NINOVA + "/a": redirect("https://girisv3.itu.edu.tr/Login.aspx")})
        with patch.object(app.client, "login", side_effect=NinovaAuthError("login needed")) as login:
            with self.assertRaises(NinovaAuthError):
                app.read_page("/a")
        login.assert_called_once_with(force=True)
        self.assertEqual(adapter.hosts, ["ninova.itu.edu.tr", "girisv3.itu.edu.tr"])


class InternalHostTests(unittest.TestCase):
    """The other İTÜ hosts stay reachable, but only from fixed internal code paths."""

    def test_internal_host_set_is_exact(self) -> None:
        app, _ = offline_app(self)
        self.assertEqual(app.client._internal_hosts(), frozenset({
            "ninova.itu.edu.tr", "girisv3.itu.edu.tr", "portal.itu.edu.tr",
            "obs.itu.edu.tr", "yardim.itu.edu.tr",
        }))
        self.assertEqual(app.client._ninova_flow_hosts(), frozenset({"ninova.itu.edu.tr", "girisv3.itu.edu.tr"}))
        for url in (
            "https://itu.edu.tr/", "https://www.itu.edu.tr/", "https://sis.itu.edu.tr/",
            "https://uicc.itu.edu.tr/", "https://x.obs.itu.edu.tr/", "https://obs.itu.edu.tr:8443/",
            "http://obs.itu.edu.tr/", "https://attacker.example\\@obs.itu.edu.tr/",
        ):
            with self.subTest(url=url):
                with self.assertRaises(NinovaError):
                    app.client._check_domain(url)

    def test_portal_path_still_works(self) -> None:
        payload = (200, {"Content-Type": "application/json"}, b'{"d": {"Balance": "12,50"}}')
        app, adapter = offline_app(self, {
            "https://portal.itu.edu.tr/apps/default/service/service.aspx/GetBalance": payload,
        })
        self.assertEqual(app._get_portal_json("GetBalance"), {"Balance": "12,50"})
        self.assertEqual(adapter.hosts, ["portal.itu.edu.tr", "portal.itu.edu.tr"])

    def test_portal_sign_on_round_trip_is_allowed(self) -> None:
        app, adapter = offline_app(self, {
            "https://portal.itu.edu.tr/apps/default/": [
                redirect("https://girisv3.itu.edu.tr/Login.aspx?ReturnUrl=portal"), HTML_OK,
            ],
            "https://girisv3.itu.edu.tr/Login.aspx?ReturnUrl=portal": redirect("https://portal.itu.edu.tr/apps/default/"),
        })
        html, url = app.client.get_portal_html("/apps/default/")
        self.assertEqual(url, "https://portal.itu.edu.tr/apps/default/")
        self.assertEqual(adapter.hosts, ["portal.itu.edu.tr", "girisv3.itu.edu.tr", "portal.itu.edu.tr"])

    def test_portal_redirect_to_an_unlisted_host_is_refused(self) -> None:
        app, adapter = offline_app(self, {
            "https://portal.itu.edu.tr/apps/default/": redirect("https://x.itu.edu.tr/"),
        })
        with self.assertRaises(NinovaError):
            app.client.get_portal_html("/apps/default/")
        self.assertEqual(adapter.hosts, ["portal.itu.edu.tr"])
        with self.assertRaises(NinovaError):
            app.client.get_portal_html("https://yardim.itu.edu.tr/")
        self.assertEqual(adapter.hosts, ["portal.itu.edu.tr"])

    def test_help_desk_is_reachable_only_through_its_own_method(self) -> None:
        app, adapter = offline_app(self)
        url = "https://yardim.itu.edu.tr/itubilet.aspx?id=123456"
        html, response = app.client.get_help_desk_html(url)
        self.assertEqual(response.url, url)
        self.assertEqual(adapter.hosts, ["yardim.itu.edu.tr"])
        for other in ("https://obs.itu.edu.tr/x", NINOVA + "/x", "https://attacker.example\\@yardim.itu.edu.tr/x"):
            with self.subTest(other=other):
                with self.assertRaises(NinovaError):
                    app.client.get_help_desk_html(other)
        self.assertEqual(adapter.hosts, ["yardim.itu.edu.tr"])

    def test_obs_requests_still_pass_through_the_shared_session(self) -> None:
        app, adapter = offline_app(self)
        response = app.client._safe_request("GET", "https://obs.itu.edu.tr/ogrenci/", timeout=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(adapter.hosts, ["obs.itu.edu.tr"])

    def test_operator_can_add_an_exact_sign_on_host(self) -> None:
        app, adapter = offline_app(self, {
            NINOVA + "/a": redirect("https://giris.itu.edu.tr/hop"),
            "https://giris.itu.edu.tr/hop": redirect(NINOVA + "/b"),
        }, NINOVA_EXTRA_SSO_HOSTS="giris.itu.edu.tr")
        self.assertEqual(app.read_page("/a")["url"], NINOVA + "/b")
        self.assertEqual(adapter.hosts, ["ninova.itu.edu.tr", "giris.itu.edu.tr", "ninova.itu.edu.tr"])
        # It is a hop only: a tool argument still cannot name it.
        with self.assertRaises(NinovaError):
            app.read_page("https://giris.itu.edu.tr/hop")
        self.assertEqual(len(adapter.hosts), 3)

    def test_extra_sign_on_hosts_must_be_exact_itu_hosts(self) -> None:
        for value in ("attacker.example", "*.itu.edu.tr", ".itu.edu.tr", "itu.edu.tr", "https://a.itu.edu.tr", "a.itu.edu.tr/x"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"NINOVA_EXTRA_SSO_HOSTS": value}):
                    with self.assertRaises(OperatorSettingError):
                        extra_sso_hosts()
        with patch.dict(os.environ, {"NINOVA_EXTRA_SSO_HOSTS": " Giris.ITU.edu.tr , sso2.itu.edu.tr,"}):
            self.assertEqual(extra_sso_hosts(), frozenset({"giris.itu.edu.tr", "sso2.itu.edu.tr"}))


LOGIN_FORM = (
    b'<html><body><form method="post" action="./Login.aspx">'
    b'<input type="hidden" name="__VIEWSTATE" value="vs">'
    b'<input name="ctl00$ContentPlaceHolder1$tbUserName" id="ContentPlaceHolder1_tbUserName">'
    b'<input name="ctl00$ContentPlaceHolder1$tbPassword" id="ContentPlaceHolder1_tbPassword">'
    b"</form></body></html>"
)
LOGIN_PAGE = (200, {"Content-Type": "text/html"}, LOGIN_FORM)
ENTRY = NINOVA + "/Login.aspx?ReturnUrl=%2Fmembers%2Fogrenci.default.aspx"
SSO = "https://girisv3.itu.edu.tr/Login.aspx?ReturnUrl=ninova"


class LoginPinningTests(unittest.TestCase):
    def client(self, script: dict[str, Any]) -> tuple[Any, RecordingAdapter]:
        app, adapter = offline_app(self, script)
        client = app.client

        def build() -> requests.Session:
            session = requests.Session()
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            return session

        self.enterContext(patch.object(client, "_build_session", side_effect=build))
        return client, adapter

    def bodies_with_password(self, adapter: RecordingAdapter) -> list[str]:
        return [
            str(item.url) for item in adapter.requests
            if item.body and "dummy-password" in (item.body if isinstance(item.body, str) else item.body.decode())
        ]

    def test_normal_flow_posts_the_password_to_the_sign_on_host_only(self) -> None:
        def sign_on(request: requests.PreparedRequest) -> Any:
            return redirect(NINOVA + "/Kampus1") if request.method == "POST" else LOGIN_PAGE

        client, adapter = self.client({ENTRY: redirect(SSO), SSO: sign_on})
        info = client.login(force=True)
        self.assertEqual(info["login_method"], "requests")
        self.assertEqual(
            [(item.method, host) for item, host in zip(adapter.requests, adapter.hosts)],
            [("GET", "ninova.itu.edu.tr"), ("GET", "girisv3.itu.edu.tr"), ("POST", "girisv3.itu.edu.tr"),
             ("GET", "ninova.itu.edu.tr"), ("GET", "ninova.itu.edu.tr")],
        )
        self.assertEqual(self.bodies_with_password(adapter), [SSO])

    def test_login_form_on_any_other_host_gets_no_password(self) -> None:
        client, adapter = self.client({ENTRY: LOGIN_PAGE})
        with self.assertRaisesRegex(NinovaAuthError, "password was not sent"):
            client.login(force=True)
        self.assertEqual([item.method for item in adapter.requests], ["GET"])
        self.assertEqual(self.bodies_with_password(adapter), [])

    def test_login_form_on_the_sign_on_host_but_another_page_gets_no_password(self) -> None:
        other = "https://girisv3.itu.edu.tr/Other.aspx"
        client, adapter = self.client({ENTRY: redirect(other), other: LOGIN_PAGE})
        with self.assertRaisesRegex(NinovaAuthError, "password was not sent"):
            client.login(force=True)
        self.assertEqual(self.bodies_with_password(adapter), [])

    def test_sign_on_redirect_cannot_replay_the_password_elsewhere(self) -> None:
        for target in (NINOVA + "/collect", "https://portal.itu.edu.tr/collect", "https://attacker.example/collect"):
            with self.subTest(target=target):
                def sign_on(request: requests.PreparedRequest, target: str = target) -> Any:
                    return redirect(target, status=307) if request.method == "POST" else LOGIN_PAGE

                client, adapter = self.client({ENTRY: redirect(SSO), SSO: sign_on})
                with self.assertRaises(NinovaError):
                    client.login(force=True)
                self.assertEqual(self.bodies_with_password(adapter), [SSO])
                self.assertNotIn("attacker.example", adapter.hosts)
                self.assertNotIn("portal.itu.edu.tr", adapter.hosts)

    def fake_playwright(self, page_url: str) -> tuple[MagicMock, Any]:
        page = MagicMock()
        page.url = page_url
        context = MagicMock()
        context.new_page.return_value = page
        context.cookies.return_value = []
        browser = MagicMock()
        browser.new_context.return_value = context
        playwright = MagicMock()
        playwright.chromium.launch.return_value = browser
        manager = MagicMock()
        manager.__enter__.return_value = playwright
        module = types.ModuleType("playwright.sync_api")
        module.sync_playwright = lambda: manager  # type: ignore[attr-defined]
        module.TimeoutError = TimeoutError  # type: ignore[attr-defined]
        return page, module

    def test_browser_fallback_does_not_type_the_password_on_another_host(self) -> None:
        for page_url in (
            NINOVA + "/Login.aspx", "https://attacker.example/Login.aspx",
            "https://girisv3.itu.edu.tr.attacker.example/Login.aspx", "https://girisv3.itu.edu.tr/Other.aspx",
            "http://girisv3.itu.edu.tr/Login.aspx",
        ):
            with self.subTest(page_url=page_url):
                client, _ = self.client({})
                page, module = self.fake_playwright(page_url)
                with patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"), "playwright.sync_api": module}):
                    with self.assertRaisesRegex(NinovaAuthError, "password was not sent"):
                        client._login_with_playwright()
                page.goto.assert_called_once()
                page.locator.assert_not_called()

    def test_browser_fallback_types_on_the_sign_on_page(self) -> None:
        client, _ = self.client({})
        page, module = self.fake_playwright("https://girisv3.itu.edu.tr/Login.aspx?ReturnUrl=x")
        with patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"), "playwright.sync_api": module}):
            info = client._login_with_playwright()
        self.assertEqual(info["login_method"], "playwright")
        page.locator.assert_any_call("#ContentPlaceHolder1_tbPassword")


class OtherClientTests(unittest.TestCase):
    def test_public_client_validates_what_is_sent(self) -> None:
        adapter = RecordingAdapter()
        session = requests.Session()
        session.mount("https://", adapter)
        with patch.dict(os.environ, {"NINOVA_REQUEST_DELAY_MS": "0"}):
            client = ItuPublicClient(session=session)
        for url in (
            "https://attacker.example\\@obs.itu.edu.tr/x", "https://attacker.example\\.sks.itu.edu.tr/x",
            "https://user@obs.itu.edu.tr/x", "https://obs.itu.edu.tr./x", "https://obs.itu.edu.tr/a\tb",
            "http://obs.itu.edu.tr/x", "https://ninova.itu.edu.tr/x",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ItuPublicError):
                    client._validate_url(url)
                with self.assertRaises(ItuPublicError):
                    client._request("GET", url)
        self.assertEqual(adapter.hosts, [])
        client._request("GET", "https://OBS.itu.edu.tr/public/x", params={"a": "b c"})
        self.assertEqual(adapter.urls, ["https://obs.itu.edu.tr/public/x?a=b+c"])

    def test_archive_client_validates_what_is_sent(self) -> None:
        adapter = RecordingAdapter({
            "https://itu-ders.com/data/index.json": redirect("https://attacker.example\\@itu-ders.com/x"),
        })
        session = requests.Session()
        session.mount("https://", adapter)
        with patch.dict(os.environ, {"NINOVA_REQUEST_DELAY_MS": "0"}):
            client = ItuArchiveClient(session=session)
            with self.assertRaises(ItuArchiveError):
                client.get_index()
            self.assertEqual(adapter.hosts, ["itu-ders.com"])
            for base in ("https://attacker.example\\@itu-ders.com/data", "https://user@itu-ders.com/data", "http://itu-ders.com/data"):
                with self.subTest(base=base):
                    with self.assertRaises(ItuArchiveError):
                        ItuArchiveClient(session=session, base_url=base)

    def test_archive_getters_refuse_unsafe_path_segments(self) -> None:
        adapter = RecordingAdapter()
        session = requests.Session()
        session.mount("https://", adapter)
        with patch.dict(os.environ, {"NINOVA_REQUEST_DELAY_MS": "0"}):
            client = ItuArchiveClient(session=session)
        bad_terms = ["../../secret", "2025-2026-guz/../x", "2025-2026-guz?x=1", "2025-2026-kis", "", "%2e%2e"]
        bad_branches = ["../BLG", "BLG/..", "B", "BLGXX", "BL%47", "BLG?x", "B.G"]
        bad_letters = ["../a", "a/b", "a.json", "%2e", "a?x", "", "a b", "x" * 17]
        calls = (
            [lambda value=value: client.get_quota(value) for value in bad_terms]
            + [lambda value=value: client.get_term_meta(value) for value in bad_terms]
            + [lambda value=value: client.get_term_branch(value, "BLG") for value in bad_terms]
            + [lambda value=value: client.get_term_branch("2025-2026-guz", value) for value in bad_branches]
            + [lambda value=value: client.get_course_history(value) for value in bad_branches]
            + [lambda value=value: client.get_instructor_history(value) for value in bad_letters]
        )
        for index, attempt in enumerate(calls):
            with self.subTest(index=index):
                with self.assertRaises(ItuArchiveError):
                    attempt()
        self.assertEqual(adapter.urls, [])
        json_reply = (200, {"Content-Type": "application/json"}, b"{}")
        adapter.script.update({
            "https://itu-ders.com/data/quota/2025-2026-guz.json": json_reply,
            "https://itu-ders.com/data/history/instructors/%C3%A7.json": json_reply,
            "https://itu-ders.com/data/history/courses/BLG.json": json_reply,
        })
        client.get_quota("2025-2026-GUZ")
        client.get_instructor_history("Ç")
        client.get_course_history("blg")
        self.assertEqual(adapter.urls, [
            "https://itu-ders.com/data/quota/2025-2026-guz.json",
            "https://itu-ders.com/data/history/instructors/%C3%A7.json",
            "https://itu-ders.com/data/history/courses/BLG.json",
        ])


if __name__ == "__main__":
    unittest.main()
