"""OBS identity selection page.

The markup follows the real "Kimlik Seçim" page (card, table rows, the
``SetIdentity`` link); every name, number and identifier in it is invented.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from ninova_mcp.obs_errors import ObsError
from ninova_mcp.obs_identity import choose_identity, identity_options, is_identity_page, page_outline
from ninova_mcp.obs_session import ObsSessionCore

BASE = "https://obs.itu.edu.tr"
SPA = "<html><head><title>OBS</title></head><body><div id='app'></div></body></html>"


def card(number: str, status: str, uuid: str, *, key: str = "ogrenci", href: str | None = None) -> str:
    link = href if href is not None else (
        f"/login/SetIdentity?id={uuid}&amp;returnURL=%2Fogrenci%2F&amp;yetkiAnahtari={key}&amp;ogrNo={number}"
    )
    return f"""
    <div class="card identity-card mx-3 mb-4" data-id="{uuid}" data-default-order="0"
         data-search="{uuid} {key} Deneme Fakültesi {number} {status}" data-role="Öğrenci" data-key="{key}">
      <span class="identity-last-used-label">Son kullanılan</span>
      <div class="identity-card-favorite-action">
        <button type="button" class="identity-action-btn identity-favorite-btn"><i class="far fa-star"></i></button>
      </div>
      <div class="card-body d-flex justify-content-between align-items-center">
        <table class="table table-borderless"><tbody>
          <tr><th>Seviye:</th><td>Lisans</td></tr>
          <tr><th>Bölüm:</th><td>Deneme M&#xFC;hendisli&#x11F;i</td></tr>
          <tr><th>Öğrenci No:</th><td>{number}</td></tr>
          <tr><th>Durum:</th><td>{status}</td></tr>
        </tbody></table>
        <a class="stretched-link" data-select-identity="{uuid}" href="{link}">
          <i class="icon-user-arrow-right ml-5"></i></a>
      </div>
    </div>"""


def page(*cards: str) -> str:
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8" /><title>OBS - Kimlik Seçim</title></head>'
        "<body><div class='container'><p>Merhaba Deneme Öğrenci</p>"
        "<div id='identityList' class='identity-list d-flex flex-wrap mt-4'>" + "".join(cards) + "</div></div></body></html>"
    )


UUID_A = "11111111-aaaa-4bbb-8ccc-000000000001"
UUID_B = "22222222-aaaa-4bbb-8ccc-000000000002"
ACTIVE = card("010000001", "Aktif", UUID_A)
TRANSFER = card("020000002", "Yatay Ge&#xE7;i&#x15F;", UUID_B)
STAFF = card("030000003", "Aktif", "33333333-aaaa-4bbb-8ccc-000000000003", key="personel")


def reply(text: str, url: str = BASE + "/ogrenci/") -> Mock:
    return Mock(text=text, url=url, status_code=200)


def session(*replies: Mock) -> tuple[ObsSessionCore, Mock]:
    core = ObsSessionCore.__new__(ObsSessionCore)
    core.base_url = BASE
    send = Mock(side_effect=list(replies))
    core._safe_request = send  # type: ignore[method-assign]
    return core, send


class PageTests(unittest.TestCase):
    def test_page_is_recognised(self) -> None:
        self.assertTrue(is_identity_page(page(ACTIVE, TRANSFER)))
        self.assertTrue(is_identity_page("<body>" + ACTIVE + "</body>"))
        for other in ("", None, SPA, '"aaa.bbb.ccc"', Mock()):
            self.assertFalse(is_identity_page(other))

    def test_cards_are_read(self) -> None:
        active, transfer = identity_options(page(ACTIVE, TRANSFER), BASE + "/ogrenci/")
        self.assertEqual(active.student_number, "010000001")
        self.assertEqual(active.program, "Deneme Mühendisliği")
        self.assertTrue(active.active)
        self.assertEqual(
            active.href,
            f"{BASE}/login/SetIdentity?id={UUID_A}&returnURL=%2Fogrenci%2F&yetkiAnahtari=ogrenci&ogrNo=010000001",
        )
        self.assertEqual((transfer.student_number, transfer.status, transfer.active), ("020000002", "Yatay Geçiş", False))

    def test_other_roles_are_not_student_records(self) -> None:
        options = identity_options(page(STAFF, ACTIVE), BASE)
        self.assertEqual([option.student_number for option in options], ["010000001"])

    def test_the_only_active_record_is_chosen_unless_a_number_is_configured(self) -> None:
        options = identity_options(page(TRANSFER, ACTIVE), BASE)
        self.assertEqual(choose_identity(options, None).student_number, "010000001")
        self.assertEqual(choose_identity(options, " 020000002 ").student_number, "020000002")

    def test_no_guess_between_two_active_records_or_for_an_unknown_number(self) -> None:
        two = identity_options(page(ACTIVE, card("050000005", "Aktif", UUID_B)), BASE)
        with self.assertRaises(ObsError) as raised:
            choose_identity(two, None)
        self.assertIn("NINOVA_OBS_STUDENT_NUMBER", str(raised.exception))
        self.assertIn("050000005 (Aktif)", str(raised.exception))
        with self.assertRaises(ObsError):
            choose_identity(two, "999999999")
        with self.assertRaises(ObsError):
            choose_identity(identity_options(page(TRANSFER), BASE), None)
        with self.assertRaises(ObsError):
            choose_identity([], None)

    def test_outline_hides_names_numbers_and_identifiers(self) -> None:
        outline = page_outline(page(ACTIVE, TRANSFER))
        self.assertIn("identity-card", outline)
        self.assertIn("href=/login/SetIdentity?id=UUID", outline)
        for secret in ("Deneme", "010000001", "020000002", UUID_A):
            self.assertNotIn(secret, outline)


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("NINOVA_OBS_STUDENT_NUMBER", None)

    def test_ordinary_home_makes_one_request(self) -> None:
        core, send = session(reply(SPA))
        core._open_student_home()
        send.assert_called_once_with("GET", BASE + "/ogrenci/", timeout=30)

    def test_active_record_is_opened(self) -> None:
        core, send = session(reply(page(TRANSFER, ACTIVE)), reply(SPA))
        core._open_student_home()
        self.assertEqual(send.call_count, 2)
        method, url = send.call_args_list[1].args
        self.assertEqual(method, "GET")
        self.assertIn(f"/login/SetIdentity?id={UUID_A}&", url)
        self.assertTrue(url.endswith("ogrNo=010000001"))

    def test_configured_number_wins(self) -> None:
        os.environ["NINOVA_OBS_STUDENT_NUMBER"] = "020000002"
        core, send = session(reply(page(ACTIVE, TRANSFER)), reply(SPA))
        core._open_student_home()
        self.assertIn(UUID_B, send.call_args_list[1].args[1])

    def test_unusable_link_or_refused_choice_is_an_error_with_a_masked_outline(self) -> None:
        scripted = card("010000001", "Aktif", UUID_A, href="javascript:void(0)")
        foreign = card("010000001", "Aktif", UUID_A, href="https://example.com/login/SetIdentity")
        for first, later in ((page(scripted), []), (page(foreign), []), (page(ACTIVE), [reply(page(ACTIVE))])):
            core, send = session(reply(first), *later)
            with self.assertRaises(ObsError) as raised:
                core._open_student_home()
            self.assertIn("identity-card", str(raised.exception))
            self.assertNotIn("010000001", str(raised.exception))
            self.assertEqual(send.call_count, 1 + len(later))


if __name__ == "__main__":
    unittest.main()
