from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from ninova_mcp.remote_security import (
    RateLimiter,
    RemoteConfigError,
    RemoteSecurityMiddleware,
    api_key_matches,
    client_address,
    extract_api_key,
    trusted_proxy_hops_from_env,
)


class RemoteSecurityUnitTests(unittest.TestCase):
    def test_api_key_matches(self) -> None:
        self.assertTrue(api_key_matches(None, None))
        self.assertTrue(api_key_matches("secret", "secret"))
        self.assertFalse(api_key_matches("wrong", "secret"))
        self.assertFalse(api_key_matches(None, "secret"))

    def test_rate_limiter(self) -> None:
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        self.assertTrue(limiter.allow("ip")[0])
        self.assertTrue(limiter.allow("ip")[0])
        allowed, retry = limiter.allow("ip")
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry, 1)

    def test_prune_empty_drops_fully_expired_client_keys(self) -> None:
        """Without pruning, every distinct client key ever seen (including a
        spoofed X-Forwarded-For value) leaves a permanent dict entry.

        window_seconds is clamped to a 1s minimum by the constructor, so
        time.monotonic is faked forward past the window instead of actually
        sleeping.
        """
        from unittest.mock import patch

        limiter = RateLimiter(max_requests=5, window_seconds=1)
        with patch("ninova_mcp.remote_security.time.monotonic", return_value=1000.0):
            limiter.allow("stale-client")
        with patch("ninova_mcp.remote_security.time.monotonic", return_value=1002.0):
            limiter.prune_empty()
        self.assertNotIn("stale-client", limiter._hits)

    def test_prune_empty_keeps_active_client_keys(self) -> None:
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        limiter.allow("active-client")
        limiter.prune_empty()
        self.assertIn("active-client", limiter._hits)

    def test_allow_prunes_periodically_on_its_own(self) -> None:
        from unittest.mock import patch

        limiter = RateLimiter(max_requests=1000, window_seconds=1)
        with patch("ninova_mcp.remote_security.time.monotonic", return_value=1000.0):
            limiter.allow("stale-client")
        # Enough further calls (from other keys, now safely past the window)
        # to cross the periodic prune threshold without calling
        # prune_empty() directly.
        with patch("ninova_mcp.remote_security.time.monotonic", return_value=1002.0):
            for i in range(RateLimiter._PRUNE_EVERY + 1):
                limiter.allow(f"other-{i}")
        self.assertNotIn("stale-client", limiter._hits)

    def test_forwarded_for_is_ignored_by_default(self) -> None:
        """X-Forwarded-For is caller-supplied and unverifiable unless the
        server is actually behind a trusted proxy; trusting it by default
        both lets a client rotate the header to dodge rate limiting and
        inflates the key space of the limiter without bound.
        """
        self.assertEqual(
            client_address("203.0.113.9", ["198.51.100.1"], trusted_hops=0),
            "203.0.113.9",
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NINOVA_REMOTE_TRUST_PROXY_HEADERS", None)
            os.environ["NINOVA_REMOTE_TRUSTED_PROXY_HOPS"] = "3"
            self.assertEqual(trusted_proxy_hops_from_env(), 0)

    def test_forwarded_for_client_is_taken_from_the_right(self) -> None:
        """The proxy appends the address it saw, so with one trusted proxy
        the client is the LAST entry. The first entry is whatever the client
        sent and must not pick the rate-limit bucket.
        """
        peer = "10.0.0.1"  # the proxy
        self.assertEqual(
            client_address(peer, ["6.6.6.6, 198.51.100.1"], trusted_hops=1),
            "198.51.100.1",
        )
        # Rotating the spoofed part does not change the key.
        keys = {
            client_address(peer, [f"1.2.3.{i}, 198.51.100.1"], trusted_hops=1)
            for i in range(20)
        }
        self.assertEqual(keys, {"198.51.100.1"})

    def test_forwarded_for_skips_the_configured_number_of_hops(self) -> None:
        header = ["6.6.6.6, 198.51.100.1, 10.0.0.2"]  # client, then proxy 1 seen by proxy 2
        self.assertEqual(client_address("10.0.0.3", header, trusted_hops=2), "198.51.100.1")
        self.assertEqual(client_address("10.0.0.3", header, trusted_hops=1), "10.0.0.2")
        # Several header lines are one list.
        split = ["6.6.6.6", "198.51.100.1", "10.0.0.2"]
        self.assertEqual(client_address("10.0.0.3", split, trusted_hops=2), "198.51.100.1")

    def test_forwarded_for_falls_back_to_the_socket_peer(self) -> None:
        peer = "10.0.0.1"
        cases = [
            ([], 1),                      # header missing
            ([""], 1),                    # empty
            (["198.51.100.1"], 2),        # shorter than the trusted hops
            (["198.51.100.1, garbage"], 1),
            (["198.51.100.1, "], 1),
            (["198.51.100.1, 999.1.1.1"], 1),
            (["198.51.100.1, <script>"], 1),
        ]
        for header, hops in cases:
            with self.subTest(header=header, hops=hops):
                self.assertEqual(client_address(peer, header, trusted_hops=hops), peer)
        self.assertEqual(client_address(None, [], trusted_hops=1), "unknown")

    def test_forwarded_for_accepts_ports_and_ipv6(self) -> None:
        self.assertEqual(client_address("p", ["198.51.100.1:4711"], trusted_hops=1), "198.51.100.1")
        self.assertEqual(client_address("p", ["[2001:db8::1]:4711"], trusted_hops=1), "2001:db8::1")
        self.assertEqual(client_address("p", ["2001:DB8::1"], trusted_hops=1), "2001:db8::1")

    def test_trusted_hops_default_and_validation(self) -> None:
        with patch.dict(os.environ, {"NINOVA_REMOTE_TRUST_PROXY_HEADERS": "1"}):
            os.environ.pop("NINOVA_REMOTE_TRUSTED_PROXY_HOPS", None)
            self.assertEqual(trusted_proxy_hops_from_env(), 1)
            os.environ["NINOVA_REMOTE_TRUSTED_PROXY_HOPS"] = "2"
            self.assertEqual(trusted_proxy_hops_from_env(), 2)
            for bad in ("0", "-1", "x", "1.5"):
                os.environ["NINOVA_REMOTE_TRUSTED_PROXY_HOPS"] = bad
                with self.subTest(bad=bad), self.assertRaises(RemoteConfigError):
                    trusted_proxy_hops_from_env()

    def test_extract_api_key_headers(self) -> None:
        async def run() -> None:
            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/",
                "raw_path": b"/",
                "query_string": b"",
                "headers": [(b"authorization", b"Bearer tok123")],
                "client": ("127.0.0.1", 123),
                "server": ("test", 80),
            }
            request = Request(scope)
            self.assertEqual(extract_api_key(request), "tok123")

        asyncio.run(run())


