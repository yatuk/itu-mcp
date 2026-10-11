from __future__ import annotations

import contextlib
import ipaddress
import os
import secrets
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import anyio
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .env import load_ninova_env
from .public_resources import register_public_resources
from .remote_security import (
    RateLimiter,
    RemoteConfigError,
    RemoteSecurityMiddleware,
    build_rate_limiter_from_env,
    configured_api_key,
    trusted_proxy_hops_from_env,
    validate_mcp_mount_path,
)
from .server_build import ToolCallGate
from .tool_registry import DESTRUCTIVE_TOOL_NAMES
from .toolsets import filter_tool_names
from .server import (
    MAIL_TOOL_NAMES,
    OBS_REGISTRATION_WRITE_TOOL_NAMES,
    REMOTE_TOOL_NAMES,
    SERVER_INSTRUCTIONS,
    SERVER_NAME,
    NinovaMcpApp,
    apply_server_version,
    register_prompts,
    register_resources,
    register_tools,
)

# Tools that change the student's data. They are never served over HTTP, no
# matter what any environment variable says.
NEVER_REMOTE_TOOL_NAMES = frozenset(DESTRUCTIVE_TOOL_NAMES | OBS_REGISTRATION_WRITE_TOOL_NAMES)

_LOOPBACK_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_LOOPBACK_ALLOWED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
_LOG_PREFIX = "[itu-mcp-remote]"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _selected_remote_tool_names() -> list[str]:
    # REMOTE_TOOL_NAMES already leaves out REMOTE_EXCLUDED_TOOLS, and the write
    # tools are in that set, so today they never reach the last condition
    # below. That condition is kept on purpose as a second, unconditional
    # check: if a write tool is ever dropped from REMOTE_EXCLUDED_TOOLS by
    # mistake, it is still not served over HTTP. No variable turns it off.
    available = [
        name
        for name in REMOTE_TOOL_NAMES
        if (
            _env_flag("NINOVA_REMOTE_ENABLE_MAIL", default=False)
            or name not in MAIL_TOOL_NAMES
        ) and name not in NEVER_REMOTE_TOOL_NAMES
    ]
    # Toolset selection only narrows the list; with both variables unset it
    # returns the list unchanged. The rules above and below still apply.
    available = filter_tool_names(available)
    configured = _split_csv_env("NINOVA_REMOTE_ALLOWED_TOOLS")
    if not configured:
        return available

    unknown = sorted(set(configured) - set(available))
    if unknown:
        raise RuntimeError(
            "NINOVA_REMOTE_ALLOWED_TOOLS contains unknown, disabled, or "
            "remote-excluded tools: " + ", ".join(unknown)
        )
    allowed = set(configured)
    return [name for name in available if name in allowed]


def _is_loopback_host(host: str) -> bool:
    name = host.strip().lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name.strip("[]")).is_loopback
    except ValueError:
        return False


def _bind_host() -> str:
    return (os.getenv("NINOVA_REMOTE_HOST") or "127.0.0.1").strip()


def _bind_port() -> int:
    raw = os.getenv("PORT") or os.getenv("NINOVA_REMOTE_PORT") or "8000"
    try:
        return int(raw)
    except ValueError as exc:
        raise RemoteConfigError(
            f"PORT / NINOVA_REMOTE_PORT must be a port number. Got: {raw!r}"
        ) from exc


