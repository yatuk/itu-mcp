"""API key auth and simple rate limiting for the remote HTTP transport."""

from __future__ import annotations

import os
import secrets
import time
from collections import defaultdict, deque
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def configured_api_key() -> str | None:
    key = (os.getenv("NINOVA_REMOTE_API_KEY") or "").strip()
    return key or None


def api_key_required() -> bool:
    """If a key is configured, MCP endpoints require it.

    Set NINOVA_REMOTE_REQUIRE_API_KEY=1 to refuse starting without a key
    (enforced by remote.main / build_app callers if desired).
    """
    return configured_api_key() is not None


def extract_api_key(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        # Some clients send raw token.
        if len(parts) == 1:
            return parts[0].strip()
    header_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    if header_key:
        return header_key.strip()
    # Bearer token (Authorization header)
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def api_key_matches(provided: str | None, expected: str | None) -> bool:
    if not expected:
        return True
    if not provided:
        return False
    return secrets.compare_digest(provided, expected)


class RateLimiter:
    """In-memory sliding-window rate limiter (single process)."""

    # A key just touched by allow() always ends up with a fresh, non-empty
    # bucket, so per-key self-cleanup can't catch keys that go quiet — only a
    # periodic sweep across all keys can. Amortized by running it every N
    # calls rather than on every one.
    _PRUNE_EVERY = 128

    def __init__(self, *, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max(1, int(max_requests))
        self.window_seconds = max(1.0, float(window_seconds))
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._calls_since_prune = 0

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        window_start = now - self.window_seconds
        bucket = self._hits[key]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            retry_after = int(max(1.0, self.window_seconds - (now - bucket[0])))
            self._maybe_prune()
            return False, retry_after
        bucket.append(now)
        self._maybe_prune()
        return True, 0

    def _maybe_prune(self) -> None:
        self._calls_since_prune += 1
        if self._calls_since_prune < self._PRUNE_EVERY:
            return
        self._calls_since_prune = 0
        self.prune_empty()

    def prune_empty(self) -> None:
        """Drop dict entries for clients whose bucket has fully expired.

        Every distinct client key (IP, or a forwarded-for value) that ever
        calls ``allow`` creates a dict entry that would otherwise never be
        removed, even long after that client stops connecting — trivially
        amplified by an unvalidated spoofed forwarded-for header (see
        ``_client_key``).
        """
        now = time.monotonic()
        window_start = now - self.window_seconds
        stale = [
            key
            for key, bucket in self._hits.items()
            if not bucket or bucket[-1] < window_start
        ]
        for key in stale:
            del self._hits[key]


def build_rate_limiter_from_env() -> RateLimiter | None:
    if _env_flag("NINOVA_REMOTE_DISABLE_RATE_LIMIT", default=False):
        return None
    max_requests = int(os.getenv("NINOVA_REMOTE_RATE_LIMIT", "60"))
    window = float(os.getenv("NINOVA_REMOTE_RATE_WINDOW_SECONDS", "60"))
    if max_requests <= 0:
        return None
    return RateLimiter(max_requests=max_requests, window_seconds=window)


class RemoteSecurityMiddleware(BaseHTTPMiddleware):
    """Protect MCP mount path with optional API key + rate limit.

    Public by default:
    - GET /
    - GET /healthz

    Everything else under the MCP path (and optionally all non-public routes)
    requires the API key when NINOVA_REMOTE_API_KEY is set.
    """

    def __init__(
        self,
        app,
        *,
        mcp_mount_path: str,
        api_key: str | None,
        rate_limiter: RateLimiter | None,
        protect_all: bool = False,
    ) -> None:
        super().__init__(app)
        self.mcp_mount_path = mcp_mount_path.rstrip("/") or "/mcp"
        self.api_key = api_key
        self.rate_limiter = rate_limiter
        self.protect_all = protect_all
        self.public_paths = {"/", "/healthz"}

    def _client_key(self, request: Request) -> str:
        # X-Forwarded-For is caller-supplied and unverifiable unless the
        # server actually sits behind a proxy that sets it — trusting it by
        # default lets a client rotate the header to bypass rate limiting
        # entirely, and inflates the limiter's key space without bound.
        if _env_flag("NINOVA_REMOTE_TRUST_PROXY_HEADERS", default=False):
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",", 1)[0].strip()
        if request.client:
            return request.client.host or "unknown"
        return "unknown"

    def _is_mcp_path(self, path: str) -> bool:
        base = self.mcp_mount_path
        return path == base or path.startswith(base + "/")

    def _needs_api_key(self, path: str) -> bool:
        if not self.api_key:
            return False
        if path in self.public_paths:
            return False
        if self.protect_all:
            return True
        return self._is_mcp_path(path)

    def _needs_rate_limit(self, path: str) -> bool:
        if self.rate_limiter is None:
            return False
        if path in self.public_paths:
            return False
        return self._is_mcp_path(path) or self.protect_all

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        if self._needs_rate_limit(path):
            limiter = self.rate_limiter
            if limiter is None:  # defensive: settings may change between checks
                return await call_next(request)
            allowed, retry_after = limiter.allow(self._client_key(request))
            if not allowed:
                return JSONResponse(
                    {
                        "error": "rate_limit_exceeded",
                        "message": "Too many requests. Slow down and retry.",
                        "retry_after_seconds": retry_after,
                    },
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )

        if self._needs_api_key(path):
            provided = extract_api_key(request)
            if not api_key_matches(provided, self.api_key):
                return JSONResponse(
                    {
                        "error": "unauthorized",
                        "message": (
                            "Missing or invalid API key. Send "
                            "Authorization: Bearer <NINOVA_REMOTE_API_KEY> "
                            "or X-API-Key header."
                        ),
                    },
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )

        return await call_next(request)
