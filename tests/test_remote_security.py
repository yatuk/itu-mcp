from __future__ import annotations

import asyncio
import unittest

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from ninova_mcp.remote_security import (
    RateLimiter,
    RemoteSecurityMiddleware,
    api_key_matches,
    extract_api_key,
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
        inflates the limiter's key space without bound.
        """
        from ninova_mcp.remote_security import RemoteSecurityMiddleware

        middleware = RemoteSecurityMiddleware.__new__(RemoteSecurityMiddleware)

        class _FakeClient:
            host = "203.0.113.9"

        class _FakeRequest:
            headers = {"x-forwarded-for": "198.51.100.1"}
            client = _FakeClient()

        self.assertEqual(middleware._client_key(_FakeRequest()), "203.0.113.9")

    def test_forwarded_for_is_used_when_trusted(self) -> None:
        import os
        from unittest.mock import patch

        from ninova_mcp.remote_security import RemoteSecurityMiddleware

        middleware = RemoteSecurityMiddleware.__new__(RemoteSecurityMiddleware)

        class _FakeClient:
            host = "203.0.113.9"

        class _FakeRequest:
            headers = {"x-forwarded-for": "198.51.100.1, 10.0.0.1"}
            client = _FakeClient()

        with patch.dict(os.environ, {"NINOVA_REMOTE_TRUST_PROXY_HEADERS": "1"}):
            self.assertEqual(middleware._client_key(_FakeRequest()), "198.51.100.1")

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
    def _app(self, *, api_key: str | None, rate_limit: int | None = None) -> TestClient:
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
            mcp_mount_path="/mcp",
            api_key=api_key,
            rate_limiter=limiter,
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


if __name__ == "__main__":
    unittest.main()