def _build_transport_security(host: str, warnings: list[str]) -> TransportSecuritySettings:
    """Host/Origin validation for the MCP endpoint. Never returns ``None``.

    The SDK turns DNS-rebinding protection on by itself only for a loopback
    bind and only when no settings object is passed, so an explicit one is
    always built here.
    """
    public_base_url = os.getenv("NINOVA_PUBLIC_BASE_URL")
    allowed_hosts = _split_csv_env("NINOVA_ALLOWED_HOSTS")
    allowed_origins = _split_csv_env("NINOVA_ALLOWED_ORIGINS")

    if public_base_url:
        parsed = urlparse(public_base_url)
        if parsed.netloc:
            allowed_hosts.append(parsed.netloc)
        if parsed.scheme and parsed.netloc:
            allowed_origins.append(f"{parsed.scheme}://{parsed.netloc}")

    if _env_flag("NINOVA_DISABLE_DNS_REBINDING_PROTECTION", default=False):
        warnings.append(
            "NINOVA_DISABLE_DNS_REBINDING_PROTECTION=1: the Host and Origin "
            "headers of MCP requests are NOT checked. Remove this variable and "
            "set NINOVA_ALLOWED_HOSTS or NINOVA_PUBLIC_BASE_URL instead."
        )
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    if not allowed_hosts:
        if not _is_loopback_host(host):
            raise RemoteConfigError(
                f"NINOVA_REMOTE_HOST={host} is not a loopback address and no "
                "expected Host header is configured. Set NINOVA_ALLOWED_HOSTS "
                "(for example itu-mcp.example.com) or NINOVA_PUBLIC_BASE_URL "
                "(for example https://itu-mcp.example.com). To run without "
                "Host/Origin validation anyway, set "
                "NINOVA_DISABLE_DNS_REBINDING_PROTECTION=1."
            )
        allowed_hosts = list(_LOOPBACK_ALLOWED_HOSTS)
        allowed_origins.extend(_LOOPBACK_ALLOWED_ORIGINS)
        literal = host.strip("[]")
        if literal not in {"127.0.0.1", "localhost", "::1"}:
            allowed_hosts.append(f"{host}:*")
            allowed_origins.append(f"http://{host}:*")

    allowed_origins.extend(["https://claude.ai", "https://claude.com"])
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(set(allowed_hosts)),
        allowed_origins=sorted(set(allowed_origins)),
    )


@dataclass(frozen=True)
class RemoteStartupConfig:
    """Everything decided at startup. Building it is what refuses to start."""

    host: str
    port: int
    mcp_path: str
    api_key: str | None
    api_key_generated: bool
    transport_security: TransportSecuritySettings
    warnings: tuple[str, ...]


def resolve_startup_config() -> RemoteStartupConfig:
    """Read the environment and fail closed on an unsafe combination.

    Raises ``RemoteConfigError`` naming the variable to set. See the startup
    table in docs/advanced.md.
    """
    warnings: list[str] = []
    host = _bind_host()
    port = _bind_port()
    loopback = _is_loopback_host(host)
    mcp_path = validate_mcp_mount_path(os.getenv("NINOVA_REMOTE_MCP_PATH"))

    api_key = configured_api_key()
    generated = False
    require_key = _env_flag("NINOVA_REMOTE_REQUIRE_API_KEY", default=False)
    allow_open = _env_flag("NINOVA_REMOTE_ALLOW_UNAUTHENTICATED", default=False)
    if api_key is None:
        if require_key:
            raise RemoteConfigError(
                "NINOVA_REMOTE_REQUIRE_API_KEY=1 but NINOVA_REMOTE_API_KEY is not "
                "set. Set NINOVA_REMOTE_API_KEY to a long random value, for "
                "example the output of: openssl rand -hex 32"
            )
        if allow_open:
            warnings.append(
                "NINOVA_REMOTE_ALLOW_UNAUTHENTICATED=1 and no NINOVA_REMOTE_API_KEY: "
                f"ANYONE who can reach {host}:{port} can read this student's "
                "grades, schedule, profile and tickets WITHOUT a key. Set "
                "NINOVA_REMOTE_API_KEY and remove this variable."
            )
        elif loopback:
            # Local testing keeps working, but not as an open endpoint: any
            # other process or user on this machine would otherwise be able
            # to call it.
            api_key = secrets.token_urlsafe(32)
            generated = True
        else:
            raise RemoteConfigError(
                f"NINOVA_REMOTE_HOST={host} is not a loopback address and "
                "NINOVA_REMOTE_API_KEY is not set. Set NINOVA_REMOTE_API_KEY to a "
                "long random value, for example the output of: openssl rand -hex 32. "
                "To run an open, unauthenticated endpoint anyway, set "
                "NINOVA_REMOTE_ALLOW_UNAUTHENTICATED=1."
            )
    elif len(api_key) < 16:
        warnings.append(
            "NINOVA_REMOTE_API_KEY is shorter than 16 characters and can be "
            "guessed. Use a long random value, for example: openssl rand -hex 32"
        )

    transport_security = _build_transport_security(host, warnings)
    return RemoteStartupConfig(
        host=host,
        port=port,
        mcp_path=mcp_path,
        api_key=api_key,
        api_key_generated=generated,
        transport_security=transport_security,
        warnings=tuple(warnings),
    )


