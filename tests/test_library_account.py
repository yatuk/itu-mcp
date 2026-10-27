"""Sirsi patron account reader, with canned pages and no network access."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ninova_mcp import library_account
from ninova_mcp.library_account import (
    LibraryAccountClient,
    LibraryAccountError,
    record_id_from_href,
)

ROOT = "https://katalog.kutuphane.itu.edu.tr/client/tr_TR/default"
LOGIN_PAGE = (
    '<form id="loginPageForm" action="/client/tr_TR/default/index.template.patronloginform.'
    'loginpageform/true?dt=list&amp;sdcsrf=abc" method="post">'
    '<input type="hidden" name="sdcsrf" value="abc"><input name="hidden" type="hidden" value="SYMWS2">'
    '<input id="j_username" name="j_username" type="text"><input id="j_password" name="j_password" type="password">'
    '<input name="submit_0" type="submit" value="Giris"></form>'
)
ACCOUNT_PAGE = """
<div id="accountMain"><div id="accountTabs">
<div id="accountSummary">Statünüz: Aktif
 <div id="checkoutsSummaryZone">Ödünç Alınanlar Toplam: 2</div>
 <div id="holdsSummaryZone">Ayırtılan Materyaller Toplam: 1</div>
 <div id="finesSummaryZone">Toplam Tutar: 3,50 TL</div></div>
<div id="checkoutsTab"><table>
 <tr><td><a href="/client/tr_TR/default/search/detailnonmodal/ent:$002f$002fSD_ILS$002f0$002fSD_ILS:111/one">Algorithms</a></td><td>Son tarih 12.11.2026</td></tr>
 <tr><td><a href="/client/tr_TR/default/search/detailnonmodal/ent:$002f$002fSD_ILS$002f0$002fSD_ILS:222/one">Calculus</a></td><td>Son tarih 20.11.2026</td></tr>
</table></div>
<div id="holdsTab"><div class="holdItem"><a href="/client/tr_TR/default/search/detailnonmodal/ent:$002f$002fSD_ILS$002f0$002fSD_ILS:333/one">Physics</a></div></div>
<div id="finesTab"></div></div></div>
"""


class Resp:
    def __init__(self, text: str, url: str) -> None:
        self.text = text
        self.url = url
        self.status_code = 200


class FakeCatalog:
    def __init__(self, accept: bool = True, account_page: str = ACCOUNT_PAGE) -> None:
        self.accept = accept
        self.account_page = account_page
        self.calls: list[tuple[str, str, dict]] = []
        self.signed_in = False

    def __call__(self, session, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "POST":
            self.signed_in = self.accept
            return Resp("", url)
        if "search/account" in url:
            return Resp(self.account_page if self.signed_in else LOGIN_PAGE, f"{ROOT}/")
        return Resp("", url)


class LibraryAccountTests(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {"NINOVA_LIBRARY_ID": "820000001", "NINOVA_LIBRARY_PIN": "pin-secret"})
        env.start()
        self.addCleanup(env.stop)

    def client_with(self, fake) -> LibraryAccountClient:
        patcher = patch.object(library_account, "request_with_safe_redirects", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return LibraryAccountClient()

    def test_sign_in_posts_only_to_the_catalog_login_form(self) -> None:
        fake = FakeCatalog()
        self.client_with(fake).get_account()
        post = next(c for c in fake.calls if c[0] == "POST")
        self.assertIn("patronloginform.loginpageform", post[1])
        self.assertTrue(post[1].startswith(ROOT))
        self.assertEqual(post[2]["data"]["j_username"], "820000001")
        self.assertEqual(post[2]["data"]["j_password"], "pin-secret")
        self.assertEqual(post[2]["data"]["sdcsrf"], "abc")
        self.assertEqual(post[2]["data"]["hidden"], "SYMWS2")

    def test_account_summary_loans_and_holds(self) -> None:
        account = self.client_with(FakeCatalog()).get_account()
        self.assertEqual(account["status"], "Aktif")
        self.assertEqual(account["loan_count"], 2)
        self.assertEqual([i["record_id"] for i in account["loans"]], ["SD_ILS:111", "SD_ILS:222"])
        self.assertEqual(account["loans"][0]["dates"], ["12.11.2026"])
        self.assertEqual(account["hold_count"], 1)
        self.assertEqual(account["holds"][0]["title"], "Physics")
        self.assertEqual(account["fines_total"], "3,50 TL")
        self.assertNotIn("parse_warning", account)
        self.assertTrue(account["untrusted_external_content"])

    def test_list_loans_is_a_subset(self) -> None:
        loans = self.client_with(FakeCatalog()).list_loans()
        self.assertEqual(loans["loan_count"], 2)
        self.assertNotIn("holds", loans)

    def test_wrong_credentials_report_failure_without_the_pin(self) -> None:
        client = self.client_with(FakeCatalog(accept=False))
        with self.assertRaises(LibraryAccountError) as ctx:
            client.get_account()
        self.assertNotIn("pin-secret", str(ctx.exception))

    def test_missing_credentials_send_nothing(self) -> None:
        fake = FakeCatalog()
        client = self.client_with(fake)
        with patch.dict(os.environ, {"NINOVA_LIBRARY_PIN": ""}), self.assertRaises(LibraryAccountError):
            client.get_account()
        self.assertEqual(fake.calls, [])

    def test_login_form_pointing_elsewhere_is_refused(self) -> None:
        calls: list[str] = []

        def swapped(session, method, url, **kwargs):
            calls.append(method)
            return Resp(LOGIN_PAGE.replace("patronloginform.loginpageform", "other.form"), f"{ROOT}/")

        client = self.client_with(swapped)
        with self.assertRaises(LibraryAccountError):
            client.get_account()
        self.assertNotIn("POST", calls)

    def test_row_count_mismatch_is_flagged(self) -> None:
        fake = FakeCatalog(account_page=ACCOUNT_PAGE.replace("Toplam: 2", "Toplam: 5"))
        account = self.client_with(fake).get_account()
        self.assertIn("Fewer loan rows", account["parse_warning"])

    def test_account_page_is_fetched_once_after_sign_in(self) -> None:
        fake = FakeCatalog()
        client = self.client_with(fake)
        client.get_account()
        gets = [c for c in fake.calls if c[0] == "GET"]
        self.assertEqual(len(gets), 2)  # login form, then the signed-in page that is reused
        client.get_account()
        self.assertEqual(len([c for c in fake.calls if c[0] == "GET"]), 3)

    def test_url_validation_and_record_ids(self) -> None:
        with self.assertRaises(LibraryAccountError):
            library_account._validate_url("https://evil.example.com/client/tr_TR/default/x")
        with self.assertRaises(LibraryAccountError):
            library_account._validate_url("https://katalog.kutuphane.itu.edu.tr/other")
        with self.assertRaises(LibraryAccountError):
            library_account._validate_url("https://katalog.kutuphane.itu.edu.tr/client/tr_TR/defaultEvil")
        self.assertEqual(record_id_from_href("/x/ent:$002f$002fSD_ILS$002f0$002fSD_ILS:69360/one"), "SD_ILS:69360")
        self.assertIsNone(record_id_from_href("/x/none"))


if __name__ == "__main__":
    unittest.main()
