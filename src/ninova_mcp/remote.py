from __future__ import annotations

import contextlib
import os
import sys
from urllib.parse import urlparse

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .env import load_ninova_env
from .remote_security import (
    RemoteSecurityMiddleware,
    api_key_required,
    build_rate_limiter_from_env,
    configured_api_key,
)
from .server import (
    REMOTE_TOOL_NAMES,
    SERVER_INSTRUCTIONS,
    SERVER_NAME,
    SERVER_VERSION,
    NinovaMcpApp,
    apply_server_version,
    register_tools,
)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _normalize_mount_path(value: str | None, default: str) -> str:
    path = (value or default).strip()
    if not path:
        return default
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def _build_transport_security() -> TransportSecuritySettings | None:
    public_base_url = os.getenv("NINOVA_PUBLIC_BASE_URL")
    allowed_hosts = _split_csv_env("NINOVA_ALLOWED_HOSTS")
    allowed_origins = _split_csv_env("NINOVA_ALLOWED_ORIGINS")

    if public_base_url:
        parsed = urlparse(public_base_url)
        if parsed.netloc:
            allowed_hosts.append(parsed.netloc)
        if parsed.scheme and parsed.netloc:
            allowed_origins.append(f"{parsed.scheme}://{parsed.netloc}")

    allowed_origins.extend(["https://claude.ai", "https://claude.com"])
    allowed_hosts = sorted(set(allowed_hosts))
    allowed_origins = sorted(set(allowed_origins))

    if not allowed_hosts:
        if _env_flag("NINOVA_DISABLE_DNS_REBINDING_PROTECTION", default=False):
            return TransportSecuritySettings(enable_dns_rebinding_protection=False)
        return None

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=not _env_flag(
            "NINOVA_DISABLE_DNS_REBINDING_PROTECTION",
            default=False,
        ),
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def _build_fastmcp(app_logic: NinovaMcpApp, mount_path: str) -> FastMCP:
    security_settings = _build_transport_security()
    host = os.getenv("NINOVA_REMOTE_HOST", "127.0.0.1")
    port = int(os.getenv("PORT") or os.getenv("NINOVA_REMOTE_PORT") or "8000")
    mcp = FastMCP(
        SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        host=host,
        port=port,
        json_response=True,
        streamable_http_path="/",
        stateless_http=False,
        transport_security=security_settings,
        website_url=os.getenv("NINOVA_PUBLIC_BASE_URL"),
    )

    apply_server_version(mcp)
    register_tools(mcp, app_logic, REMOTE_TOOL_NAMES)

    # Keep the mounted path on the instance for observability/debug logs if needed.
    mcp.mount_path = mount_path  # type: ignore[attr-defined]
    return mcp


def build_app() -> Starlette:
    load_ninova_env()

    if _env_flag("NINOVA_REMOTE_REQUIRE_API_KEY", default=False) and not configured_api_key():
        raise RuntimeError(
            "NINOVA_REMOTE_REQUIRE_API_KEY=1 but NINOVA_REMOTE_API_KEY is not set."
        )

    app_logic = NinovaMcpApp()
    mcp_mount_path = _normalize_mount_path(os.getenv("NINOVA_REMOTE_MCP_PATH"), "/mcp")
    mcp = _build_fastmcp(app_logic, mount_path=mcp_mount_path)
    api_key = configured_api_key()
    rate_limiter = build_rate_limiter_from_env()

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp.session_manager.run():
            yield

    async def root(_: Request) -> JSONResponse:
        security = mcp.settings.transport_security
        return JSONResponse(
            {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
                "transport": "streamable-http",
                "status": "ok",
                "mcp_path": mcp_mount_path,
                "api_key_required": api_key_required(),
                "rate_limit_enabled": rate_limiter is not None,
                "dns_rebinding_protection_enabled": bool(
                    security and security.enable_dns_rebinding_protection
                ),
            }
        )

    async def healthz(_: Request) -> JSONResponse:
        # Lightweight: do not force a full Ninova login on every probe unless asked.
        if _env_flag("NINOVA_HEALTHZ_CHECK_AUTH", default=False):
            status = app_logic.auth_status()
            return JSONResponse(
                {
                    "ok": bool(status.get("authenticated")),
                    "credentials_present": bool(status.get("credentials_present")),
                    "authenticated": bool(status.get("authenticated")),
                    "state_dir": status.get("state_dir"),
                    "api_key_required": api_key_required(),
                },
                status_code=200 if status.get("authenticated") else 503,
            )
        credentials_present = bool(os.getenv("NINOVA_USERNAME") and os.getenv("NINOVA_PASSWORD"))
        return JSONResponse(
            {
                "ok": True,
                "credentials_present": credentials_present,
                "api_key_required": api_key_required(),
                "rate_limit_enabled": rate_limiter is not None,
            }
        )

    starlette_app = Starlette(
        routes=[
            Route("/", root),
            Route("/healthz", healthz),
            Mount(mcp_mount_path, app=mcp.streamable_http_app()),
        ],
        lifespan=lifespan,
    )
    starlette_app.add_middleware(
        RemoteSecurityMiddleware,
        mcp_mount_path=mcp_mount_path,
        api_key=api_key,
        rate_limiter=rate_limiter,
        protect_all=_env_flag("NINOVA_REMOTE_PROTECT_ALL", default=False),
    )
    return starlette_app


app = build_app()


def main() -> None:
    load_ninova_env()
    if _env_flag("NINOVA_REMOTE_REQUIRE_API_KEY", default=False) and not configured_api_key():
        print(
            "error: NINOVA_REMOTE_REQUIRE_API_KEY=1 requires NINOVA_REMOTE_API_KEY",
            file=sys.stderr,
        )
        raise SystemExit(2)

    host = os.getenv("NINOVA_REMOTE_HOST", "127.0.0.1")
    port = int(os.getenv("PORT") or os.getenv("NINOVA_REMOTE_PORT") or "8000")
    uvicorn.run(
        "ninova_mcp.remote:app",
        host=host,
        port=port,
        reload=False,
        factory=False,
    )


if __name__ == "__main__":
    main()
