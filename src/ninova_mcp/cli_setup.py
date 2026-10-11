"""The ``setup``, ``connect`` and ``logout`` commands of ``itu-mcp``.

``setup`` is the only place that asks for the password. It is read with
``getpass``, handed to the OS keychain and to one optional login attempt, and
is never printed, logged or written to a file.
"""

from __future__ import annotations

import contextlib
import getpass
import os
import subprocess  # nosec B404 # only list2cmdline, to print a command
import sys

from . import client_config, diagnostics
from .cli_messages import t
from .credentials import (
    PASSWORD_ENV,
    USERNAME_ENV,
    KeychainUnavailable,
    credential_source,
    delete_credentials,
    keychain_has_credentials,
    store_credentials,
)
from .env import load_ninova_env
from .session_store import clear_session, default_session_path

CONNECT_TARGETS = (*client_config.CLIENTS, "all")
_YES_LETTERS = ("e", "y")


def configure_output() -> None:
    """Make Turkish characters printable on a Windows console."""
    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


def is_interactive() -> bool:
    stream = sys.stdin
    try:
        return bool(stream is not None and stream.isatty())
    except (AttributeError, ValueError):
        return False


def ask_yes_no(lang: str, question: str, *, default: bool = True, assume_yes: bool = False) -> bool:
    if assume_yes:
        return default
    if not is_interactive():
        return False
    try:
        answer = input(f"{question} {t(lang, 'yes_no')} ").strip().casefold()
    except EOFError:
        return False
    if not answer:
        return default
    return answer.startswith(_YES_LETTERS)


def _load_env() -> None:
    with contextlib.suppress(OSError, UnicodeDecodeError):
        load_ninova_env()


def run_setup(lang: str, *, verify: bool = True, assume_yes: bool = False) -> int:
    print(t(lang, "setup_intro"))
    if not is_interactive():
        print(t(lang, "setup_no_tty"))
        return 2

    try:
        username = input(t(lang, "setup_username_prompt")).strip()
        if username and "@" not in username:
            print(t(lang, "setup_username_hint"))
        password = getpass.getpass(t(lang, "setup_password_prompt")) if username else ""
    except EOFError:
        # Input closed (a pipe, or NUL on Windows, which claims to be a TTY).
        print()
        print(t(lang, "setup_no_tty"))
        return 2
    if not username or not password:
        print(t(lang, "setup_empty"))
        return 1

    _load_env()
    if os.getenv(USERNAME_ENV) or os.getenv(PASSWORD_ENV):
        print(t(lang, "setup_env_overrides"))

    stored = False
    try:
        backend = store_credentials(username, password)
    except KeychainUnavailable as exc:
        print(t(lang, "setup_no_keychain", reason=t(lang, f"reason_{exc.reason}")))
        if exc.reason != "disabled":
            print("  " + t(lang, "fix", fix=t(lang, "fix_keyring")))
        print(t(lang, "setup_fallback"))
        print(client_config.manual_env_snippet(*client_config.server_command()))
    else:
        stored = True
        print(t(lang, "setup_stored", backend=backend.rsplit(".", 1)[-1]))

    if verify and ask_yes_no(lang, t(lang, "setup_verify_q"), assume_yes=assume_yes):
        print(t(lang, "setup_verifying"))
        works, message = diagnostics.verify_login(username, password)
        if not works:
            print(t(lang, "setup_verify_failed", message=message))
            return 1
        print(t(lang, "setup_verify_ok"))

    if stored:
        if ask_yes_no(lang, t(lang, "setup_connect_q"), assume_yes=assume_yes):
            code = run_connect(lang, "all", assume_yes=assume_yes)
            if code:
                return code
        else:
            print(t(lang, "setup_connect_later"))
    print(t(lang, "setup_done"))
    return 0


