from __future__ import annotations

import os
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests

from ninova_mcp.library_catalog import CATALOG_URL, record_url
from ninova_mcp.library_client import LibraryClient, LibraryError, _library_proxy_url


PROXY = "http://127.0.0.1:18888"
ONE_RESULT = (CATALOG_URL + "search/detailnonmodal/"
              "ent:$002f$002fSD_ILS$002f0$002fSD_ILS:69360/one"
              "?qu=9780070682856&rt=false%7C%7C%7CISBN%7C%7C%7CISBN")


class RecordingAdapter(requests.adapters.BaseAdapter):
    """Keep requests' actual preparation, session merging and cookie handling."""

    def __init__(self, replies: list[dict | Exception] | None = None) -> None:
        self.replies = list(replies or [{}])
        self.sent: list[tuple[requests.PreparedRequest, dict]] = []

    def send(self, request: requests.PreparedRequest, **kwargs) -> requests.Response:
        self.sent.append((request, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        result = requests.Response()
        result.request = request
        result.url = request.url
        result.status_code = reply.get("status", 200)
        result.headers.update(reply.get("headers", {}))
        name = reply.get("fixture", "empty")
        result._content = (Path(__file__).parent / "fixtures" / f"library_sirsi_{name}.html").read_bytes()
        result._content_consumed = True
        result.encoding = "utf-8"
        message = Message()
        for key, value in result.headers.items():
            message[key] = value
        result.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=message))
        return result

    def close(self) -> None:
        pass


def make_client(replies=None, *, proxy=PROXY, session=None) -> tuple[LibraryClient, RecordingAdapter]:
    session = session or requests.Session()
    adapter = RecordingAdapter(replies)
    session.mount("https://", adapter)
    env = {} if proxy is None else {"NINOVA_LIBRARY_PROXY_URL": proxy}
    with patch.dict(os.environ, env, clear=True):
        client = LibraryClient(session=session)
    client._min_request_interval = 0
    return client, adapter


class LibraryProxyConfigurationTests(unittest.TestCase):
    def test_only_explicit_loopback_http_endpoints_are_accepted(self) -> None:
        for raw, expected in ((None, None), ("", None), (PROXY, PROXY),
                              (PROXY + "/", PROXY),
                              ("http://[::1]:18888", "http://[::1]:18888"),
                              ("http://127.0.0.1:65535", "http://127.0.0.1:65535")):
            with self.subTest(raw=raw):
                self.assertEqual(_library_proxy_url(raw), expected)

    def test_invalid_proxy_configuration_fails_without_echoing_it(self) -> None:
        invalid = (
            "https://127.0.0.1:18888", "socks5://127.0.0.1:18888", "127.0.0.1:18888",
            "http://localhost:18888", "http://127.0.0.2:18888", "http://2130706433:18888",
            "http://127.0.0.1.example:18888", "http://192.0.2.1:18888",
            "http://[::ffff:127.0.0.1]:18888", "http://[::1", "http://%31%32%37.0.0.1:18888",
            "http://127.0.0.1", "http://127.0.0.1:80", "http://127.0.0.1:65536",
            "http://127.0.0.1:port", "http://127.0.0.1:18888/path",
            PROXY + "?token=private-value", PROXY + "?", PROXY + "#",
            "http://private-value@127.0.0.1:18888", "http://@127.0.0.1:18888",
            "http://127.0.0.1:18888\n", " http://127.0.0.1:18888",
        )
        for raw in invalid:
            with self.subTest(raw=raw), patch.dict(os.environ, {"NINOVA_LIBRARY_PROXY_URL": raw}, clear=True):
                with self.assertRaisesRegex(LibraryError, "NINOVA_LIBRARY_PROXY_URL") as raised:
                    LibraryClient()
                self.assertNotIn("private-value", str(raised.exception))

    def test_legacy_client_does_not_consume_or_apply_the_new_proxy_setting(self) -> None:
        session = requests.Session()
        session.auth = ("legacy-user", "legacy-password")
        session.cookies.set("legacy-session", "legacy-value")
        session.proxies = {"https": "http://legacy-proxy.example:8080"}
        with patch.dict(os.environ, {"NINOVA_LIBRARY_PROXY_URL": "invalid-setting"}, clear=True):
            client = LibraryClient(base_url="https://divit.library.itu.edu.tr", session=session)
        self.assertIs(client.session, session)
        self.assertTrue(session.trust_env)
        self.assertEqual(session.auth, ("legacy-user", "legacy-password"))
        self.assertEqual(session.cookies.get("legacy-session"), "legacy-value")
        self.assertEqual(session.proxies, {"https": "http://legacy-proxy.example:8080"})


