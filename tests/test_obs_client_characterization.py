"""Pins the behaviour of ``ObsClient``: session, JWT, request core, semesters.

Everything runs against a recording fake of the Ninova client, so the order
of the security-relevant steps (throttle, session check, token, request) is
asserted and nothing reaches the network.
"""

from __future__ import annotations

import base64
import os
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import requests

from ninova_mcp import private_io
from ninova_mcp.client import NinovaAuthError, NinovaError
from ninova_mcp.obs_client import ObsClient, ObsError, describe_semester

BASE = "https://obs.itu.edu.tr"
HOME = BASE + "/ogrenci/"
JWT = BASE + "/ogrenci/auth/jwt"
TOKEN = "aa.bb.cc"
LOGIN_HTML = "<html>login form</html>"

TERMS = {"ogrenciDonemListesi": [
    {"akademikDonemId": 41, "donemKodu": "204010", "akademikDonemAdi": "2039-2040 Güz Dönemi", "akademikDonemAdiEN": "2039-2040 Fall Semester"},
    {"akademikDonemId": 42, "donemKodu": "204020", "akademikDonemAdi": "2039-2040 Bahar Dönemi", "akademikDonemAdiEN": "2039-2040 Spring Semester"},
    {"akademikDonemId": 43, "donemKodu": "204030", "akademikDonemAdi": "2039-2040 Yaz Dönemi", "akademikDonemAdiEN": "2039-2040 Summer Semester"},
    {"akademikDonemId": 44, "donemKodu": "204110", "akademikDonemAdi": "2040-2041 Güz Dönemi", "akademikDonemAdiEN": "2040-2041 Fall Semester"},
    {"akademikDonemId": 45, "donemKodu": "204120", "akademikDonemAdi": "2040-2041 Bahar Dönemi", "akademikDonemAdiEN": "2040-2041 Spring Semester"},
    {"akademikDonemId": 46, "donemKodu": "204210", "akademikDonemAdi": "2041-2042 Güz Dönemi", "akademikDonemAdiEN": "2041-2042 Fall Semester"},
]}
ALL_TERMS_LISTING = (
    "2039-2040 Güz Dönemi (term code 204010), id 41; 2039-2040 Bahar Dönemi (term code 204020), id 42; "
    "2039-2040 Yaz Dönemi (term code 204030), id 43; 2040-2041 Güz Dönemi (term code 204110), id 44; "
    "2040-2041 Bahar Dönemi (term code 204120), id 45; 2041-2042 Güz Dönemi (term code 204210), id 46"
)
BARE_NUMBER = (
    "A bare number must be a term code (e.g. 202610) or an OBS term id; a year alone does not identify a term."
)


def response(status: int = 200, body: str = "", content_type: str | None = "text/plain") -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = body.encode("utf-8")
    result.encoding = "utf-8"
    if content_type is not None:
        result.headers["Content-Type"] = content_type
    return result


class FakeNinova:
    """Records every call the OBS client makes on the Ninova client."""

    login_method = "requests"

    def __init__(self, responses: list[requests.Response] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple] = []

    def ensure_logged_in(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("ensure_logged_in", args, kwargs))

    def _throttle(self) -> None:
        self.calls.append(("throttle",))

    def _safe_request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append(("request", method, url, kwargs))
        return self.responses.pop(0)

    def _looks_like_login_page(self, response: requests.Response, html: str | None = None) -> bool:
        return "login" in (html or "")


def obs_with(responses: list[requests.Response] | None = None, *, token: bool = True) -> tuple[ObsClient, FakeNinova]:
    ninova = FakeNinova(responses)
    client = ObsClient(ninova_client=ninova)  # type: ignore[arg-type]
    if token:
        client._jwt = TOKEN
        client._jwt_obtained_at = 10 ** 12  # far in the future: never expires in a test
    return client, ninova


HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/plain, */*",
    "Referer": HOME,
    "X-Requested-With": "XMLHttpRequest",
}
MINT = [
    ("ensure_logged_in", (), {}),
    ("request", "GET", HOME, {"timeout": 30}),
    ("request", "GET", JWT, {"timeout": 30}),
]


class ConstructionTests(unittest.TestCase):
    def test_base_url_must_be_the_obs_host_over_https(self) -> None:
        for bad in ("http://obs.itu.edu.tr", "https://obs.itu.edu.tr.example.org", "https://example.org",
                    "https://ninova.itu.edu.tr"):
            with self.subTest(bad=bad):
                with self.assertRaises(ObsError) as caught:
                    ObsClient(ninova_client=MagicMock(), base_url=bad)
                self.assertEqual(str(caught.exception), "NINOVA_OBS_BASE_URL must be https://obs.itu.edu.tr")

    def test_base_url_from_argument_or_environment_loses_trailing_slash(self) -> None:
        self.assertEqual(ObsClient(ninova_client=MagicMock(), base_url=BASE + "/").base_url, BASE)
        with patch.dict("os.environ", {"NINOVA_OBS_BASE_URL": "https://example.org"}):
            with self.assertRaises(ObsError):
                ObsClient(ninova_client=MagicMock())
            self.assertEqual(ObsClient(ninova_client=MagicMock(), base_url=BASE).base_url, BASE)

    def test_initial_state(self) -> None:
        with patch.dict("os.environ", {"NINOVA_OBS_JWT_TTL_SECONDS": ""}):
            client = ObsClient(ninova_client=MagicMock())
        self.assertIsNone(client._jwt)
        self.assertIsNone(client._jwt_obtained_at)
        self.assertIsNone(client._last_registration_check)
        self.assertEqual(client._jwt_ttl_seconds, 1500.0)
        with patch.dict("os.environ", {"NINOVA_OBS_JWT_TTL_SECONDS": "10"}):
            self.assertEqual(ObsClient(ninova_client=MagicMock())._jwt_ttl_seconds, 10.0)

    def test_session_and_safe_request_belong_to_the_ninova_client(self) -> None:
        ninova = MagicMock()
        client = ObsClient(ninova_client=ninova)
        self.assertIs(client.ninova, ninova)
        self.assertIs(client.session, ninova.session)
        self.assertIs(client._safe_request("GET", HOME, timeout=3), ninova._safe_request.return_value)
        ninova._safe_request.assert_called_once_with("GET", HOME, timeout=3)

    def test_error_type_is_a_ninova_error(self) -> None:
        self.assertTrue(issubclass(ObsError, NinovaError))


class JwtTests(unittest.TestCase):
    def test_minting_checks_the_session_then_opens_home_then_reads_the_token(self) -> None:
        client, ninova = obs_with([response(), response(body=f'"{TOKEN}"\n')], token=False)
        with patch("time.time", return_value=5000.0):
            self.assertEqual(client._get_jwt(), TOKEN)
        self.assertEqual(ninova.calls, MINT)
        self.assertEqual(client._jwt, TOKEN)
        self.assertEqual(client._jwt_obtained_at, 5000.0)

    def test_token_is_reused_strictly_inside_its_ttl(self) -> None:
        client, ninova = obs_with([response(), response(body="new.to.ken")], token=False)
        client._jwt, client._jwt_obtained_at = TOKEN, 5000.0
        with patch("time.time", return_value=5000.0 + 1499.9):
            self.assertEqual(client._get_jwt(), TOKEN)
        self.assertEqual(ninova.calls, [])
        with patch("time.time", return_value=5000.0 + 1500.0):
            self.assertEqual(client._get_jwt(), "new.to.ken")
        self.assertEqual(ninova.calls, MINT)
        self.assertEqual(client._jwt_obtained_at, 6500.0)

    def test_force_ignores_a_fresh_token(self) -> None:
        client, ninova = obs_with([response(), response(body="new.to.ken")])
        self.assertEqual(client._get_jwt(force=True), "new.to.ken")
        self.assertEqual(ninova.calls, MINT)

    def test_a_token_without_a_timestamp_is_not_reused(self) -> None:
        client, _ = obs_with([response(), response(body="new.to.ken")], token=False)
        client._jwt = TOKEN
        self.assertEqual(client._get_jwt(), "new.to.ken")

    def test_failed_or_empty_answer(self) -> None:
        for answer in (response(500, TOKEN), response(200, "  \n"), response(401, "")):
            with self.subTest(status=answer.status_code):
                client, _ = obs_with([response(), answer], token=False)
                with self.assertRaises(NinovaAuthError) as caught:
                    client._get_jwt()
                self.assertEqual(
                    str(caught.exception),
                    "Could not obtain OBS JWT. Log in via Ninova credentials and open "
                    "https://obs.itu.edu.tr/ogrenci/ in a browser to verify access.",
                )
                self.assertIsNone(client._jwt)
                self.assertIsNone(client._jwt_obtained_at)

    def test_answer_that_is_not_a_token(self) -> None:
        html = "<html><script src='a.b.js'></script>login page</html>"
        for answer in (response(200, "one.dot"), response(200, html, "text/html"), response(200, "nodots")):
            with self.subTest(body=answer.text):
                client, _ = obs_with([response(), answer], token=False)
                with self.assertRaises(NinovaAuthError) as caught:
                    client._get_jwt()
                self.assertEqual(
                    str(caught.exception),
                    "OBS returned a login page instead of a JWT. The İTÜ SSO session is "
                    "stale; call refresh_session and try again.",
                )
                self.assertIsNone(client._jwt)

    def test_headers_carry_the_token(self) -> None:
        client, ninova = obs_with()
        self.assertEqual(client._headers(), HEADERS)
        self.assertEqual(ninova.calls, [])

    def test_ensure_ready_verifies_opens_home_and_forces_a_token(self) -> None:
        client, ninova = obs_with([response(), response(), response(body=TOKEN)])
        self.assertEqual(client.ensure_ready(), {"obs_base_url": BASE, "jwt_present": True, "login_method": "requests"})
        self.assertEqual(ninova.calls, [("ensure_logged_in", (), {"verify": True}),
                                        ("request", "GET", HOME, {"timeout": 30}), *MINT])


class LoginPageDetectionTests(unittest.TestCase):
    def test_json_content_type_or_json_shaped_or_empty_body_is_never_a_login_page(self) -> None:
        client, _ = obs_with()
        client.ninova._looks_like_login_page = Mock(return_value=True)  # type: ignore[method-assign]
        for answer in (response(200, "login", "application/json"), response(200, ' {"login": 1}'),
                       response(200, "[login]"), response(200, ""), response(200, "", None)):
            with self.subTest(body=answer.text):
                self.assertFalse(client._looks_like_obs_login_page(answer))
        client.ninova._looks_like_login_page.assert_not_called()

    def test_anything_else_is_decided_by_the_ninova_client(self) -> None:
        client, _ = obs_with()
        self.assertTrue(client._looks_like_obs_login_page(response(200, LOGIN_HTML, None)))
        self.assertFalse(client._looks_like_obs_login_page(response(200, "plain words")))


class ApiRequestTests(unittest.TestCase):
    URL = BASE + "/api/x"
    RECOVERY = [("ensure_logged_in", (), {"verify": True}), *MINT, ("throttle",)]

    def request_call(self, method: str = "GET", **extra: Any) -> tuple:
        return ("request", method, self.URL, {"headers": HEADERS, "params": None, **extra, "timeout": 45})

    def recovery(self, first: requests.Response, second: requests.Response) -> tuple[ObsClient, FakeNinova]:
        return obs_with([first, response(), response(body=TOKEN), second])

    def test_get_throttles_then_sends_one_authorised_request(self) -> None:
        client, ninova = obs_with([response(200, '{"ok": true}', "application/json")])
        self.assertEqual(client.api_get("/api/x", params={"a": 1}), {"ok": True})
        self.assertEqual(ninova.calls, [("throttle",), self.request_call(params={"a": 1})])
        self.assertNotIn("json", ninova.calls[1][3])

    def test_a_missing_token_is_minted_after_the_throttle_and_before_the_request(self) -> None:
        client, ninova = obs_with([response(), response(body=TOKEN), response(200, "[1]")], token=False)
        self.assertEqual(client.api_get("/api/x"), [1])
        self.assertEqual(ninova.calls, [("throttle",), *MINT, self.request_call()])

    def test_path_gains_a_leading_slash_and_a_body_is_sent_as_json(self) -> None:
        client, ninova = obs_with([response(200, '{"ok": 1}')])
        self.assertEqual(client._api_request("POST", "api/x", json_body={"ecrn": ["1"]}), {"ok": 1})
        self.assertEqual(ninova.calls[1], self.request_call("POST", json={"ecrn": ["1"]}))

    def test_api_get_is_a_get_through_the_request_core(self) -> None:
        client, _ = obs_with()
        client._api_request = Mock(return_value="x")  # type: ignore[method-assign]
        self.assertEqual(client.api_get("/p", params={"a": 1}), "x")
        client._api_request.assert_called_once_with("GET", "/p", params={"a": 1})
        client.api_get("/q")
        client._api_request.assert_called_with("GET", "/q", params=None)

    def test_decoding_of_the_answer(self) -> None:
        cases = (
            (response(200, ""), None),
            (response(200, '{"a": 1}', "application/json; charset=utf-8"), {"a": 1}),
            (response(200, '"text"', "application/JSON"), "text"),
            (response(200, "[1, 2]", "text/plain"), [1, 2]),
            (response(200, '{"a": 2}', None), {"a": 2}),
            (response(200, "plain words", "text/plain"), "plain words"),
            (response(204, ""), None),
        )
        for answer, expected in cases:
            with self.subTest(body=answer.text):
                client, _ = obs_with([answer])
                self.assertEqual(client.api_get("/api/x"), expected)

    def test_http_error_names_the_normalised_path(self) -> None:
        for status in (400, 404, 500):
            with self.subTest(status=status):
                client, ninova = obs_with([response(status, "boom")])
                with self.assertRaises(ObsError) as caught:
                    client.api_get("api/x")
                self.assertEqual(str(caught.exception), f"OBS API /api/x failed with HTTP {status}")
                self.assertEqual(len(ninova.calls), 2)

    def test_rejected_or_login_page_answer_reverifies_remints_and_retries_once(self) -> None:
        for first in (response(401, ""), response(403, "denied"), response(200, LOGIN_HTML, "text/html")):
            with self.subTest(status=first.status_code):
                client, ninova = self.recovery(first, response(200, '{"ok": true}'))
                self.assertEqual(client._api_request("POST", "/api/x", json_body={"a": 1}), {"ok": True})
                call = self.request_call("POST", json={"a": 1})
                self.assertEqual(ninova.calls, [("throttle",), call, *self.RECOVERY, call])

    def test_login_page_after_the_retry_is_a_clear_error(self) -> None:
        client, ninova = self.recovery(response(401, ""), response(200, LOGIN_HTML, "text/html"))
        with self.assertRaises(ObsError) as caught:
            client.api_get("api/x")
        self.assertEqual(
            str(caught.exception),
            "OBS API /api/x kept returning a login page after refreshing the session. "
            "Call refresh_session, or check access manually at https://obs.itu.edu.tr/ogrenci/.",
        )
        self.assertEqual(ninova.responses, [])

    def test_error_status_after_the_retry_is_reported_and_not_retried_again(self) -> None:
        for status in (401, 403, 500):
            with self.subTest(status=status):
                client, ninova = self.recovery(response(200, LOGIN_HTML), response(status, ""))
                with self.assertRaises(ObsError) as caught:
                    client.api_get("/api/x")
                self.assertEqual(str(caught.exception), f"OBS API /api/x failed with HTTP {status}")
                self.assertEqual(len(ninova.calls), 8)

    def test_empty_answer_after_the_retry_is_none(self) -> None:
        client, _ = self.recovery(response(401, ""), response(200, ""))
        self.assertIsNone(client.api_get("/api/x"))


class EndpointTests(unittest.TestCase):
    def test_every_read_maps_to_its_path(self) -> None:
        client, _ = obs_with()
        client.api_get = Mock(return_value={"ok": 1})  # type: ignore[method-assign]
        cases = (
            (client.get_profile, (), "/api/ogrenci/KisiselBilgiler/"),
            (client.list_programs, (), "/api/ogrenci/OgrenciProgamListesi/"),
            (client.list_semesters, (), "/api/ogrenci/DonemListesi/"),
            (client.get_registration_status, (), "/api/ogrenci/KayitDurumu"),
            (client.get_lesson_registration_status, (), "/api/ogrenci/DersKayitDurumu"),
            (client.get_advisor, (), "/api/ogrenci/DanismanBilgi"),
            (client.get_internships, (), "/api/ogrenci/StajBilgi"),
            (client.get_contacts, (), "/api/ogrenci/IletisimBilgileri"),
            (client.get_academic_status, (7,), "/api/ogrenci/AkademikDurum/7"),
            (client.get_debts, ("7",), "/api/ogrenci/BorcBilgisi/7"),
            (client.get_graduation_remaining, (7,), "/api/ogrenci/MezuniyetimeNeKaldi/7"),
            (client.list_registered_courses, (42,), "/api/ogrenci/sinif/KayitliSinifListesi/42"),
            (client.get_letter_grades, (42,), "/api/ogrenci/Sinif/SinifHarfNotuListesi/42"),
            (client.get_midterm_grades, (9,), "/api/ogrenci/Sinif/SinifDonemIciNotListesi/9"),
            (client.get_attendance, (9,), "/api/ogrenci/Sinif/SinifOgrenciYoklama/9"),
            (client.get_schedule, (42,), "/api/ogrenci/Takvim/DersTakvimi/42"),
        )
        for method, args, path in cases:
            with self.subTest(method=method.__name__):
                client.api_get.reset_mock()
                self.assertEqual(method(*args), {"ok": 1})
                client.api_get.assert_called_once_with(path)
        client.api_get.reset_mock()
        client.get_final_calendar(42)
        client.api_get.assert_called_once_with("/api/ogrenci/Takvim/FinalTakvimi", params={"donemId": 42})
        client.api_get.reset_mock()
        client.get_transcript_preview()
        client.get_transcript_preview(english=True)
        self.assertEqual([call.args[0] for call in client.api_get.call_args_list],
                         ["/api/ogrenci/Belgeler/TranskriptOnizleme", "/api/ogrenci/Belgeler/TranskriptIngilizceOnizleme"])

    def test_default_program_is_the_first_listed(self) -> None:
        client, _ = obs_with()
        client.list_programs = Mock(return_value={"ogrenciProgramBilgiListesi": [  # type: ignore[method-assign]
            {"ogrenciProgramId": "17"}, {"ogrenciProgramId": 18}]})
        self.assertEqual(client.default_program_id(), 17)
        for empty in ({}, {"ogrenciProgramBilgiListesi": None}, {"ogrenciProgramBilgiListesi": []}):
            client.list_programs = Mock(return_value=empty)  # type: ignore[method-assign]
            with self.assertRaises(ObsError) as caught:
                client.default_program_id()
            self.assertEqual(str(caught.exception), "No OBS student programs found.")

    def test_card_info_reads_the_portal_page_through_the_ninova_client(self) -> None:
        ninova = MagicMock()
        ninova.get_portal_html.return_value = ("<html></html>", "https://portal.itu.edu.tr/apps/default/")
        client = ObsClient(ninova_client=ninova)
        with patch("ninova_mcp.parsing.extract_campus_card_info", return_value={"card": 1}) as extract:
            self.assertEqual(client.get_card_info(), {"card": 1})
        ninova.get_portal_html.assert_called_once_with("/apps/default/")
        extract.assert_called_once_with("<html></html>", "https://portal.itu.edu.tr/apps/default/", base_url=BASE)

    def test_registration_reads_and_writes_go_through_their_modules_with_this_client(self) -> None:
        client, _ = obs_with()
        with patch("ninova_mcp.registration_draft.read_registration_draft", return_value={"d": 1}) as read:
            self.assertEqual(client.get_registration_draft(), {"d": 1})
        read.assert_called_once_with(client)
        gate = Mock()
        with patch("ninova_mcp.registration_write.save_registration_draft", return_value={"s": 1}) as save:
            self.assertEqual(client.save_registration_draft(["1"]), {"s": 1})
            self.assertEqual(client.save_registration_draft(["1"], allow_replace=True, confirm_write=gate), {"s": 1})
        self.assertEqual(save.call_args_list[0].args, (client, ["1"]))
        self.assertEqual(save.call_args_list[0].kwargs, {"allow_replace": False})
        self.assertEqual(save.call_args_list[1].kwargs, {"allow_replace": True, "confirm_write": gate})


class TranscriptTests(unittest.TestCase):
    PDF = b"%PDF-1.4 synthetic"

    def client(self, payload: Any) -> ObsClient:
        client, _ = obs_with()
        client.get_transcript_preview = Mock(return_value=payload)  # type: ignore[method-assign]
        return client

    def test_pdf_is_decoded_and_written_under_the_default_name(self) -> None:
        payload = {"belgeAsByteArray": base64.b64encode(self.PDF).decode(), "statusCode": 0}
        with tempfile.TemporaryDirectory() as folder:
            for english, name in ((False, "transcript-tr.pdf"), (True, "transcript-en.pdf")):
                client = self.client(payload)
                result = client.save_transcript_pdf(folder, english=english)
                path = Path(folder).resolve() / name
                self.assertEqual(result, {"path": str(path), "size_bytes": len(self.PDF),
                                          "english": english, "statusCode": 0})
                self.assertEqual(path.read_bytes(), self.PDF)
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                client.get_transcript_preview.assert_called_once_with(english=english)
            result = self.client(payload).save_transcript_pdf(folder, filename="mine.pdf")
            self.assertEqual(result["path"], str(Path(folder).resolve() / "mine.pdf"))
            self.assertEqual(sorted(item.name for item in Path(folder).iterdir()),
                             ["mine.pdf", "transcript-en.pdf", "transcript-tr.pdf"])

    def test_the_file_goes_through_the_private_writer_and_the_directory_mode_is_left_alone(self) -> None:
        payload = {"belgeAsByteArray": base64.b64encode(self.PDF).decode()}
        # The writer the method really calls, wherever the method is defined.
        namespace = ObsClient.save_transcript_pdf.__globals__
        self.assertIs(namespace["write_private_bytes"], private_io.write_private_bytes)
        writer = Mock()
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(namespace, {"write_private_bytes": writer}):
                result = self.client(payload).save_transcript_pdf(folder)
            writer.assert_called_once_with(Path(folder).resolve() / "transcript-tr.pdf", self.PDF, private_dir=False)
            self.assertIsNone(result["statusCode"])

    def test_preview_without_a_document_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            for payload in ({}, {"belgeAsByteArray": ""}, {"belgeAsByteArray": None}, "text", None, []):
                with self.subTest(payload=payload):
                    with self.assertRaises(ObsError) as caught:
                        self.client(payload).save_transcript_pdf(folder)
                    self.assertEqual(str(caught.exception), "Transcript preview did not include belgeAsByteArray.")
            self.assertEqual(list(Path(folder).iterdir()), [])


def semesters(terms: Any = TERMS) -> ObsClient:
    client, _ = obs_with()
    client.list_semesters = Mock(return_value=terms)  # type: ignore[method-assign]
    return client


def numbered_terms(name: str, count: int = 10) -> dict[str, Any]:
    return {"ogrenciDonemListesi": [
        {"akademikDonemId": n, "donemKodu": f"30{n:02d}", "akademikDonemAdi": f"{name} {n}"} for n in range(1, count + 1)
    ]}


class SemesterMessageTests(unittest.TestCase):
    def failure(self, query: Any, terms: Any = TERMS) -> str:
        with self.assertRaises(ObsError) as caught:
            semesters(terms).resolve_semester(query)
        return str(caught.exception)

    def test_no_terms_at_all(self) -> None:
        for terms in ({}, {"ogrenciDonemListesi": []}, {"ogrenciDonemListesi": None}):
            for query in (None, "bahar", True):
                with self.subTest(terms=terms, query=query):
                    self.assertEqual(self.failure(query, terms), "No OBS semesters found.")

    def test_none_is_the_last_listed_term_as_is(self) -> None:
        self.assertIs(semesters().resolve_semester(None), TERMS["ogrenciDonemListesi"][-1])
        self.assertIs(semesters().resolve_semester(), TERMS["ogrenciDonemListesi"][-1])

    def test_booleans_are_rejected(self) -> None:
        for query in (True, False):
            with self.subTest(query=query):
                self.assertEqual(self.failure(query), "Semester must be a term code, a term id or a term name.")

    def test_exact_id_or_code_returns_the_listed_item_itself(self) -> None:
        for query, position in ((42, 1), ("42", 1), ("204020", 1), (204020, 1), (204020.0, 1), (41.0, 0),
                                ("204020.0", 1), ("204020.00", 1), (' "204020" ', 1), ("'204020'", 1),
                                ("`42`", 1), ("“42”", 1), ("‘204020’", 1), ("  46  ", 5)):
            with self.subTest(query=query):
                self.assertIs(semesters().resolve_semester(query), TERMS["ogrenciDonemListesi"][position])

    def test_the_first_listed_term_whose_id_or_code_matches_wins(self) -> None:
        terms = {"ogrenciDonemListesi": [
            {"akademikDonemId": 1, "donemKodu": "77", "akademikDonemAdi": "A"},
            {"akademikDonemId": 77, "donemKodu": "5", "akademikDonemAdi": "B"},
        ]}
        self.assertEqual(semesters(terms).resolve_semester(77)["akademikDonemAdi"], "A")
        self.assertEqual(semesters(terms).resolve_semester("5")["akademikDonemAdi"], "B")

    def test_a_number_that_is_not_whole_is_looked_up_as_a_name(self) -> None:
        self.assertEqual(self.failure(204020.5), f"Semester not found: 204020.5. Examples: {ALL_TERMS_LISTING}")
        self.assertEqual(self.failure("204020.50"), f"Semester not found: 204020.50. Examples: {ALL_TERMS_LISTING}")

    def test_bare_number_messages(self) -> None:
        self.assertEqual(self.failure("7"), f"Semester not found: 7. {BARE_NUMBER}")
        self.assertEqual(self.failure(20402), f"Semester not found: 20402. {BARE_NUMBER}")
        self.assertEqual(
            self.failure("2041"),
            f"Semester not found: 2041. {BARE_NUMBER} Terms that mention 2041: "
            "2040-2041 Güz Dönemi (term code 204110), id 44; 2040-2041 Bahar Dönemi (term code 204120), id 45; "
            "2041-2042 Güz Dönemi (term code 204210), id 46.",
        )
        self.assertEqual(
            self.failure(2042),
            f"Semester not found: 2042. {BARE_NUMBER} Terms that mention 2042: "
            "2041-2042 Güz Dönemi (term code 204210), id 46.",
        )
        self.assertTrue(self.failure("2041 dönemi").startswith(f"Semester not found: 2041 dönemi. {BARE_NUMBER}"))

    def test_bare_number_hint_lists_at_most_the_last_eight(self) -> None:
        message = self.failure("2050", numbered_terms("2050 Part"))
        self.assertNotIn("id 2;", message)
        self.assertIn("Terms that mention 2050: 2050 Part 3 (term code 3003), id 3; ", message)
        self.assertTrue(message.endswith("2050 Part 10 (term code 3010), id 10."))

    def test_not_found_messages(self) -> None:
        for query in ("2050-2051 Bahar", "ah", "", "dönemi", "kış", "bahar güz", "   "):
            with self.subTest(query=query):
                self.assertEqual(self.failure(query), f"Semester not found: {query}. Examples: {ALL_TERMS_LISTING}")

    def test_not_found_examples_are_the_last_eight_terms(self) -> None:
        message = self.failure("nothing", numbered_terms("Part"))
        self.assertTrue(message.startswith("Semester not found: nothing. Examples: Part 3 (term code 3003), id 3; "))
        self.assertTrue(message.endswith("Part 10 (term code 3010), id 10"))

    def test_ambiguous_messages(self) -> None:
        start = "is ambiguous. Use a term code or add the year range and season. Candidates: "
        year = ("2039-2040 Güz Dönemi (term code 204010), id 41; "
                "2039-2040 Bahar Dönemi (term code 204020), id 42; 2039-2040 Yaz Dönemi (term code 204030), id 43")
        self.assertEqual(self.failure("2039-2040"), f"Semester '2039-2040' {start}{year}")
        self.assertEqual(self.failure("2039 2040"), f"Semester '2039 2040' {start}{year}")
        self.assertEqual(
            self.failure("2040 bahar"),
            f"Semester '2040 bahar' {start}2039-2040 Bahar Dönemi (term code 204020), id 42; "
            "2040-2041 Bahar Dönemi (term code 204120), id 45",
        )

    def test_ambiguous_message_keeps_the_original_argument_and_the_last_eight_candidates(self) -> None:
        message = self.failure(' "2050-2051" ', numbered_terms("2050-2051 Part"))
        self.assertTrue(message.startswith("Semester ' \"2050-2051\" ' is ambiguous."))
        self.assertIn("Candidates: 2050-2051 Part 3 (term code 3003), id 3; ", message)
        self.assertNotIn("id 2;", message)

    def test_a_season_with_another_word_is_ambiguous_not_most_recent(self) -> None:
        terms = {"ogrenciDonemListesi": [
            {"akademikDonemId": 1, "donemKodu": "1", "akademikDonemAdi": "Hazırlık Bahar"},
            {"akademikDonemId": 2, "donemKodu": "2", "akademikDonemAdi": "Hazırlık Bahar"},
        ]}
        self.assertIn("is ambiguous", self.failure("hazırlık bahar", terms))
        self.assertIn("is ambiguous", self.failure("hazırlık", terms))


class SeasonResolutionNoteTests(unittest.TestCase):
    def test_note_contents(self) -> None:
        resolved = semesters().resolve_semester("güz")
        self.assertEqual(resolved, {
            **TERMS["ogrenciDonemListesi"][5],
            "resolution": {
                "query": "güz",
                "rule": "most_recent_term_of_season",
                "note": (
                    "'güz' names a season only. The most recent matching term was used: 2041-2042 Güz Dönemi "
                    "(term code 204210). Pass a term code or a year range with the season "
                    "(e.g. \"2024-2025 Bahar\") for another term."
                ),
                "other_candidates": [
                    {"code": "204110", "id": 44, "name": "2040-2041 Güz Dönemi"},
                    {"code": "204010", "id": 41, "name": "2039-2040 Güz Dönemi"},
                ],
            },
        })
        self.assertEqual(list(resolved)[-1], "resolution")
        self.assertEqual(list(resolved["resolution"]), ["query", "rule", "note", "other_candidates"])
        self.assertNotIn("resolution", TERMS["ogrenciDonemListesi"][5])

    def test_query_is_the_cleaned_text_with_filler_words_kept(self) -> None:
        resolved = semesters().resolve_semester(' "Spring Semester" ')
        self.assertEqual(resolved["resolution"]["query"], "Spring Semester")
        self.assertTrue(resolved["resolution"]["note"].startswith("'Spring Semester' names a season only."))

    def test_other_candidates_are_newest_first_and_at_most_eight(self) -> None:
        terms = {"ogrenciDonemListesi": [
            {"akademikDonemId": n, "donemKodu": f"{2000 + n}20", "akademikDonemAdi": "Bahar Dönemi"} for n in range(1, 12)
        ]}
        others = semesters(terms).resolve_semester("bahar")["resolution"]["other_candidates"]
        self.assertEqual([item["id"] for item in others], [10, 9, 8, 7, 6, 5, 4, 3])

    def test_terms_without_a_numeric_code_rank_below_coded_ones_then_by_position(self) -> None:
        terms = {"ogrenciDonemListesi": [
            {"akademikDonemId": 1, "donemKodu": "204020", "akademikDonemAdi": "Bahar"},
            {"akademikDonemId": 2, "donemKodu": None, "akademikDonemAdi": "Bahar"},
            {"akademikDonemId": 3, "donemKodu": "x", "akademikDonemAdi": "Bahar"},
        ]}
        resolved = semesters(terms).resolve_semester("bahar")
        self.assertEqual(resolved["akademikDonemId"], 1)
        self.assertEqual(resolved["resolution"]["other_candidates"],
                         [{"code": "x", "id": 3, "name": "Bahar"}, {"code": "", "id": 2, "name": "Bahar"}])
        equal = {"ogrenciDonemListesi": [
            {"akademikDonemId": 1, "donemKodu": "5", "akademikDonemAdi": "Yaz"},
            {"akademikDonemId": 2, "donemKodu": "5", "akademikDonemAdi": "Yaz"},
        ]}
        self.assertEqual(semesters(equal).resolve_semester("summer")["akademikDonemId"], 2)

    def test_english_name_alone_is_enough_to_match(self) -> None:
        terms = {"ogrenciDonemListesi": [
            {"akademikDonemId": 1, "donemKodu": "1", "akademikDonemAdiEN": "2030-2031 Autumn Term"},
            {"akademikDonemId": 2, "donemKodu": "2", "akademikDonemAdi": None},
        ]}
        self.assertEqual(semesters(terms).resolve_semester("güz")["akademikDonemId"], 1)
        self.assertEqual(semesters(terms).resolve_semester("2030-2031 fall")["akademikDonemId"], 1)


class DescribeSemesterTests(unittest.TestCase):
    def test_every_form(self) -> None:
        cases = (
            ({"akademikDonemAdi": " 2040-2041  Bahar Dönemi ", "donemKodu": 204120}, "2040-2041 Bahar Dönemi (term code 204120)"),
            ({"akademikDonemAdiEN": "Spring", "donemKodu": "1"}, "Spring (term code 1)"),
            ({"akademikDonemAdi": "Bahar", "akademikDonemAdiEN": "Spring"}, "Bahar"),
            ({"donemKodu": "204120", "akademikDonemId": 3}, "term code 204120"),
            ({"akademikDonemId": 9}, "term id 9"),
            ({}, "term id None"),
            (None, "unknown term"),
            ("204120", "unknown term"),
            ([{"akademikDonemId": 9}], "unknown term"),
        )
        for item, expected in cases:
            with self.subTest(item=item):
                self.assertEqual(describe_semester(item), expected)


if __name__ == "__main__":
    unittest.main()
