"""Load ``NINOVA_*`` settings from a ``.env`` file.

Two kinds of file are told apart, because the working directory of an MCP
server is chosen by whatever started it and may sit inside a tree somebody
else wrote (a cloned repository, an unpacked archive):

* An explicit file, named with ``NINOVA_ENV_FILE`` or the ``env_file``
  argument. The operator chose it, so every ``NINOVA_*`` key in it counts.
* An auto-discovered file: ``.env`` in the current working directory itself.
  Parent directories are not searched. Such a file may set credentials and
  tuning values, but not the keys that decide where credentials are sent,
  where state is written or which protections are on. Those keys are skipped
  and named (never their values) on stderr.

Variables already present in the process environment always win. Keys without
the ``NINOVA_`` prefix are never loaded from any file.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path

_OFF_VALUES = frozenset({"0", "false", "no", "off"})
_ON_VALUES = frozenset({"1", "true", "yes", "on"})


def _is_off(value: str) -> bool:
    return value.strip().lower() in _OFF_VALUES


def _is_on(value: str) -> bool:
    return value.strip().lower() in _ON_VALUES


def _is_exactly_one(value: str) -> bool:
    return value == "1"


# Keys an auto-discovered ``.env`` may set. None of them changes a host, a
# path on disk or a protection: credentials (sent only to allowlisted hosts),
# cache lifetimes, pacing, payload size and tool selection, which can only
# narrow the default of "every tool group on".
AUTO_ENV_SAFE_KEYS: frozenset[str] = frozenset(
    {
        "NINOVA_USERNAME",
        "NINOVA_PASSWORD",
        "NINOVA_MAIL_USERNAME",
        "NINOVA_MAIL_PASSWORD",
        "NINOVA_MAIL_TIMEOUT_SECONDS",
        "NINOVA_LIBRARY_NAME",
        "NINOVA_LIBRARY_ID",
        "NINOVA_LIBRARY_PIN",
        "NINOVA_LIBRARY_CACHE_TTL_SECONDS",
        "NINOVA_COURSE_CACHE_TTL_SECONDS",
        "NINOVA_OBS_JWT_TTL_SECONDS",
        "NINOVA_OBS_PUBLIC_CACHE_TTL_SECONDS",
        "NINOVA_PUBLIC_SCHEDULE_CACHE_TTL_SECONDS",
        "NINOVA_ITU_PUBLIC_CACHE_TTL_SECONDS",
        "NINOVA_REQUEST_DELAY_MS",
        "NINOVA_COMPACT_DEFAULT",
        "NINOVA_DISABLE_PLAYWRIGHT_FALLBACK",
        "NINOVA_SESSION_PERSIST",
        "NINOVA_SESSION_MAX_AGE_SECONDS",
        "NINOVA_TOOLSETS",
        "NINOVA_DISABLED_TOOLSETS",
        # Remote transport settings that can only add a requirement or narrow
        # the published tools. They stay honoured so that an existing remote
        # setup does not silently lose its API key after an upgrade.
        "NINOVA_REMOTE_API_KEY",
        "NINOVA_REMOTE_ALLOWED_TOOLS",
    }
)

# Switches an auto-discovered ``.env`` may move in the stricter direction
# only. The predicate says whether a value is the stricter one.
AUTO_ENV_RESTRICT_ONLY_KEYS: dict[str, Callable[[str], bool]] = {
    "NINOVA_ALLOW_UPLOADS": _is_off,
    "NINOVA_REQUIRE_ELICITATION": _is_exactly_one,
    "NINOVA_REMOTE_REQUIRE_API_KEY": _is_on,
    "NINOVA_REMOTE_PROTECT_ALL": _is_on,
}

# Keys an auto-discovered ``.env`` may never set. Any ``NINOVA_*`` key that is
# in none of the three collections is treated the same way, so a variable
# added later is refused until somebody classifies it here.
AUTO_ENV_BLOCKED_KEYS: frozenset[str] = frozenset(
    {
        # Where credentials and requests are sent.
        "NINOVA_BASE_URL",
        "NINOVA_OBS_BASE_URL",
        "NINOVA_LIBRARY_BASE_URL",
        "NINOVA_MAIL_HOST",
        "NINOVA_MAIL_PORT",
        # TLS trust and proxying.
        "NINOVA_LIBRARY_CA_BUNDLE",
        "NINOVA_LIBRARY_PROXY_URL",
        # Where cookies, snapshots and downloads are written.
        "NINOVA_STATE_DIR",
        # Which other file is read.
        "NINOVA_ENV_FILE",
        # Account writes.
        "NINOVA_OBS_REGISTRATION_WRITES",
        # Remote HTTP transport.
        "NINOVA_REMOTE_HOST",
        "NINOVA_REMOTE_PORT",
        "NINOVA_REMOTE_MCP_PATH",
        "NINOVA_REMOTE_ENABLE_MAIL",
        "NINOVA_REMOTE_RATE_LIMIT",
        "NINOVA_REMOTE_RATE_WINDOW_SECONDS",
        "NINOVA_REMOTE_DISABLE_RATE_LIMIT",
        "NINOVA_REMOTE_TRUST_PROXY_HEADERS",
        "NINOVA_PUBLIC_BASE_URL",
        "NINOVA_ALLOWED_HOSTS",
        "NINOVA_ALLOWED_ORIGINS",
        "NINOVA_DISABLE_DNS_REBINDING_PROTECTION",
        "NINOVA_HEALTHZ_CHECK_AUTH",
    }
)

_notice_lock = threading.Lock()
_ignored_keys: set[str] = set()
_reported: set[tuple[str, str]] = set()


def auto_env_allows(key: str, value: str) -> bool:
    """Whether an auto-discovered ``.env`` may set ``key`` to ``value``."""
    if key in AUTO_ENV_SAFE_KEYS:
        return True
    is_stricter = AUTO_ENV_RESTRICT_ONLY_KEYS.get(key)
    return bool(is_stricter and is_stricter(value))


def ignored_env_keys() -> tuple[str, ...]:
    """Names of the keys skipped in auto-discovered files so far, sorted."""
    with _notice_lock:
        return tuple(sorted(_ignored_keys))


def reset_env_notices() -> None:
    """Forget what was reported. For tests."""
    with _notice_lock:
        _ignored_keys.clear()
        _reported.clear()


def _report_ignored(path: Path, keys: list[str]) -> None:
    with _notice_lock:
        _ignored_keys.update(keys)
        fresh = sorted({key for key in keys if (str(path), key) not in _reported})
        _reported.update((str(path), key) for key in fresh)
    if not fresh:
        return
    # stderr only: stdout carries the MCP protocol on the stdio transport.
    # Key names only, never values.
    print(
        f"itu-mcp: ignored {len(fresh)} security-relevant key(s) in the "
        f"auto-discovered {path}: {', '.join(fresh)}. Set them in the "
        "process environment, or name the file explicitly with NINOVA_ENV_FILE.",
        file=sys.stderr,
    )


def _find_env_file(env_file: str | Path | None = None) -> tuple[Path | None, bool]:
    """Return the file to load and whether it was named explicitly."""
    explicit = env_file or os.getenv("NINOVA_ENV_FILE")
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if candidate.is_file():
            return candidate, True
        return None, True

    # The working directory itself and nothing above it: a parent directory
    # is not something the person who started the server necessarily owns.
    candidate = Path.cwd().resolve() / ".env"
    if candidate.is_file():
        return candidate, False
    return None, False


def _split_key_value(line: str) -> tuple[str, str] | None:
    if "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip().removeprefix("export ").strip()
    if not key:
        return None
    return key, value.strip()


def _strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(value):
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double:
            if index == 0 or value[index - 1].isspace():
                return value[:index].rstrip()
    return value.rstrip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_ninova_env(
    env_file: str | Path | None = None,
    *,
    override: bool = False,
) -> Path | None:
    path, explicit = _find_env_file(env_file=env_file)
    if path is None:
        return None

    ignored: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = _split_key_value(line)
        if parsed is None:
            continue
        key, value = parsed
        if not key.startswith("NINOVA_"):
            continue
        value = _unquote(_strip_inline_comment(value))
        if not explicit and not auto_env_allows(key, value):
            # A key the process environment already sets is decided there, so
            # there is nothing to report.
            if override or key not in os.environ:
                ignored.append(key)
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = value
    if ignored:
        _report_ignored(path, ignored)
    return path
