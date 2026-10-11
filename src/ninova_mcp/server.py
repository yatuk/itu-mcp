"""Public surface of the İTÜ MCP server.

Nothing is implemented here. This module puts the pieces together and
re-exports the names other code relies on: the ``itu-mcp`` entry point
(``ninova_mcp.server:main``), ``remote.py``, ``scripts/build_mcpb.py`` and the
tests all import from ``ninova_mcp.server``.

Where things live:

- ``server_info``: server name, version and the instructions text
- ``tool_metadata``: name, title, description and input schema of each tool
- ``tool_registry``: the full ``TOOLS`` list and the tool name sets
- ``app_base``: shared state and the lazy clients
- ``app_*``: one mixin of tool methods per domain
- ``app``: ``NinovaMcpApp``, composed from the mixins
- ``server_build``: registration on FastMCP and the stdio server builder

A new feature should follow ``archive_tools``, ``seat_watch`` and
``calendar_export``: its own module with a mixin and a metadata list, wired in
through ``app`` and ``tool_registry``.

The client classes, ``load_ninova_env`` and ``match_upload_slot`` are imported
here because the code that uses them reads them from this module when it runs
(see ``app_base.server_namespace``). Patching one of them on
``ninova_mcp.server`` therefore still changes what the app does. The other
names below are plain re-exports: patch those where they are defined.
"""

from __future__ import annotations

import argparse

from . import toolsets as _toolsets
from .app import NinovaMcpApp
from .app_base import COURSES_CACHE_KEY, DEFAULT_COURSE_CACHE_TTL_SECONDS
from .app_ninova import MAX_UPLOAD_BYTES
from .app_registration import (
    RegistrationDraftCrns,
    _confirm_draft_write,  # noqa: F401
    obs_registration_writes_enabled,
)
from .archive_client import ItuArchiveClient
from .client import NinovaClient
from .community_data import CrossCheckDataClient
from .env import load_ninova_env
from .library_client import LibraryClient
from .mail_client import ItuMailClient
from .obs_client import ObsClient, ObsPublicClient
from .parsing import match_upload_slot, pretty_json
from .prompts import PROMPT_NAMES
from .public_client import ItuPublicClient
from .public_resources import PUBLIC_RESOURCE_URIS, RESOURCE_TEMPLATE_URIS
from .resources import RESOURCE_URIS
from .server_build import (
    apply_server_version,
    build_stdio_server,
    register_prompts,
    register_resources,
    register_tools,
)
from .server_info import SERVER_INSTRUCTIONS, SERVER_NAME, SERVER_VERSION
from .tool_registry import (
    DESTRUCTIVE_TOOL_NAMES,
    LOCAL_TOOL_NAMES,
    MAIL_TOOL_NAMES,
    OBS_REGISTRATION_WRITE_TOOL_NAMES,
    REMOTE_EXCLUDED_TOOLS,
    REMOTE_TOOL_NAMES,
    STATEFUL_TOOL_NAMES,
    TOOLS,
)

__all__ = [
    "COURSES_CACHE_KEY",
    "CrossCheckDataClient",
    "DEFAULT_COURSE_CACHE_TTL_SECONDS",
    "DESTRUCTIVE_TOOL_NAMES",
    "ItuArchiveClient",
    "ItuMailClient",
    "ItuPublicClient",
    "LOCAL_TOOL_NAMES",
    "LibraryClient",
    "MAIL_TOOL_NAMES",
    "MAX_UPLOAD_BYTES",
    "NinovaClient",
    "NinovaMcpApp",
    "OBS_REGISTRATION_WRITE_TOOL_NAMES",
    "ObsClient",
    "ObsPublicClient",
    "REMOTE_EXCLUDED_TOOLS",
    "REMOTE_TOOL_NAMES",
    "RegistrationDraftCrns",
    "SERVER_INSTRUCTIONS",
    "SERVER_NAME",
    "SERVER_VERSION",
    "STATEFUL_TOOL_NAMES",
    "TOOLS",
    "apply_server_version",
    "build_arg_parser",
    "build_stdio_server",
    "load_ninova_env",
    "main",
    "match_upload_slot",
    "obs_registration_writes_enabled",
    "register_prompts",
    "register_resources",
    "register_tools",
]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="itu-mcp",
        description=(
            "MCP server for İTÜ Ninova (LMS) and OBS (student portal). "
            "With no arguments, starts the local stdio MCP server "
            "(used by Claude Desktop, Claude Code, Cursor, Codex, etc.)."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"{SERVER_NAME} {SERVER_VERSION}",
    )
    parser.add_argument(
        "--check-auth",
        action="store_true",
        help="Load credentials, attempt a Ninova login, print JSON status, and exit.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print registered local tool names and exit.",
    )
    parser.add_argument(
        "--list-toolsets",
        action="store_true",
        help="Print tool groups with tool counts and on/off state, and exit.",
    )
    parser.add_argument(
        "--list-prompts",
        action="store_true",
        help="Print registered prompt names and exit.",
    )
    parser.add_argument(
        "--list-resources",
        action="store_true",
        help="Print registered resource URIs and exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint. No args → stdio MCP server (MCP clients)."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.list_tools or args.list_toolsets:
        try:
            lines = (
                _toolsets.describe_toolsets(LOCAL_TOOL_NAMES)
                if args.list_toolsets
                else _toolsets.local_tool_names(LOCAL_TOOL_NAMES)
            )
        except _toolsets.ToolsetError as exc:
            parser.exit(2, f"error: {exc}\n")
        for name in lines:
            print(name)
        return

    if args.list_prompts:
        for name in PROMPT_NAMES:
            print(name)
        return

    if args.list_resources:
        for uri in RESOURCE_URIS:
            print(uri)
        for uri in [*PUBLIC_RESOURCE_URIS, *RESOURCE_TEMPLATE_URIS]:
            print(uri)
        return

    if args.check_auth:
        status = NinovaMcpApp().auth_status()
        print(pretty_json(status))
        raise SystemExit(0 if status.get("authenticated") else 1)

    try:
        server = build_stdio_server()
    except _toolsets.ToolsetError as exc:
        parser.exit(2, f"error: {exc}\n")
    server.run()


if __name__ == "__main__":
    main()
