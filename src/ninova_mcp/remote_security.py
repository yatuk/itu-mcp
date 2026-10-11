"""API key auth, rate limiting and path rules for the remote HTTP transport.

The middleware is default deny: the two status endpoints are the only public
routes, matched exactly on method and path. Every other request, whatever its
path, is rate limited and must carry the API key when one is configured.
"""

from __future__ import annotations

import ipaddress
import os
import re
import secrets
import time
from collections import defaultdict, deque
from typing import Any, Iterable

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketClose

# The only routes served without the API key, and only for these methods.
PUBLIC_STATUS_PATHS = frozenset({"/", "/healthz"})
PUBLIC_STATUS_METHODS = frozenset({"GET", "HEAD"})

_MOUNT_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+")


class RemoteConfigError(RuntimeError):
    """The remote server's configuration is unsafe or invalid; do not start."""


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def configured_api_key() -> str | None:
    key = (os.getenv("NINOVA_REMOTE_API_KEY") or "").strip()
    return key or None


def validate_mcp_mount_path(value: str | None, default: str = "/mcp") -> str:
    """Return the path the MCP app is mounted at, or refuse an unsafe one.

    ``None`` (variable unset) means the default. A value that is set but
    empty, the root path, a path that collides with a status route, or a path
    that the router and the security check could read differently is refused:
    the MCP app must never share a path with a public route.
    """
    if value is None:
        return default
    path = value.strip()
    if not path or not path.strip("/"):
        raise RemoteConfigError(
            "NINOVA_REMOTE_MCP_PATH must not be empty or '/': the MCP endpoint "
            "would share the path of the public status route. Unset it to use "
            f"{default}, or set it to a path such as /mcp-<long-random-text>."
        )
    if not path.startswith("/"):
        path = "/" + path
    path = path.rstrip("/")
    segments = path[1:].split("/")
    if any(
        not _MOUNT_SEGMENT.fullmatch(segment) or set(segment) == {"."}
        for segment in segments
    ):
        raise RemoteConfigError(
            "NINOVA_REMOTE_MCP_PATH may only contain letters, digits and . _ ~ - "
            "in segments separated by single slashes (no '//', '..', '%', spaces "
            f"or braces). Got: {value!r}"
        )
    first = "/" + segments[0].casefold()
    if first in PUBLIC_STATUS_PATHS:
        raise RemoteConfigError(
            f"NINOVA_REMOTE_MCP_PATH must not be {first} or a path under it: "
            "that is a public status route. Set it to a path such as "
            "/mcp-<long-random-text>."
        )
    return path