def _announce(config: RemoteStartupConfig, *, rate_limited: bool) -> None:
    """Print the startup summary to stderr. Status routes no longer carry it."""

    def say(text: str) -> None:
        print(f"{_LOG_PREFIX} {text}", file=sys.stderr, flush=True)

    for warning in config.warnings:
        say("=" * 70)
        say("WARNING: " + warning)
        say("=" * 70)
    if config.api_key_generated:
        say("NINOVA_REMOTE_API_KEY is not set. One-time API key for this run:")
        say(f"    {config.api_key}")
        say("Send it as 'Authorization: Bearer <key>' or 'X-API-Key: <key>'.")
        say("It changes on every start. Set NINOVA_REMOTE_API_KEY to keep one key.")
    security = config.transport_security
    say(
        f"listening on {config.host}:{config.port}, "
        f"api key {'required' if config.api_key else 'NOT REQUIRED'}, "
        f"rate limit {'on' if rate_limited else 'OFF'}, "
        "host/origin check "
        + (
            "on for " + ", ".join(security.allowed_hosts)
            if security.enable_dns_rebinding_protection
            else "OFF"
        )
    )


def _build_fastmcp(
    app_logic: NinovaMcpApp,
    mount_path: str,
    *,
    transport_security: TransportSecuritySettings | None = None,
    gate: ToolCallGate | None = None,
) -> FastMCP:
    host = _bind_host()
    if transport_security is None:
        transport_security = _build_transport_security(host, [])
    tool_names = _selected_remote_tool_names()
    leaked = sorted(NEVER_REMOTE_TOOL_NAMES.intersection(tool_names))
    if leaked:  # third check, after the excluded set and the selection filter
        raise RuntimeError("Write tools must never be served remotely: " + ", ".join(leaked))
    mcp = FastMCP(
        SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        host=host,
        port=_bind_port(),
        json_response=True,
        streamable_http_path="/",
        stateless_http=False,
        transport_security=transport_security,
        website_url=os.getenv("NINOVA_PUBLIC_BASE_URL"),
    )

    apply_server_version(mcp)
    register_tools(mcp, app_logic, tool_names, gate)
    # Prompts and resources are read-only text/JSON with no filesystem or
    # write access, so unlike the excluded tools they are safe to expose on
    # the hosted transport as-is.
    register_prompts(mcp)
    register_resources(mcp)
    # Public, credential-free data read through the app's allowlisted clients.
    register_public_resources(mcp, app_logic)

    # Keep the mounted path on the instance for observability/debug logs if needed.
    mcp.mount_path = mount_path  # type: ignore[attr-defined]
    return mcp


