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
