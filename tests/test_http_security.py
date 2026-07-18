from __future__ import annotations

import unittest

import requests

from ninova_mcp.http_security import request_with_safe_redirects
from ninova_mcp.library_client import LibraryClient, LibraryError
from ninova_mcp.obs_client import ObsError, ObsPublicClient
from ninova_mcp.public_client import ItuPublicClient, ItuPublicError


def response(url: str, status: int = 200, location: str | None = None) -> requests.Response:
    item = requests.Response()
    item.url = url
    item.status_code = status
    item._content = b"ok"
    item._content_consumed = True
    if location is not None:
        item.headers["Location"] = location
    return item


class FakeSession:
    def __init__(self, responses: list[requests.Response]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> requests.Response:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class RedirectSecurityTests(unittest.TestCase):
    @staticmethod
    def validate_example(url: str) -> None:
        if not url.startswith("https://safe.example/"):
            raise ValueError("blocked")

    def test_redirect_target_is_rejected_before_second_request(self) -> None:
        session = FakeSession([
            response("https://safe.example/start", 302, "http://127.0.0.1/private")
        ])
        with self.assertRaises(ValueError):
            request_with_safe_redirects(
                session,  # type: ignore[arg-type]
                "GET",
                "https://safe.example/start",
                validate_url=self.validate_example,
            )
        self.assertEqual(len(session.calls), 1)

    def test_safe_redirect_is_followed_and_post_becomes_get(self) -> None:
        session = FakeSession([
            response("https://safe.example/start", 302, "/result"),
            response("https://safe.example/result"),
        ])
        result = request_with_safe_redirects(
            session,  # type: ignore[arg-type]
            "POST",
            "https://safe.example/start",
            validate_url=self.validate_example,
            data={"secret": "value"},
        )
        self.assertEqual(result.url, "https://safe.example/result")
        self.assertEqual([call[0] for call in session.calls], ["POST", "GET"])
        self.assertNotIn("data", session.calls[1][2])
        self.assertEqual(len(result.history), 1)

    def test_public_clients_reject_lookalike_hosts(self) -> None:
        with self.assertRaises(ItuPublicError):
            ItuPublicClient()._validate_url("https://obs.itu.edu.tr.attacker.example/")
        with self.assertRaises(LibraryError):
            LibraryClient(base_url="https://divit.library.itu.edu.tr.attacker.example")
        with self.assertRaises(ObsError):
            ObsPublicClient(base_url="https://obs.itu.edu.tr.attacker.example")


if __name__ == "__main__":
    unittest.main()