class LibraryAnonymousTransportTests(unittest.TestCase):
    def test_prepared_requests_exclude_inherited_and_environment_identity(self) -> None:
        for proxy in (None, PROXY):
            with self.subTest(proxy=proxy):
                session = requests.Session()
                session.headers.update({"Authorization": "Bearer inherited", "Cookie": "account=private",
                                        "Proxy-Authorization": "Basic private", "X-Private": "private"})
                session.params = {"patronId": "private", "t:formdata": "private"}
                session.auth = ("inherited-user", "inherited-password")
                session.cert = "/private/inherited-client.pem"
                session.verify = False
                session.proxies = {"https": "http://inherited-proxy.example:8080"}
                session.cookies.set("account-cookie", "private", domain="katalog.kutuphane.itu.edu.tr")
                old_hook = Mock(side_effect=AssertionError("Inherited hook executed"))
                session.hooks = {"response": [old_hook]}
                client, adapter = make_client(proxy=proxy, session=session)
                ambient = {"HTTP_PROXY": "http://ambient.example:8080", "HTTPS_PROXY": "http://ambient.example:8080",
                           "ALL_PROXY": "http://ambient.example:8080", "NO_PROXY": "*",
                           "REQUESTS_CA_BUNDLE": "/private/ambient-ca.pem", "CURL_CA_BUNDLE": "/private/ambient-ca.pem"}
                with patch.dict(os.environ, ambient, clear=True), patch(
                    "requests.sessions.get_netrc_auth", side_effect=AssertionError("Read .netrc")
                ), patch.object(client, "_credentials", side_effect=AssertionError("Read account credentials")):
                    result = client.search("ısı transferi", search_type="title", limit=3)
                self.assertEqual(result["count"], 0)
                self.assertEqual(len(adapter.sent), 1)
                prepared, options = adapter.sent[0]
                self.assertEqual(prepared.method, "GET")
                self.assertIsNone(prepared.body)
                self.assertFalse({"authorization", "cookie", "proxy-authorization", "x-private"} &
                                 {name.lower() for name in prepared.headers})
                self.assertEqual(parse_qs(urlsplit(prepared.url).query), {
                    "qu": ["ısı transferi"], "ps": ["3"], "rw": ["0"], "rt": ["false|||TITLE|||Başlık"]})
                self.assertEqual(options["proxies"], {"https": proxy} if proxy else {})
                self.assertIs(options["verify"], True)
                self.assertIsNone(options["cert"])
                old_hook.assert_not_called()

    def test_verified_single_result_redirect_retains_only_new_anonymous_cookie(self) -> None:
        client, adapter = make_client([
            {"status": 302, "headers": {"Location": ONE_RESULT,
             "Set-Cookie": "JSESSIONID=synthetic-anonymous; Path=/client/tr_TR/default; Secure; HttpOnly"}},
            {"fixture": "detail"},
        ])
        result = client.search("9780070682856", search_type="isbn")
        self.assertEqual(result["records"][0]["record_id"], "SD_ILS:69360")
        self.assertEqual(len(adapter.sent), 2)
        self.assertNotIn("Cookie", adapter.sent[0][0].headers)
        self.assertEqual(adapter.sent[1][0].headers["Cookie"], "JSESSIONID=synthetic-anonymous")
        self.assertEqual(adapter.sent[1][0].url, ONE_RESULT)
        for prepared, options in adapter.sent:
            self.assertEqual(prepared.method, "GET")
            self.assertIsNone(prepared.body)
            self.assertEqual(options["proxies"], {"https": PROXY})
            self.assertIs(options["verify"], True)

    def test_search_then_canonical_detail_share_only_public_transport(self) -> None:
        client, adapter = make_client([{"fixture": "search"}, {"fixture": "detail"}])
        result = client.search("thermodynamics", search_type="title", limit=3)
        item = client.get_item(result["records"][0]["record_id"])
        self.assertEqual(item["record_id"], "SD_ILS:69360")
        self.assertEqual(adapter.sent[1][0].url, record_url("SD_ILS:69360"))
        self.assertEqual(len(adapter.sent), 2)

    def test_proxy_failure_does_not_retry_a_direct_or_ambient_route(self) -> None:
        client, adapter = make_client([requests.exceptions.ProxyError("synthetic tunnel failure")])
        with self.assertRaisesRegex(LibraryError, "tunnel failure"):
            client.search("thermodynamics")
        self.assertEqual(len(adapter.sent), 1)
        self.assertEqual(adapter.sent[0][1]["proxies"], {"https": PROXY})