class AuthHealthCheck:
    """The optional login check behind ``/healthz``, made cheap to probe.

    The result (success or failure) is cached, so a flood of probes cannot
    cause a flood of logins. A refresh runs in a worker thread while holding
    the tool lock: the event loop stays free and the check never overlaps a
    tool call on the shared session. While a refresh or a tool call is in
    progress, the last known result is served instead of waiting.
    """

    def __init__(
        self,
        probe: Callable[[], dict[str, Any]],
        *,
        gate: ToolCallGate,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._probe = probe
        self._gate = gate
        self._ttl = ttl_seconds
        self._value: bool | None = None
        self._checked_at = 0.0
        self._refreshing = False

    def _fresh(self) -> bool:
        return self._value is not None and self._clock() - self._checked_at < self._ttl

    def _run_probe(self) -> bool:
        try:
            return bool(self._probe().get("authenticated"))
        except Exception:
            return False

    async def authenticated(self) -> bool:
        if self._fresh():
            return bool(self._value)
        if self._value is not None and (self._refreshing or self._gate.busy()):
            return self._value
        async with self._gate.lock():
            if self._fresh():
                return bool(self._value)
            self._refreshing = True
            try:
                value = await anyio.to_thread.run_sync(self._run_probe)
            finally:
                self._refreshing = False
            self._value = value
            self._checked_at = self._clock()
        return value


def _healthz_cache_seconds() -> float:
    raw = (os.getenv("NINOVA_HEALTHZ_AUTH_CACHE_SECONDS") or "60").strip()
    try:
        return max(5.0, float(raw))
    except ValueError as exc:
        raise RemoteConfigError(
            f"NINOVA_HEALTHZ_AUTH_CACHE_SECONDS must be a number of seconds. Got: {raw!r}"
        ) from exc


class _McpPathWithoutRedirect:
    """Serve the MCP path itself instead of redirecting it to ``<path>/``.

    The router would answer ``POST /mcp`` with a 307 whose ``Location`` is
    built from the request's scheme and Host. Behind a TLS proxy that is an
    ``http://`` address, and clients drop the ``Authorization`` header when a
    redirect changes origin. Runs inside the security middleware, so the key
    and the rate limit have already been applied to the original path.
    """

    def __init__(self, app: Any, *, mcp_path: str) -> None:
        self.app = app
        self.mcp_path = mcp_path

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("path") == self.mcp_path:
            target = self.mcp_path + "/"
            scope = {**scope, "path": target, "raw_path": target.encode("ascii")}
        await self.app(scope, receive, send)


def build_app(app_logic: NinovaMcpApp | None = None) -> Starlette:
    load_ninova_env()
    config = resolve_startup_config()
    rate_limiter = build_rate_limiter_from_env()
    trusted_hops = trusted_proxy_hops_from_env()
    check_auth = _env_flag("NINOVA_HEALTHZ_CHECK_AUTH", default=False)
    cache_seconds = _healthz_cache_seconds()

    if app_logic is None:
        app_logic = NinovaMcpApp()
    gate = ToolCallGate()
    mcp = _build_fastmcp(
        app_logic,
        mount_path=config.mcp_path,
        transport_security=config.transport_security,
        gate=gate,
    )
    auth_check = AuthHealthCheck(app_logic.auth_status, gate=gate, ttl_seconds=cache_seconds)
    _announce(config, rate_limited=rate_limiter is not None)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp.session_manager.run():
            yield

    # Both status routes are served without the API key, so they say nothing
    # about the configuration: no MCP path, no state directory, no security
    # flags, and no version (a caller with the key gets it from the MCP
    # handshake; an anonymous one has no need to fingerprint the build).
    async def root(_: Request) -> JSONResponse:
        return JSONResponse({"name": SERVER_NAME, "status": "ok"})

    async def healthz(_: Request) -> JSONResponse:
        if not check_auth:
            return JSONResponse({"ok": True})
        authenticated = await auth_check.authenticated()
        return JSONResponse(
            {"ok": authenticated, "authenticated": authenticated},
            status_code=200 if authenticated else 503,
        )

    starlette_app = Starlette(
        routes=[
            Route("/", root),
            Route("/healthz", healthz),
            Mount(config.mcp_path, app=mcp.streamable_http_app()),
        ],
        lifespan=lifespan,
    )
    # Added first, so it sits inside the security middleware added below.
    starlette_app.add_middleware(_McpPathWithoutRedirect, mcp_path=config.mcp_path)
    starlette_app.add_middleware(
        RemoteSecurityMiddleware,
        api_key=config.api_key,
        rate_limiter=rate_limiter,
        status_rate_limiter=(
            RateLimiter(
                max_requests=rate_limiter.max_requests,
                window_seconds=rate_limiter.window_seconds,
            )
            if rate_limiter is not None
            else None
        ),
        trusted_proxy_hops=trusted_hops,
    )
    return starlette_app


_app: Starlette | None = None


def __getattr__(name: str) -> Any:
    # ``ninova_mcp.remote:app`` still works for ``uvicorn ninova_mcp.remote:app``,
    # but the app is built on first use. Importing this module must not start
    # anything, print a key, or fail on an unsafe configuration.
    if name == "app":
        global _app
        if _app is None:
            _app = build_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    try:
        application = build_app()
    except RemoteConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None

    uvicorn.run(
        application,
        host=_bind_host(),
        port=_bind_port(),
        reload=False,
        # X-Forwarded-For is read only by RemoteSecurityMiddleware, and only
        # with NINOVA_REMOTE_TRUST_PROXY_HEADERS. Uvicorn's own handling
        # trusts 127.0.0.1 by default, which would let a local caller pick
        # its own rate-limit bucket.
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
