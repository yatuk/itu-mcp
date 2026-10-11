"""Where the İTÜ username and password come from.

One seam for every caller. The order is:

1. the process environment (``NINOVA_USERNAME`` / ``NINOVA_PASSWORD``),
2. a ``.env`` file, which ``env.load_ninova_env`` has already copied into the
   environment, so it only differs from 1 in the reported source,
3. the operating system keychain, through the optional ``keyring`` package
   (``pip install "itu-mcp[keyring]"``), filled by ``itu-mcp setup``.

Keychain layout: service ``itu-mcp``; the username is stored under the fixed
key ``default-username`` and the password under the username itself.

Only real OS stores are accepted (Windows Credential Manager, macOS Keychain,
Secret Service, KWallet). File based, plain text and null backends are
refused. Values are never logged; callers that only need to know where the
credentials come from use :func:`credential_source`.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any

from .env import keys_loaded_from_file

KEYRING_SERVICE = "itu-mcp"
KEYRING_USERNAME_KEY = "default-username"
KEYRING_ENV = "NINOVA_KEYRING"
USERNAME_ENV = "NINOVA_USERNAME"
PASSWORD_ENV = "NINOVA_PASSWORD"  # nosec B105 # the variable's name, not a value

SOURCE_ENVIRONMENT = "environment"
SOURCE_ENV_FILE = "env_file"
SOURCE_KEYCHAIN = "keychain"
SOURCE_NONE = "none"

MISSING_CREDENTIALS_MESSAGE = (
    "NINOVA_USERNAME and NINOVA_PASSWORD must both be set. "
    "The easiest way: run `itu-mcp setup` once in a terminal to store them "
    "in the operating system keychain."
)

# Module prefixes of the keyring backends that hand the secret to the
# operating system. Anything else (keyrings.alt files, fail, null, a backend
# written by a third party) is refused.
_REAL_BACKEND_MODULES = (
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.OS_X",
    "keyring.backends.SecretService",
    "keyring.backends.libsecret",
    "keyring.backends.kwallet",
)
_CHAINER_MODULE = "keyring.backends.chainer"

_OFF_VALUES = frozenset({"0", "false", "no", "off"})


class KeychainUnavailable(RuntimeError):
    """No usable OS keychain. ``reason`` is a short machine readable code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedCredentials:
    username: str | None
    password: str | None
    source: str

    @property
    def complete(self) -> bool:
        return bool(self.username and self.password)

    def __repr__(self) -> str:  # never show values, not even in a traceback
        return f"ResolvedCredentials(source={self.source!r}, complete={self.complete})"


def keyring_enabled() -> bool:
    return os.getenv(KEYRING_ENV, "1").strip().lower() not in _OFF_VALUES


def _import_keyring() -> Any | None:
    """Import ``keyring`` on demand. ``None`` when the extra is not installed."""
    try:
        return importlib.import_module("keyring")
    except Exception:  # ImportError, or a broken installation
        return None


def _backend_is_real(backend: Any) -> bool:
    module = type(backend).__module__ or ""
    if module.startswith(_CHAINER_MODULE):
        members = list(getattr(backend, "backends", None) or [])
        return bool(members) and all(_backend_is_real(member) for member in members)
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in _REAL_BACKEND_MODULES
    )


def _backend_name(backend: Any) -> str:
    kind = type(backend)
    return f"{kind.__module__}.{kind.__qualname__}"


def keychain_backend() -> tuple[Any, str]:
    """Return ``(keyring module, backend name)`` or raise ``KeychainUnavailable``."""
    if not keyring_enabled():
        raise KeychainUnavailable("disabled", f"The keychain is switched off with {KEYRING_ENV}=0.")
    module = _import_keyring()
    if module is None:
        raise KeychainUnavailable(
            "not_installed",
            'The "keyring" package is not installed. Install it with: '
            'pipx install "itu-mcp[keyring]" (or: pipx inject itu-mcp keyring).',
        )
    try:
        backend = module.get_keyring()
    except Exception as exc:
        raise KeychainUnavailable(
            "no_backend", f"No keychain backend could be loaded ({type(exc).__name__})."
        ) from None
    name = _backend_name(backend)
    if not _backend_is_real(backend):
        raise KeychainUnavailable(
            "insecure_backend",
            f"The keyring backend {name} is not an operating system keychain "
            "(plain text, file based or null backends are refused).",
        )
    return module, name