class LibraryPublicRequestBoundaryTests(unittest.TestCase):
    def test_non_get_and_request_bodies_never_reach_transport(self) -> None:
        for method, data in (("POST", None), ("PUT", None), ("HEAD", None), ("GET", {}), ("GET", {"token": "value"})):
            with self.subTest(method=method, data=data):
                client, adapter = make_client()
                with self.assertRaisesRegex(LibraryError, "GET requests without a body"):
                    client._request(method, "search/results", data=data)
                self.assertEqual(adapter.sent, [])

    def test_unverified_destinations_are_rejected_before_initial_or_redirect_request(self) -> None:
        blocked = (
            "http://katalog.kutuphane.itu.edu.tr/client/tr_TR/default/search/results",
            "https://katalog.kutuphane.itu.edu.tr:8443/client/tr_TR/default/search/results",
            "https://@katalog.kutuphane.itu.edu.tr/client/tr_TR/default/search/results",
            "https://katalog.kutuphane.itu.edu.tr.attacker.example/", "https://127.0.0.1/",
            CATALOG_URL + "login", CATALOG_URL + "patroninfo", CATALOG_URL + "ajax",
            CATALOG_URL + "search/results;jsessionid=secret",
            CATALOG_URL + "search/%2e%2e/login", CATALOG_URL + "search/results%2f..%2flogin",
            CATALOG_URL + "search/results%3baction=renew", CATALOG_URL + "search/%252e%252e/login",
            CATALOG_URL + "search/detailnonmodal.detail:lookuptitleinfo",
            CATALOG_URL + "search/detailnonmodal/ent:$002f$002fSD_ILS$002f0$002fSD_ILS:1/one:renew",
            CATALOG_URL + "search/results#account", CATALOG_URL + "search/results?t:ac=renew",
            CATALOG_URL + "search/results?t%3Aformdata=private", CATALOG_URL + "search/results?patronId=1",
            CATALOG_URL + "search/results?qu=one&%71u=two", CATALOG_URL + "search/results?qu=one&qu=two",
            CATALOG_URL + "search/results?qu=one&rt=renew", CATALOG_URL + "search/results?ps=0",
            CATALOG_URL + "search/results?rw=10001", CATALOG_URL + "search/results?qu=%0d%0aCookie:secret",
            CATALOG_URL + "search/detailnonmodal?d=ent://SD_ILS/0/SD_ILS:1~ILS~0:renew",
            CATALOG_URL + "search/results?qu=" + "x" * 257,
        )
        for url in blocked:
            for redirect in (False, True):
                with self.subTest(url=url, redirect=redirect):
                    client, adapter = make_client([{"status": 302, "headers": {"Location": url}}])
                    with self.assertRaises(LibraryError):
                        if redirect:
                            client.search("thermodynamics")
                        else:
                            client._request("GET", url)
                    self.assertEqual(len(adapter.sent), int(redirect))

    def test_prepared_query_is_validated_before_sending(self) -> None:
        for params in ({"t:formdata": "private"}, {"ps": 51}, {"d": "ent://SD_ILS/0/SD_ILS:1~ILS~0"}):
            with self.subTest(params=params):
                client, adapter = make_client()
                with self.assertRaises(LibraryError):
                    client._request("GET", "search/results", params=params)
                self.assertEqual(adapter.sent, [])
        client, adapter = make_client()
        with self.assertRaises(LibraryError):
            client._request("GET", "search/results?qu=first", params={"qu": "second"})
        self.assertEqual(adapter.sent, [])

    def test_public_redirect_chain_is_bounded_to_three_hops(self) -> None:
        reply = {"status": 302, "headers": {"Location": CATALOG_URL + "search/results?qu=loop"}}
        client, adapter = make_client([reply] * 5)
        with self.assertRaisesRegex(LibraryError, "Exceeded 3 redirects"):
            client.search("thermodynamics")
        self.assertEqual(len(adapter.sent), 4)  # Initial request and three redirected requests.


if __name__ == "__main__":
    unittest.main()