def _report(lang: str, result: client_config.ConnectResult, target: str) -> None:
    label = t(lang, f"client_{result.client}")
    status = result.status
    is_code = result.client == client_config.CLIENT_CODE
    if status in (client_config.CREATED, client_config.UPDATED):
        if is_code:
            print(t(lang, "connect_code_updated", client=label))
            return
        print(t(lang, f"connect_{status}", client=label, path=result.path))
        if result.backup is not None:
            print(t(lang, "connect_backup", path=result.backup))
            if result.had_password:
                print(t(lang, "connect_backup_password"))
        print(t(lang, "connect_restart", client=label))
    elif status == client_config.UNCHANGED:
        print(t(lang, "connect_unchanged", client=label))
    elif status == client_config.DRY_RUN:
        if is_code:
            print(t(lang, "connect_dry_run_command", client=label))
            print("  " + result.manual)
        else:
            print(t(lang, "connect_dry_run", client=label))
            print(result.diff)
    elif status == client_config.INVALID:
        print(t(lang, "connect_invalid", client=label, path=result.path))
        print(result.manual)
    elif status == client_config.PASSWORD_KEPT:
        print(t(lang, "connect_password_kept", client=label, target=target))
    else:  # MANUAL, EXISTS_DIFFERENT, FAILED: Claude Code only
        print(t(lang, f"connect_{status}", client=label))
        print("  " + result.manual)


def _replace_password_entry(
    lang: str, state: client_config.ConfigState, *, dry_run: bool, assume_yes: bool
) -> bool:
    """Decide whether an entry holding ``NINOVA_PASSWORD`` may be rewritten."""
    print(
        t(
            lang,
            "connect_has_password",
            client=t(lang, f"client_{state.client}"),
            path=state.path,
        )
    )
    if not keychain_has_credentials():
        # Taking the password out now would leave the server with none.
        print(t(lang, "connect_password_no_keychain"))
        return False
    if dry_run:
        return True
    if assume_yes:
        return False
    return ask_yes_no(lang, t(lang, "connect_replace_q"))


def run_connect(
    lang: str, target: str = "all", *, dry_run: bool = False, assume_yes: bool = False
) -> int:
    command, args = client_config.server_command()
    print(t(lang, "connect_command", command=subprocess.list2cmdline([command, *args])))
    clients = client_config.CLIENTS if target == "all" else (target,)
    exit_code = 0
    handled = 0
    for client in clients:
        label = t(lang, f"client_{client}")
        if target == "all" and not client_config.client_detected(client):
            print(t(lang, "connect_not_found", client=label))
            continue
        handled += 1
        if client == client_config.CLIENT_CODE:
            result = client_config.register_claude_code(command, args, dry_run=dry_run)
        else:
            state = client_config.read_state(client)
            replace = bool(
                state.valid
                and state.has_password
                and _replace_password_entry(lang, state, dry_run=dry_run, assume_yes=assume_yes)
            )
            result = client_config.register_json_client(
                client, command, args, dry_run=dry_run, replace_password=replace
            )
        _report(lang, result, client)
        if result.status in (client_config.INVALID, client_config.FAILED):
            exit_code = 1
    if not handled:
        print(t(lang, "connect_none_found"))
        print(client_config.snippet(command, args))
    _load_env()
    if credential_source() == "none":
        print(t(lang, "connect_need_setup"))
    return exit_code


def run_logout(lang: str, *, forget_password: bool = False) -> int:
    _load_env()
    path = default_session_path(os.getenv("NINOVA_STATE_DIR"))
    existed = path.is_file()
    clear_session(path)
    print(t(lang, "logout_session_deleted" if existed else "logout_session_none", path=path))
    if not forget_password:
        if keychain_has_credentials():
            print(t(lang, "logout_password_hint"))
        return 0
    try:
        removed = delete_credentials()
    except KeychainUnavailable as exc:
        print(t(lang, "logout_password_unavailable", reason=t(lang, f"reason_{exc.reason}")))
        return 1
    print(t(lang, "logout_password_deleted" if removed else "logout_password_none"))
    return 0
