"""``itu-mcp doctor``: one line per check, and the step that fixes it.

Nothing here contacts a host unless ``online=True`` is asked for. Credential
values are never read into a message: only where they come from.
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import client_config
from .cli_messages import t
from .credentials import PASSWORD_ENV, USERNAME_ENV, credential_source, keychain_status
from .env import ignored_env_keys, load_ninova_env
from .server_info import SERVER_VERSION

OK = "ok"
WARN = "warn"
ERROR = "error"
MIN_PYTHON = (3, 11)
PYPI_URL = "https://pypi.org/pypi/itu-mcp/json"


@dataclass(frozen=True, slots=True)
class Check:
    level: str
    text: str
    fix: str = ""


def _python_version() -> tuple[int, int, int]:
    return (sys.version_info[0], sys.version_info[1], sys.version_info[2])


def state_dir_path() -> Path:
    return Path(os.getenv("NINOVA_STATE_DIR") or (Path.home() / ".ninova_state")).expanduser()


def verify_login(username: str | None = None, password: str | None = None) -> tuple[bool, str]:
    """Try one Ninova login. The only network call of ``setup``.

    With a username and password the attempt uses exactly those (the
    environment is put back afterwards); without, whatever the server itself
    would find. The returned message never contains the password.
    """
    from .client import NinovaClient

    saved = {key: os.environ.get(key) for key in (USERNAME_ENV, PASSWORD_ENV)}
    if username and password:
        os.environ[USERNAME_ENV] = username
        os.environ[PASSWORD_ENV] = password
    try:
        NinovaClient().ensure_logged_in(verify=True)
    except Exception as exc:  # any failure is an answer to "does login work"
        message = str(exc) or type(exc).__name__
        secret = password or os.environ.get(PASSWORD_ENV) or ""
        if secret:
            message = message.replace(secret, "***")
        return False, message
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return True, ""


def latest_pypi_version() -> str | None:
    """Newest released version, or ``None`` when PyPI cannot be read."""
    import requests

    try:
        response = requests.get(PYPI_URL, timeout=10)
        response.raise_for_status()
        version = response.json()["info"]["version"]
    except Exception:
        return None
    return version if isinstance(version, str) else None


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version)[:3])


def _check_python(lang: str) -> Check:
    version = _python_version()
    shown = ".".join(str(part) for part in version)
    if version[:2] < MIN_PYTHON:
        return Check(ERROR, t(lang, "doc_python_old", version=shown), t(lang, "doc_python_fix"))
    return Check(OK, t(lang, "doc_python_ok", version=shown))


def _check_credentials(lang: str) -> Check:
    source = credential_source()
    text = t(lang, "doc_credentials", source=t(lang, f"source_{source}"))
    if source == "none":
        return Check(ERROR, text, t(lang, "doc_credentials_fix"))
    return Check(OK, text)


def _check_keychain(lang: str) -> Check:
    status = keychain_status()
    if status["available"]:
        return Check(OK, t(lang, "doc_keychain_ok", backend=status["backend"]))
    reason = t(lang, f"reason_{status['reason']}")
    fix = "" if status["reason"] == "disabled" else t(lang, "fix_keyring")
    return Check(WARN, t(lang, "doc_keychain_bad", reason=reason), fix)


def _writable(directory: Path) -> bool:
    try:
        with tempfile.TemporaryFile(dir=directory):
            return True
    except OSError:
        return False


def _check_state_dir(lang: str) -> Check:
    path = state_dir_path()
    if not path.exists():
        parent = path
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if parent.is_dir() and _writable(parent):
            return Check(OK, t(lang, "doc_state_missing", path=path))
        return Check(ERROR, t(lang, "doc_state_not_writable", path=path), t(lang, "doc_state_fix"))
    if not path.is_dir() or not _writable(path):
        return Check(ERROR, t(lang, "doc_state_not_writable", path=path), t(lang, "doc_state_fix"))
    if os.name == "posix":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            return Check(
                WARN,
                t(lang, "doc_state_wide", mode=oct(mode)[2:], path=path),
                f"chmod 700 {path}",
            )
    return Check(OK, t(lang, "doc_state_ok", path=path))


def _check_timezone(lang: str) -> Check:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("Europe/Istanbul")
    except Exception:
        return Check(ERROR, t(lang, "doc_tz_bad"), t(lang, "doc_tz_fix"))
    return Check(OK, t(lang, "doc_tz_ok"))


def _check_ignored_env(lang: str) -> Check:
    keys = ignored_env_keys()
    if keys:
        return Check(
            WARN, t(lang, "doc_env_ignored", keys=", ".join(keys)), t(lang, "doc_env_ignored_fix")
        )
    return Check(OK, t(lang, "doc_env_ok"))


def _check_env_file(lang: str) -> Check:
    raw = os.getenv("NINOVA_ENV_FILE")
    if not raw:
        return Check(OK, t(lang, "doc_env_file_unset"))
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.is_file():
        return Check(OK, t(lang, "doc_env_file_ok", path=path))
    return Check(ERROR, t(lang, "doc_env_file_missing", path=path), t(lang, "doc_env_file_fix"))


def _check_clients(lang: str) -> list[Check]:
    checks: list[Check] = []
    registered: list[str] = []
    for state in client_config.all_states():
        label = t(lang, f"client_{state.client}")
        if not state.valid:
            checks.append(
                Check(
                    WARN,
                    t(lang, "doc_config_invalid", client=label, path=state.path),
                    t(lang, "doc_config_invalid_fix"),
                )
            )
            continue
        if state.has_password:
            checks.append(
                Check(
                    WARN,
                    t(lang, "doc_plaintext", client=label, path=state.path),
                    t(lang, "doc_plaintext_fix"),
                )
            )
        if state.registered:
            registered.append(label)
    if registered:
        checks.append(Check(OK, t(lang, "doc_clients", clients=", ".join(registered))))
    else:
        checks.append(Check(WARN, t(lang, "doc_clients_none"), t(lang, "doc_clients_fix")))
    return checks


def _check_online(lang: str) -> list[Check]:
    checks: list[Check] = []
    works, message = verify_login()
    if works:
        checks.append(Check(OK, t(lang, "doc_login_ok")))
    else:
        checks.append(
            Check(ERROR, t(lang, "doc_login_bad", message=message), t(lang, "doc_login_fix"))
        )
    latest = latest_pypi_version()
    if latest is None:
        checks.append(Check(WARN, t(lang, "doc_pypi_unknown")))
    elif _version_key(latest) > _version_key(SERVER_VERSION):
        checks.append(
            Check(
                WARN,
                t(lang, "doc_pypi_newer", latest=latest, version=SERVER_VERSION),
                t(lang, "doc_pypi_fix"),
            )
        )
    else:
        checks.append(Check(OK, t(lang, "doc_pypi_ok", version=SERVER_VERSION)))
    return checks


def run_checks(lang: str = "tr", *, online: bool = False) -> list[Check]:
    # An unreadable .env shows up as "no credentials" below.
    with contextlib.suppress(OSError, UnicodeDecodeError):
        load_ninova_env()
    checks = [
        _check_python(lang),
        Check(OK, t(lang, "doc_version", version=SERVER_VERSION)),
        _check_credentials(lang),
        _check_keychain(lang),
        _check_state_dir(lang),
        _check_timezone(lang),
        _check_ignored_env(lang),
        _check_env_file(lang),
        *_check_clients(lang),
    ]
    if online:
        checks.extend(_check_online(lang))
    return checks


def run_doctor(lang: str = "tr", *, online: bool = False) -> int:
    """Print the report. Returns 0 when no check is an error."""
    checks = run_checks(lang, online=online)
    for check in checks:
        print(f"[{t(lang, 'level_' + check.level)}] {check.text}")
        if check.fix and check.level != OK:
            print("    " + t(lang, "fix", fix=check.fix))
    errors = sum(1 for check in checks if check.level == ERROR)
    print(t(lang, "doc_summary_bad", count=errors) if errors else t(lang, "doc_summary_ok"))
    return 1 if errors else 0
