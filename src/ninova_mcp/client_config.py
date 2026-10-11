"""Register the server in MCP client configuration files, without secrets.

Used by ``itu-mcp connect`` and read by ``itu-mcp doctor``. The entry written
is ``{"mcpServers": {"itu": {"command": ..., "args": [...]}}}``: credentials
are expected in the OS keychain (``itu-mcp setup``), never in the file.

Write rules for a JSON config:

* the file is parsed first; a file that is not a JSON object is left alone,
* only ``mcpServers.itu`` changes, every other key keeps its place,
* a timestamped backup is written next to the file before it is replaced,
* the new content goes to a temporary file that is renamed over the target,
* running it again changes nothing.
"""

from __future__ import annotations

import contextlib
import copy
import difflib
import json
import os
import shutil
import subprocess  # nosec B404 # one fixed argv, no shell: see register_claude_code
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SERVER_KEY = "itu"
CLIENT_DESKTOP = "claude-desktop"
CLIENT_CODE = "claude-code"
CLIENT_CURSOR = "cursor"
CLIENTS = (CLIENT_DESKTOP, CLIENT_CODE, CLIENT_CURSOR)
JSON_CLIENTS = (CLIENT_DESKTOP, CLIENT_CURSOR)

_CREDENTIAL_ENV_KEYS = ("NINOVA_USERNAME", "NINOVA_PASSWORD")
_PASSWORD_ENV_KEY = "NINOVA_PASSWORD"  # nosec B105 # a variable name
_MASK = "***"
_PLACEHOLDERS = ("ad.soyad@itu.edu.tr", "<ITU_SIFRENIZ>")

# Outcomes of a registration attempt.
CREATED = "created"
UPDATED = "updated"
UNCHANGED = "unchanged"
DRY_RUN = "dry_run"
INVALID = "invalid"
PASSWORD_KEPT = "password_kept"  # nosec B105 # a status name
MANUAL = "manual"
EXISTS_DIFFERENT = "exists_different"
FAILED = "failed"


def _home() -> Path:
    return Path.home()


def desktop_config_path() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else _home() / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return _home() / ".config" / "Claude" / "claude_desktop_config.json"


def cursor_config_path() -> Path:
    return _home() / ".cursor" / "mcp.json"


def claude_code_config_path() -> Path:
    """Where Claude Code keeps user-scope servers. Only ever read here."""
    return _home() / ".claude.json"


def config_path(client: str) -> Path:
    if client == CLIENT_DESKTOP:
        return desktop_config_path()
    if client == CLIENT_CURSOR:
        return cursor_config_path()
    if client == CLIENT_CODE:
        return claude_code_config_path()
    raise ValueError(f"unknown client: {client}")


def client_detected(client: str) -> bool:
    """Whether the client looks installed, so ``connect all`` should touch it."""
    if client == CLIENT_CODE:
        return shutil.which("claude") is not None
    return config_path(client).parent.is_dir()


def server_command() -> tuple[str, list[str]]:
    """Absolute command a GUI client can start (it does not inherit PATH)."""
    found = shutil.which("itu-mcp")
    if found:
        return os.path.abspath(found), []
    return os.path.abspath(sys.executable), ["-m", "ninova_mcp"]


def entry_for(command: str, args: list[str]) -> dict[str, Any]:
    return {"command": command, "args": list(args)}


def snippet(command: str, args: list[str]) -> str:
    """The block to add by hand. Holds no secret."""
    return json.dumps(
        {"mcpServers": {SERVER_KEY: entry_for(command, args)}}, indent=2, ensure_ascii=False
    )


def manual_env_snippet(command: str, args: list[str]) -> str:
    """Fallback block with placeholders, for machines without a keychain."""
    entry = entry_for(command, args)
    entry["env"] = dict(zip(_CREDENTIAL_ENV_KEYS, _PLACEHOLDERS, strict=True))
    return json.dumps({"mcpServers": {SERVER_KEY: entry}}, indent=2, ensure_ascii=False)


@dataclass(slots=True)
class ConfigState:
    client: str
    path: Path
    exists: bool = False
    error: str | None = None  # unreadable | invalid_json | not_object | servers_not_object
    text: str | None = None
    data: dict[str, Any] | None = None
    entry: dict[str, Any] | None = None

    @property
    def valid(self) -> bool:
        return self.error is None

    @property
    def registered(self) -> bool:
        return self.entry is not None

    @property
    def has_password(self) -> bool:
        return entry_has_password(self.entry)


