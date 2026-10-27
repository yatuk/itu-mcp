"""Opt-in ProQuest search through OpenAthens, with canned pages and no network."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ninova_mcp import proquest_client
from ninova_mcp.proquest_client import ProQuestClient, ProQuestError, itu_link

IDP_URL = "https://login.openathens.net/auth/itu.edu.tr/o/82346286"
LOGIN_PAGE = (
    '<form method="post"><input name="username" type="text"><input name="password" type="password">'
    '<input type="hidden" name="t" value="tok"><input type="hidden" name="formtoken" value="ft"></form>'
)
SAML_PAGE = (
    '<form action="https://shibboleth-sp.prod.proquest.com/Shibboleth.sso/SAML2/POST" method="post">'
    '<input type="hidden" name="SAMLResponse" value="PHNhbWw+"><input type="hidden" name="RelayState" value="rs"></form>'
)
SEARCH_PAGE = (
    '<a href="/index?accountid=11638">x</a>'
    '<form id="searchForm" action="https://www.proquest.com/index.sourcetypessearch.expandedbasicsearchbox.searchform">'
    '<input type="hidden" name="_csrf" value="c1"><input type="hidden" name="t:formdata" value="fd">'
    '<textarea name="searchTerm"></textarea><input type="checkbox" name="fullTextLimit">'
    '<input type="checkbox" name="peerReviewLimit"></form>'
)
RESULTS_PAGE = """
<h1>2,448 results</h1>
<ul>
<li class="resultItem">
 <div class="source-type-label">Scholarly Journal</div>
 <h3><a href="https://www.proquest.com/pqrl/docview/2229605787/7A98PQ/2?accountid=11638">Causes and consequences of sleepiness</a></h3>
 <span class="truncatedAuthor">Hershner, Shelley D; Chervin, Ronald D; vs.</span>
 <span class="jnlArticle">Nature and Science of Sleep ; Macclesfield Vol. 6, (2014): 73-84.</span>
 <a href="https://www.proquest.com/pqrl/docview/2229605787/fulltext/7A98PQ/2">Full text</a>
 <a href="https://www.proquest.com/pqrl/docview/2229605787/fulltextPDF/7A98PQ/2">Full text - PDF</a>
 Times cited 50 on ProQuest
