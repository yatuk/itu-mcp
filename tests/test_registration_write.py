from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from ninova_mcp.obs_client import ObsClient, ObsError
from ninova_mcp.client import DEFAULT_HEADERS
from ninova_mcp.registration_write import (
    SAVE_DRAFT_PATH, _post_once, save_registration_draft,
)


def draft(crns=(), *, allowed=True, term="202710"):
    return {"term_code": term, "draft_creation_allowed": allowed, "draft_exists": bool(crns),
            "courses": [{"crn": crn} for crn in crns]}


def response(payload=None, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result._content_consumed = True
    result.headers["Content-Type"] = "application/json"
    return result


class RegistrationWriteTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"NINOVA_OBS_REGISTRATION_WRITES": "1"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        source_session = requests.Session()
        source_session.auth = ("synthetic-user", "synthetic-password")
        source_session.proxies = {"https": "http://synthetic-proxy.example:8080"}
        source_session.cookies.set("sso", "synthetic-cookie", domain="obs.itu.edu.tr")
        self.obs = SimpleNamespace(base_url="https://obs.itu.edu.tr", session=source_session,
            _headers=Mock(return_value={"Authorization": "Bearer synthetic-token", "Accept": "application/json"}),
            get_registration_draft=Mock(side_effect=[draft(), draft(["12345", "12346"])]))

    def test_draft_write_uses_static_public_headers_without_private_session_headers(self):
        self.obs.session.headers.update({"User-Agent": "synthetic-session-agent",
            "Accept-Language": "synthetic-language", "X-Private-Session": "synthetic-private-value"})
        with patch.object(requests.Session, "request", return_value=response({"statusCode": 0})) as request:
            save_registration_draft(self.obs, ["12345", "12346"])
        headers = request.call_args.kwargs["headers"]
        self.assertEqual(headers["User-Agent"], DEFAULT_HEADERS["User-Agent"])
        self.assertEqual(headers["Accept-Language"], DEFAULT_HEADERS["Accept-Language"])
        self.assertEqual(headers["Authorization"], "Bearer synthetic-token")
        self.assertNotIn("X-Private-Session", headers)
        self.assertNotIn("synthetic-private-value", json.dumps(headers))


    def test_exact_official_payload_single_attempt_verified_transport_and_readback(self):
        seen = []
        def request(session, method, url, **kwargs):
            seen.append((session, method, url, kwargs))
            self.assertFalse(session.trust_env)
            self.assertIsNone(session.auth)
            self.assertIsNone(session.cert)
            self.assertEqual(session.proxies, {})
            self.assertEqual(session.cookies.get("sso"), "synthetic-cookie")
            self.assertIsNot(session.cookies, self.obs.session.cookies)
            retries = session.get_adapter(url).max_retries
            for key in ("total", "connect", "read", "redirect", "status", "other"):
                self.assertEqual(getattr(retries, key), 0)
            return response({"statusCode": 0})
        with patch.object(requests.Session, "request", request):
            result = save_registration_draft(self.obs, ["12345", "12346"])
        self.assertEqual(result["status"], "saved")
        self.assertTrue(result["saved"])
        self.assertEqual(self.obs.get_registration_draft.call_count, 2)
        self.assertEqual(len(seen), 1)
        _, method, url, options = seen[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://obs.itu.edu.tr" + SAVE_DRAFT_PATH)
        self.assertEqual(options["json"], {"ecrn": ["12345", "12346"]})
        self.assertIs(options["allow_redirects"], False)
        self.assertIs(options["verify"], True)
        self.assertFalse(result["course_registration_performed"])
        self.assertFalse(result["retry_performed"])

    def test_input_and_operator_gate_run_before_any_auth_or_network(self):
        for invalid in ([], ["12345", "12345"], [12345], ["12345"], ["12345/../"]):
            with self.subTest(invalid=invalid):
                self.obs.get_registration_draft.reset_mock()
                with patch.dict(os.environ, {}, clear=True), patch.object(requests.Session, "request") as request:
                    with self.assertRaises(ObsError):
                        save_registration_draft(self.obs, invalid)
                self.obs.get_registration_draft.assert_not_called()
                self.obs._headers.assert_not_called()
                request.assert_not_called()

    def test_unknown_or_closed_draft_period_does_not_send(self):
        for before in (draft(allowed=False), draft(allowed=None), draft(allowed=1), draft(term="bad"), {}):
            with self.subTest(before=before):
                self.obs.get_registration_draft = Mock(return_value=before)
                with patch.object(requests.Session, "request") as request, self.assertRaises(ObsError):
                    save_registration_draft(self.obs, ["12345"])
                request.assert_not_called()
                self.obs._headers.assert_not_called()

    def test_unapproved_destination_rejected_before_headers(self):
        for base, path in (("http://obs.itu.edu.tr", SAVE_DRAFT_PATH),
                           ("https://obs.itu.edu.tr:444", SAVE_DRAFT_PATH),
                           ("https://obs.itu.edu.tr.attacker.example", SAVE_DRAFT_PATH),
                           ("https://obs.itu.edu.tr", "/api/not-a-draft-endpoint/"),
                           ("https://obs.itu.edu.tr", SAVE_DRAFT_PATH + "?action=drop")):
            with self.subTest(base=base, path=path):
                self.obs.base_url = base
                with patch.object(requests.Session, "request") as request, self.assertRaises(ObsError):
                    _post_once(self.obs, path, {"ecrn": ["12345"]})
                request.assert_not_called()
                self.obs._headers.assert_not_called()

    def test_auth_redirect_and_http_errors_are_not_replayed(self):
        for code in (301, 302, 303, 307, 308, 401, 403, 429, 500):
            with self.subTest(code=code):
                self.obs.get_registration_draft = Mock(side_effect=[draft(), draft(["12345"])])
                reply = response({"statusCode": 0}, status=code)
                reply.headers["Location"] = "https://obs.itu.edu.tr/another"
                with patch.object(requests.Session, "request", return_value=reply) as request:
                    result = save_registration_draft(self.obs, ["12345"])
                self.assertEqual(result["status"], "uncertain")
                self.assertIsNone(result["saved"])
                self.assertEqual(result["readback"]["status"], "matched")
                request.assert_called_once()
                self.assertEqual(self.obs.get_registration_draft.call_count, 2)

    def test_timeout_then_matching_readback_is_not_fabricated_acknowledgement(self):
        with patch.object(requests.Session, "request", side_effect=requests.Timeout("synthetic-private-message")) as request:
            result = save_registration_draft(self.obs, ["12345", "12346"])
        request.assert_called_once()
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue(result["readback"]["matches_requested"])
        self.assertNotIn("synthetic-private-message", json.dumps(result))

    def test_business_rejection_is_distinct_and_messages_are_not_echoed(self):
        reply = response({"statusCode": 1, "resultCode": "ERRMaxIstekTaslakOlustur", "resultMessage": "synthetic-private-message"})
        with patch.object(requests.Session, "request", return_value=reply) as request:
            result = save_registration_draft(self.obs, ["12345", "12346"])
        request.assert_called_once()
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["saved"])
        self.assertEqual(result["submission"]["result_code"], "ERRMaxIstekTaslakOlustur")
        self.assertNotIn("synthetic-private-message", json.dumps(result))

    def test_success_requires_matching_complete_same_term_readback(self):
        cases = [draft(["12345"]), draft(["12345", "12346"], term="202720"),
                 draft(["12345", "12345"]), {**draft(), "courses": [None]},
                 ObsError("synthetic unreadable draft")]
        for after in cases:
            with self.subTest(after_type=type(after).__name__):
                self.obs.get_registration_draft = Mock(side_effect=[draft(), after])
                with patch.object(requests.Session, "request", return_value=response({"statusCode": 0})) as request:
                    result = save_registration_draft(self.obs, ["12345", "12346"])
                self.assertEqual(result["status"], "uncertain")
                self.assertIsNone(result["saved"])
                request.assert_called_once()

    def test_malformed_or_empty_acknowledgement_is_uncertain(self):
        for payload in (None, [], {}, {"statusCode": True}, {"statusCode": "0"}):
            with self.subTest(payload=payload):
                self.obs.get_registration_draft = Mock(side_effect=[draft(), draft(["12345"])])
                with patch.object(requests.Session, "request", return_value=response(payload)) as request:
                    result = save_registration_draft(self.obs, ["12345"])
                self.assertEqual(result["status"], "uncertain")
                request.assert_called_once()

    def test_client_method_forwards_to_draft_only_writer(self):
        client = ObsClient(ninova_client=Mock())
        with patch("ninova_mcp.registration_write.save_registration_draft", return_value={"status": "saved"}) as write:
            self.assertEqual(client.save_registration_draft(["12345"]), {"status": "saved"})
        write.assert_called_once_with(client, ["12345"])


if __name__ == "__main__":
    unittest.main()