def extract_api_key(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip() or None
        # Some clients send raw token.
        if len(parts) == 1:
            return parts[0].strip() or None
    header_key = request.headers.get("x-api-key")
    if header_key:
        return header_key.strip() or None
    return None


def api_key_matches(provided: str | None, expected: str | None) -> bool:
    if not expected:
        return True
    if not provided:
        return False
    return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


class RateLimiter:
    """In-memory sliding-window rate limiter (single process)."""

    # A key just touched by allow() always ends up with a fresh, non-empty
    # bucket, so per-key self-cleanup cannot catch keys that go quiet. Only a
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

        Every distinct client key that ever calls ``allow`` creates a dict
        entry that would otherwise never be removed, even long after that
        client stops connecting.
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
    try:
        max_requests = int(os.getenv("NINOVA_REMOTE_RATE_LIMIT", "60"))
        window = float(os.getenv("NINOVA_REMOTE_RATE_WINDOW_SECONDS", "60"))
    except ValueError as exc:
        raise RemoteConfigError(
            "NINOVA_REMOTE_RATE_LIMIT and NINOVA_REMOTE_RATE_WINDOW_SECONDS "
            "must be numbers."
        ) from exc
    if max_requests <= 0:
        return None
    return RateLimiter(max_requests=max_requests, window_seconds=window)


def trusted_proxy_hops_from_env() -> int:
    """How many trusted proxies sit in front of the server.

    0 when proxy headers are not trusted. Otherwise
    NINOVA_REMOTE_TRUSTED_PROXY_HOPS, default 1.
    """
    if not _env_flag("NINOVA_REMOTE_TRUST_PROXY_HEADERS", default=False):
        return 0
    raw = (os.getenv("NINOVA_REMOTE_TRUSTED_PROXY_HOPS") or "1").strip()
    try:
        hops = int(raw)
    except ValueError:
        hops = 0
    if hops < 1:
        raise RemoteConfigError(
            "NINOVA_REMOTE_TRUSTED_PROXY_HOPS must be a whole number of 1 or "
            f"more (the number of trusted proxies in front of the server). Got: {raw!r}"
        )
    return hops


def _parse_ip(value: str) -> str | None:
    text = value.strip()
    if text.startswith("["):  # [v6] or [v6]:port
        text = text[1:].split("]", 1)[0]
    elif text.count(":") == 1:  # v4:port
        text = text.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def client_address(
    peer: str | None,
    forwarded_for: Iterable[str],
    *,
    trusted_hops: int,
) -> str:
    """The address rate limiting is keyed on.

    Each proxy appends the address it received the request from to
    ``X-Forwarded-For``, so only the rightmost ``trusted_hops`` entries were
    written by proxies the operator trusts; everything further left is
    whatever the client chose to send. With one trusted proxy the client is
    the last entry, with two it is the second from last. If the header is
    missing, too short or the entry is not an IP address, the socket peer is
    used instead.
    """
    fallback = peer or "unknown"
    if trusted_hops < 1:
        return fallback
    entries = [part for value in forwarded_for for part in value.split(",")]
    if len(entries) < trusted_hops:
        return fallback
    return _parse_ip(entries[-trusted_hops]) or fallback


class RemoteSecurityMiddleware:
    """Default-deny gate in front of the whole remote app.

    Public: ``GET``/``HEAD`` on exactly ``/`` and ``/healthz``. They need no
    key and use their own rate-limit bucket, so status probes and MCP traffic
    do not eat each other's budget.

    Everything else is rate limited and needs the API key when one is
    configured. The decision is made on ``scope["path"]``, the same decoded
    string the router matches on, and it is compared exactly: no variant of a
    path (other case, doubled or trailing slash, percent-encoding) is ever
    normalised into a public route, so no variant can be public here and
    still be routed to the MCP app.
    """

    def __init__(
        self,
        app: Any,
        *,
        api_key: str | None,
        rate_limiter: RateLimiter | None,
        status_rate_limiter: RateLimiter | None = None,
        trusted_proxy_hops: int = 0,
    ) -> None:
        self.app = app
        self.api_key = api_key
        self.rate_limiter = rate_limiter
        self.status_rate_limiter = status_rate_limiter
        self.trusted_proxy_hops = trusted_proxy_hops

    @staticmethod
    def is_public(method: str, path: str) -> bool:
        return method in PUBLIC_STATUS_METHODS and path in PUBLIC_STATUS_PATHS

    def _client_key(self, request: Request) -> str:
        peer = request.client.host if request.client else None
        # X-Forwarded-For is caller-supplied unless a proxy we trust wrote
        # it, so with no trusted hops it is not read at all.
        return client_address(
            peer,
            request.headers.getlist("x-forwarded-for"),
            trusted_hops=self.trusted_proxy_hops,
        )

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # No WebSocket route exists; refuse instead of passing it on.
            await WebSocketClose(code=1008)(scope, receive, send)
            return

        request = Request(scope)
        public = self.is_public(scope.get("method", ""), scope.get("path", ""))
        limiter = self.status_rate_limiter if public else self.rate_limiter

        if limiter is not None:
            allowed, retry_after = limiter.allow(self._client_key(request))
            if not allowed:
                response = JSONResponse(
                    {
                        "error": "rate_limit_exceeded",
                        "message": "Too many requests. Slow down and retry.",
                        "retry_after_seconds": retry_after,
                    },
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
                await response(scope, receive, send)
                return

        if not public and self.api_key:
            if not api_key_matches(extract_api_key(request), self.api_key):
                response = JSONResponse(
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
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
