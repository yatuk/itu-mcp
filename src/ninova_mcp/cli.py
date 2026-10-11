"""Command line entry point: the ``itu-mcp`` flags and the stdio server start."""

from __future__ import annotations

import argparse

from . import cli_setup, diagnostics
from . import toolsets as _toolsets
from .app import NinovaMcpApp
from .cli_messages import DEFAULT_LANGUAGE, LANGUAGES
from .parsing import pretty_json
from .prompts import PROMPT_NAMES
from .public_resources import PUBLIC_RESOURCE_URIS, RESOURCE_TEMPLATE_URIS
from .resources import RESOURCE_URIS
from .server_build import build_stdio_server
from .server_info import SERVER_NAME, SERVER_VERSION
from .tool_registry import LOCAL_TOOL_NAMES


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
    _add_commands(parser)
    return parser


def _add_commands(parser: argparse.ArgumentParser) -> None:
    """``setup``, ``connect``, ``doctor`` and ``logout``. None is required:
    with no command the stdio server starts, as MCP clients expect."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--lang",
        choices=LANGUAGES,
        default=DEFAULT_LANGUAGE,
        help="Output language (default: tr).",
    )
    commands = parser.add_subparsers(dest="command", metavar="{setup,connect,doctor,logout}")

    setup = commands.add_parser(
        "setup",
        parents=[common],
        help="Ask for the İTÜ username and password and store them in the OS keychain.",
    )
    setup.add_argument("--no-verify", action="store_true", help="Do not try a login.")
    setup.add_argument(
        "--yes", "-y", action="store_true", help="Accept the default answer of every question."
    )

    connect = commands.add_parser(
        "connect",
        parents=[common],
        help="Register the server in an MCP client, without any secret in its config.",
    )
    connect.add_argument(
        "client", nargs="?", choices=cli_setup.CONNECT_TARGETS, default="all"
    )
    connect.add_argument(
        "--dry-run", action="store_true", help="Show the change and write nothing."
    )
    connect.add_argument(
        "--yes", "-y", action="store_true", help="Do not ask; keep entries that hold a password."
    )

    doctor = commands.add_parser(
        "doctor", parents=[common], help="Check the installation and say how to fix what is off."
    )
    doctor.add_argument(
        "--online",
        action="store_true",
        help="Also try a login and look up the latest version on PyPI.",
    )

    logout = commands.add_parser(
        "logout", parents=[common], help="Delete the stored session cookies."
    )
    logout.add_argument(
        "--forget-password",
        action="store_true",
        help="Also remove the username and password from the OS keychain.",
    )


def _run_command(args: argparse.Namespace) -> int:
    cli_setup.configure_output()
    if args.command == "setup":
        return cli_setup.run_setup(args.lang, verify=not args.no_verify, assume_yes=args.yes)
    if args.command == "connect":
        return cli_setup.run_connect(
            args.lang, args.client, dry_run=args.dry_run, assume_yes=args.yes
        )
    if args.command == "doctor":
        return diagnostics.run_doctor(args.lang, online=args.online)
    return cli_setup.run_logout(args.lang, forget_password=args.forget_password)


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint. No args → stdio MCP server (MCP clients)."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.command:
        code = _run_command(args)
        if code:
            raise SystemExit(code)
        return

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