class RemoteSecurityMiddlewareTests(unittest.TestCase):
    def _app(
        self,
        *,
        api_key: str | None,
        rate_limit: int | None = None,
        trusted_proxy_hops: int = 0,
    ) -> TestClient:
        async def ok(_: Request) -> PlainTextResponse:
            return PlainTextResponse("ok")

        async def health(_: Request) -> JSONResponse:
            return JSONResponse({"ok": True})

        app = Starlette(
            routes=[
                Route("/", ok),
                Route("/healthz", health),
                Route("/mcp", ok),
                Route("/mcp/messages", ok),
            ]
        )
        limiter = RateLimiter(max_requests=rate_limit, window_seconds=60) if rate_limit else None
        app.add_middleware(
            RemoteSecurityMiddleware,
            api_key=api_key,
            rate_limiter=limiter,
            trusted_proxy_hops=trusted_proxy_hops,
        )
        return TestClient(app)

    def test_public_health_without_key(self) -> None:
        client = self._app(api_key="secret")
        response = client.get("/healthz")
        self.assertEqual(response.status_code, 200)

    def test_mcp_requires_key(self) -> None:
        client = self._app(api_key="secret")
        denied = client.get("/mcp")
        self.assertEqual(denied.status_code, 401)
        allowed = client.get("/mcp", headers={"Authorization": "Bearer secret"})
        self.assertEqual(allowed.status_code, 200)
        allowed2 = client.get("/mcp", headers={"X-API-Key": "secret"})
        self.assertEqual(allowed2.status_code, 200)

    def test_rate_limit(self) -> None:
        client = self._app(api_key=None, rate_limit=2)
        self.assertEqual(client.get("/mcp").status_code, 200)
        self.assertEqual(client.get("/mcp").status_code, 200)
        limited = client.get("/mcp")
        self.assertEqual(limited.status_code, 429)
        self.assertIn("retry_after_seconds", limited.json())

    def test_everything_but_the_status_routes_needs_the_key(self) -> None:
        client = self._app(api_key="secret")
        for path in ("/mcp/messages", "/nope", "/healthz/x", "/MCP"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 401)
        self.assertEqual(client.post("/healthz").status_code, 401)
        self.assertEqual(client.get("/").status_code, 200)

    def test_empty_or_wrong_scheme_keys_are_refused(self) -> None:
        client = self._app(api_key="secret")
        for headers in ({"Authorization": "Bearer "}, {"X-API-Key": " "}, {"Authorization": "Basic secret"}):
            with self.subTest(headers=headers):
                self.assertEqual(client.get("/mcp", headers=headers).status_code, 401)

    def test_spoofed_forwarded_for_does_not_dodge_the_rate_limit(self) -> None:
        client = self._app(api_key=None, rate_limit=2, trusted_proxy_hops=1)
        codes = [
            # The socket peer plays the proxy that appended the real client.
            client.get("/mcp", headers={"X-Forwarded-For": f"1.2.3.{i}, 198.51.100.7"}).status_code
            for i in range(4)
        ]
        self.assertEqual(codes, [200, 200, 429, 429])
        # A different real client has its own bucket.
        other = client.get("/mcp", headers={"X-Forwarded-For": "1.2.3.4, 198.51.100.8"})
        self.assertEqual(other.status_code, 200)

    def test_websocket_is_refused(self) -> None:
        from starlette.websockets import WebSocketDisconnect

        client = self._app(api_key=None)
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("/mcp"):
                pass


if __name__ == "__main__":
    unittest.main()