def keychain_status() -> dict[str, Any]:
    """Whether a real keychain can be used. No secret is read."""
    try:
        _module, name = keychain_backend()
    except KeychainUnavailable as exc:
        return {"available": False, "reason": exc.reason, "message": str(exc)}
    return {"available": True, "backend": name}


def _keychain_username(module: Any) -> str | None:
    try:
        return module.get_password(KEYRING_SERVICE, KEYRING_USERNAME_KEY) or None
    except Exception:
        return None


def _keychain_password(module: Any, username: str) -> str | None:
    try:
        return module.get_password(KEYRING_SERVICE, username) or None
    except Exception:
        return None


def _environment_source() -> str:
    loaded = keys_loaded_from_file()
    if USERNAME_ENV in loaded or PASSWORD_ENV in loaded:
        return SOURCE_ENV_FILE
    return SOURCE_ENVIRONMENT


def resolve_credentials() -> ResolvedCredentials:
    """Find the shared İTÜ credentials. Never raises, never logs.

    A username in the environment with no password next to it is completed
    from the keychain, so a client config may name the account without
    holding the secret.
    """
    username = os.getenv(USERNAME_ENV) or None
    password = os.getenv(PASSWORD_ENV) or None
    if username and password:
        return ResolvedCredentials(username, password, _environment_source())

    try:
        module, _name = keychain_backend()
    except KeychainUnavailable:
        return ResolvedCredentials(username, password, SOURCE_NONE)

    if password is None:
        username = username or _keychain_username(module)
        if username:
            password = _keychain_password(module, username)
            if password:
                return ResolvedCredentials(username, password, SOURCE_KEYCHAIN)
    return ResolvedCredentials(username, password, SOURCE_NONE)


def credential_source() -> str:
    """``environment``, ``env_file``, ``keychain`` or ``none``. No values."""
    return resolve_credentials().source


def credentials_available() -> bool:
    return resolve_credentials().complete


def keychain_has_credentials() -> bool:
    """Whether the keychain alone holds a full username and password pair."""
    try:
        module, _name = keychain_backend()
    except KeychainUnavailable:
        return False
    username = _keychain_username(module)
    return bool(username and _keychain_password(module, username))


def _delete_entry(module: Any, key: str) -> bool:
    try:
        module.delete_password(KEYRING_SERVICE, key)
    except Exception:  # not stored, or the backend refused: nothing to undo
        return False
    return True


def store_credentials(username: str, password: str) -> str:
    """Save the pair in the OS keychain and return the backend name.

    Raises ``KeychainUnavailable`` when there is no real keychain; nothing is
    written anywhere else in that case.
    """
    username = username.strip()
    if not username or not password:
        raise ValueError("username and password must not be empty")
    module, name = keychain_backend()
    previous = _keychain_username(module)
    try:
        module.set_password(KEYRING_SERVICE, username, password)
        module.set_password(KEYRING_SERVICE, KEYRING_USERNAME_KEY, username)
    except Exception as exc:
        raise KeychainUnavailable(
            "write_failed", f"The keychain refused the write ({type(exc).__name__})."
        ) from None
    if previous and previous != username:
        _delete_entry(module, previous)
    return name


def delete_credentials() -> bool:
    """Remove the stored pair. ``True`` when something was removed.

    Raises ``KeychainUnavailable`` when there is no real keychain.
    """
    module, _name = keychain_backend()
    username = _keychain_username(module)
    removed = False
    if username:
        removed = _delete_entry(module, username) or removed
    removed = _delete_entry(module, KEYRING_USERNAME_KEY) or removed
    return removed
