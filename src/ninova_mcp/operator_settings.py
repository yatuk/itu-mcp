"""Settings only the operator can change: environment variables, never tool arguments.

A tool argument is written by the model, and the model reads untrusted text
(announcements, mail, tickets). Anything that widens what the server may read,
write or reveal is therefore decided here, from the process environment.
"""

from __future__ import annotations

import os
from pathlib import Path

ALLOW_SENSITIVE_OUTPUT_ENV = "NINOVA_ALLOW_SENSITIVE_OUTPUT"
EXTRA_READ_DIRS_ENV = "NINOVA_EXTRA_READ_DIRS"
EXTRA_DOWNLOAD_DIRS_ENV = "NINOVA_EXTRA_DOWNLOAD_DIRS"
MAX_DOWNLOAD_MB_ENV = "NINOVA_MAX_DOWNLOAD_MB"
EXTRA_SSO_HOSTS_ENV = "NINOVA_EXTRA_SSO_HOSTS"

DEFAULT_MAX_DOWNLOAD_MB = 200

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


class OperatorSettingError(ValueError):
    """An environment variable holds a value the server refuses to guess about."""


def env_flag(name: str, *, default: bool = False) -> bool:
    """Read an on/off environment variable.

    ``1/true/yes/on`` and ``0/false/no/off`` are accepted in any letter case.
    An unset or empty variable gives ``default``. Anything else raises: a
    safety switch that silently stays off because of a typo is worse than a
    clear error.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise OperatorSettingError(
        f"{name} has an unrecognised value. Use 1, true, yes or on to enable it, "
        "and 0, false, no or off to disable it."
    )


def sensitive_output_allowed() -> bool:
    return env_flag(ALLOW_SENSITIVE_OUTPUT_ENV)


SENSITIVE_OUTPUT_DISABLED_NOTE = (
    "include_sensitive was requested, but unredacted output is switched off by the operator "
    f"setting. The redacted result is returned. The person running the server can enable it by "
    f"setting {ALLOW_SENSITIVE_OUTPUT_ENV}=1 in the server environment; a tool argument cannot."
)


def extra_directories(name: str) -> list[Path]:
    """Absolute directories listed in ``name``, separated by ``os.pathsep``.

    A relative entry raises instead of being resolved against whatever the
    working directory happens to be.
    """
    raw = os.getenv(name) or ""
    directories: list[Path] = []
    for item in raw.split(os.pathsep):
        text = item.strip()
        if not text:
            continue
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            raise OperatorSettingError(
                f"{name} must list absolute directories separated by {os.pathsep!r}."
            )
        directories.append(candidate)
    return directories


def max_download_bytes() -> int:
    raw = os.getenv(MAX_DOWNLOAD_MB_ENV)
    if raw is None or not raw.strip():
        megabytes = DEFAULT_MAX_DOWNLOAD_MB
    else:
        try:
            megabytes = int(raw.strip())
        except ValueError:
            megabytes = 0
        if megabytes <= 0:
            raise OperatorSettingError(f"{MAX_DOWNLOAD_MB_ENV} must be a positive whole number of MiB.")
    return megabytes * 1024 * 1024


def extra_sso_hosts() -> frozenset[str]:
    """Extra İTÜ hosts the single sign-on redirect chain may pass through.

    Exact host names under ``itu.edu.tr`` only, separated by commas.
    """
    hosts: set[str] = set()
    for item in (os.getenv(EXTRA_SSO_HOSTS_ENV) or "").split(","):
        host = item.strip().lower()
        if not host:
            continue
        labels = host.split(".")
        valid = host.endswith(".itu.edu.tr") and all(
            label and label.replace("-", "").isalnum() and label.isascii() for label in labels
        )
        if not valid:
            raise OperatorSettingError(
                f"{EXTRA_SSO_HOSTS_ENV} accepts only exact host names under itu.edu.tr, "
                "separated by commas."
            )
        hosts.add(host)
    return frozenset(hosts)