def entry_has_password(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    env = entry.get("env")
    return isinstance(env, dict) and bool(env.get(_PASSWORD_ENV_KEY))


def read_state(client: str, path: Path | None = None) -> ConfigState:
    """Parse a client config. Reads only; never raises."""
    target = path or config_path(client)
    state = ConfigState(client=client, path=target)
    if not target.is_file():
        state.data = {}
        return state
    state.exists = True
    try:
        state.text = target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        state.error = "unreadable"
        return state
    if not state.text.strip():
        state.data = {}  # an empty file holds nothing that could be lost
        return state
    try:
        data = json.loads(state.text)
    except ValueError:
        state.error = "invalid_json"
        return state
    if not isinstance(data, dict):
        state.error = "not_object"
        return state
    servers = data.get("mcpServers")
    if servers is not None and not isinstance(servers, dict):
        state.error = "servers_not_object"
        return state
    state.data = data
    entry = (servers or {}).get(SERVER_KEY)
    if isinstance(entry, dict):
        state.entry = entry
    return state


def all_states() -> list[ConfigState]:
    return [read_state(client) for client in CLIENTS]


@dataclass(slots=True)
class ConnectResult:
    client: str
    status: str
    path: Path | None = None
    backup: Path | None = None
    diff: str = ""
    manual: str = ""
    had_password: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def _serialise(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _masked(data: dict[str, Any]) -> dict[str, Any]:
    """Copy with every server's ``env`` values hidden, for the printed diff."""
    clone = copy.deepcopy(data)
    servers = clone.get("mcpServers")
    if isinstance(servers, dict):
        for entry in servers.values():
            if isinstance(entry, dict) and isinstance(entry.get("env"), dict):
                entry["env"] = dict.fromkeys(entry["env"], _MASK)
    return clone


def _diff(old: dict[str, Any], new: dict[str, Any], name: str) -> str:
    lines = difflib.unified_diff(
        _serialise(_masked(old)).splitlines(),
        _serialise(_masked(new)).splitlines(),
        fromfile=f"{name} (now)",
        tofile=f"{name} (new)",
        lineterm="",
    )
    return "\n".join(lines)


def _merged_entry(existing: dict[str, Any] | None, command: str, args: list[str]) -> dict[str, Any]:
    entry = dict(existing or {})
    entry["command"] = command
    entry["args"] = list(args)
    if entry_has_password(existing):
        env = {
            key: value for key, value in entry["env"].items() if key not in _CREDENTIAL_ENV_KEYS
        }
        if env:
            entry["env"] = env
        else:
            del entry["env"]
    return entry


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{counter}")
        counter += 1
    shutil.copy2(path, candidate)
    return candidate


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp"
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        if path.exists():
            with contextlib.suppress(OSError):
                shutil.copymode(path, temp)
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise


def register_json_client(
    client: str,
    command: str,
    args: list[str],
    *,
    path: Path | None = None,
    dry_run: bool = False,
    replace_password: bool = False,
) -> ConnectResult:
    """Add or refresh ``mcpServers.itu`` in a JSON config file."""
    state = read_state(client, path)
    result = ConnectResult(client=client, status=UNCHANGED, path=state.path)
    if not state.valid or state.data is None:
        result.status = INVALID
        result.manual = snippet(command, args)
        result.extra["error"] = state.error
        return result

    result.had_password = state.has_password
    if state.has_password and not replace_password:
        result.status = PASSWORD_KEPT
        return result

    new_entry = _merged_entry(state.entry, command, args)
    if state.entry == new_entry:
        return result

    new_data = dict(state.data)
    servers = dict(new_data.get("mcpServers") or {})
    servers[SERVER_KEY] = new_entry
    new_data["mcpServers"] = servers
    result.diff = _diff(state.data, new_data, state.path.name)
    if dry_run:
        result.status = DRY_RUN
        return result

    if state.exists:
        result.backup = _backup(state.path)
    _atomic_write(state.path, _serialise(new_data))
    result.status = UPDATED if state.exists else CREATED
    return result


def claude_code_argv(command: str, args: list[str], executable: str = "claude") -> list[str]:
    return [executable, "mcp", "add", SERVER_KEY, "--scope", "user", "--", command, *args]


def _printable(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv)


def register_claude_code(
    command: str,
    args: list[str],
    *,
    dry_run: bool = False,
    runner: Callable[..., Any] | None = None,
) -> ConnectResult:
    """Run ``claude mcp add itu --scope user -- <command>`` when possible."""
    state = read_state(CLIENT_CODE)
    result = ConnectResult(client=CLIENT_CODE, status=UNCHANGED, path=state.path)
    result.manual = _printable(claude_code_argv(command, args))
    result.had_password = state.has_password
    if state.entry is not None:
        same = state.entry.get("command") == command and list(
            state.entry.get("args") or []
        ) == list(args)
        if not same or state.has_password:
            # `claude mcp add` refuses an existing name; removing a server is
            # the user's call, so only the two commands are shown.
            result.status = EXISTS_DIFFERENT
        return result

    executable = shutil.which("claude")
    if executable is None:
        result.status = MANUAL
        return result
    if dry_run:
        result.status = DRY_RUN
        return result

    run = runner or subprocess.run
    try:
        # A fixed argv list, no shell, no user supplied text.
        completed = run(
            claude_code_argv(command, args, executable),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        result.status = FAILED
        return result
    result.status = UPDATED if completed.returncode == 0 else FAILED
    return result