</li>
<li class="resultItem"><h3><a href="#">No docview link</a></h3></li>
</ul>
"""


class Resp:
    def __init__(self, text: str, url: str) -> None:
        self.text = text
        self.url = url
        self.status_code = 200


class FakeFlow:
    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, session, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if "Shibboleth.sso/DS" in url:
            return Resp(LOGIN_PAGE, IDP_URL)
        if method == "POST" and "openathens" in url:
            return Resp(SAML_PAGE if self.accept else '<div class="alert">Invalid credentials</div>', url)
        if "SAML2/POST" in url:
            return Resp("", "https://www.proquest.com/index")
        if url.endswith("/index") and method == "GET":
            return Resp(SEARCH_PAGE, url)
        if "searchform" in url:
            return Resp(RESULTS_PAGE, "https://www.proquest.com/pqrl/results/X/1?accountid=11638")
        return Resp("", url)


class ProQuestTests(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {
            "NINOVA_PROQUEST_ENABLE": "1", "NINOVA_LIBRARY_ID": "820000001", "NINOVA_LIBRARY_PIN": "lib-secret",
        })
        env.start()
        self.addCleanup(env.stop)
        sleep = patch.object(proquest_client.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def client_with(self, fake) -> ProQuestClient:
        patcher = patch.object(proquest_client, "request_with_safe_redirects", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return ProQuestClient()

    def test_disabled_by_default_sends_nothing(self) -> None:
        fake = FakeFlow()
        client = self.client_with(fake)
        with patch.dict(os.environ, {"NINOVA_PROQUEST_ENABLE": ""}):
            with self.assertRaisesRegex(ProQuestError, "NINOVA_PROQUEST_ENABLE"):
                client.search("sleep")
        self.assertEqual(fake.calls, [])

    def test_password_goes_only_to_openathens_and_saml_only_to_proquest(self) -> None:
        fake = FakeFlow()
        self.client_with(fake).search("sleep")
        for method, url, kwargs in fake.calls:
            data = kwargs.get("data")
            if data is None:
                continue
            flat = dict(data)
            if "password" in flat:
                self.assertTrue(url.startswith("https://login.openathens.net/"))
                self.assertEqual(kwargs["body_hosts"], {"login.openathens.net"})
                self.assertEqual(flat["username"], "820000001")
                self.assertEqual(flat["formtoken"], "ft")
            elif "SAMLResponse" in flat:
                self.assertEqual(kwargs["body_hosts"], {"shibboleth-sp.prod.proquest.com"})
            else:
                self.assertEqual(kwargs["body_hosts"], {"www.proquest.com"})
                self.assertNotIn("lib-secret", str(data))

    def test_search_results_are_parsed(self) -> None:
        result = self.client_with(FakeFlow()).search("sleep", peer_reviewed_only=True, from_year=2020, to_year=2024)
        self.assertEqual(result["query"], "(sleep) AND pd(2020-2024)")
        self.assertEqual(result["total_count"], 2448)
        self.assertEqual(result["count"], 1)
        article = result["articles"][0]
        self.assertEqual(article["authors"], ["Hershner, Shelley D", "Chervin, Ronald D"])
        self.assertEqual(article["year"], 2014)
        self.assertEqual(article["source_type"], "Scholarly Journal")
        self.assertTrue(article["has_pdf"])
        self.assertEqual(article["times_cited"], 50)
        self.assertEqual(article["itu_url"], itu_link("2229605787"))
        self.assertTrue(result["untrusted_external_content"])

    def test_filters_reach_the_form(self) -> None:
        fake = FakeFlow()
        self.client_with(fake).search("sleep", full_text_only=False, peer_reviewed_only=True)
        data = dict(next(c[2]["data"] for c in fake.calls if "searchform" in c[1]))
        self.assertEqual(data["searchTerm"], "sleep")
        self.assertNotIn("fullTextLimit", data)
        self.assertEqual(data["peerReviewLimit"], "on")
        self.assertEqual(data["_csrf"], "c1")

    def test_failed_sign_in_reports_without_the_password(self) -> None:
        client = self.client_with(FakeFlow(accept=False))
        with self.assertRaises(ProQuestError) as ctx:
            client.search("sleep")
        self.assertIn("Invalid credentials", str(ctx.exception))
        self.assertNotIn("lib-secret", str(ctx.exception))

    def test_daily_limit(self) -> None:
        client = self.client_with(FakeFlow())
        with patch.dict(os.environ, {"NINOVA_PROQUEST_DAILY_SEARCHES": "2"}):
            client.search("sleep")
            client.search("memory")
            with self.assertRaisesRegex(ProQuestError, "daily"):
                client.search("dreams")

    def test_saml_response_to_another_host_is_refused(self) -> None:
        class Hijacked(FakeFlow):
            def __call__(self, session, method, url, **kwargs):
                resp = super().__call__(session, method, url, **kwargs)
                resp.text = resp.text.replace("shibboleth-sp.prod.proquest.com", "evil.example.com")
                return resp

        client = self.client_with(Hijacked())
        with self.assertRaisesRegex(ProQuestError, "unexpected host"):
            client.search("sleep")

    def test_repeated_search_is_served_from_cache(self) -> None:
        fake = FakeFlow()
        client = self.client_with(fake)
        first = client.search("sleep", peer_reviewed_only=True)
        sent = len(fake.calls)
        second = client.search("sleep", peer_reviewed_only=True)
        self.assertEqual(len(fake.calls), sent)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["searches_left_today"], first["searches_left_today"])
        client.search("sleep", peer_reviewed_only=False)
        self.assertGreater(len(fake.calls), sent)

    def test_cache_still_requires_the_opt_in(self) -> None:
        client = self.client_with(FakeFlow())
        client.search("sleep")
        with patch.dict(os.environ, {"NINOVA_PROQUEST_ENABLE": ""}), self.assertRaises(ProQuestError):
            client.search("sleep")

    def test_empty_result_page_is_not_cached(self) -> None:
        class Empty(FakeFlow):
            def __call__(self, session, method, url, **kwargs):
                resp = super().__call__(session, method, url, **kwargs)
                if "searchform" in url:
                    resp.text = "<h1>0 results</h1>"
                return resp

        fake = Empty()
        client = self.client_with(fake)
        client.search("zzzz")
        sent = len(fake.calls)
        client.search("zzzz")
        self.assertGreater(len(fake.calls), sent)

    def test_only_search_submissions_are_spaced_out(self) -> None:
        client = self.client_with(FakeFlow())
        with patch.object(proquest_client.time, "sleep") as sleep,                 patch.object(proquest_client.time, "monotonic", return_value=100.0):
            client.search("sleep")
            self.assertEqual(sleep.call_count, 0)
            client.search("memory")
            self.assertEqual(sleep.call_count, 1)
            self.assertAlmostEqual(sleep.call_args.args[0], proquest_client.MIN_INTERVAL_SECONDS)

    def test_password_is_not_sent_to_another_openathens_tenant(self) -> None:
        class OtherTenant(FakeFlow):
            def __call__(self, session, method, url, **kwargs):
                resp = super().__call__(session, method, url, **kwargs)
                resp.url = resp.url.replace("/auth/itu.edu.tr/", "/auth/other.example/")
                return resp

        fake = OtherTenant()
        client = self.client_with(fake)
        with self.assertRaisesRegex(ProQuestError, "did not open"):
            client.search("sleep")
        self.assertFalse(any("password" in dict(c[2].get("data") or {}) for c in fake.calls))

    def test_proquest_is_not_offered_on_the_remote_transport(self) -> None:
        from ninova_mcp.tool_registry import REMOTE_TOOL_NAMES

        self.assertNotIn("proquest_search", REMOTE_TOOL_NAMES)

    def test_only_expected_hosts(self) -> None:
        with self.assertRaises(ProQuestError):
            proquest_client._validate_url("https://evil.example.com/")
        proquest_client._validate_url("https://www.proquest.com/index")


if __name__ == "__main__":
    unittest.main()
