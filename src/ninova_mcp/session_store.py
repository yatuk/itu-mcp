"""Persist Ninova session cookies between process restarts."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from requests.cookies import RequestsCookieJar, create_cookie

SESSION_STATE_VERSION = 1
DEFAULT_MAX_AGE_SECONDS = 12 * 60 * 60  # 12 hours


def session_persist_enabled() -> bool:
    value = os.getenv("NINOVA_SESSION_PERSIST", "1")
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _max_age_seconds() -> float:
    raw = os.getenv("NINOVA_SESSION_MAX_AGE_SECONDS")
    if raw is None or not str(raw).strip():
        return float(DEFAULT_MAX_AGE_SECONDS)
    try:
        return max(60.0, float(raw))
    except ValueError:
        return float(DEFAULT_MAX_AGE_SECONDS)


def default_session_path(state_dir: str | Path | None = None) -> Path:
    root = Path(state_dir or os.getenv("NINOVA_STATE_DIR") or (Path.home() / ".ninova_state"))
    return root.expanduser().resolve() / "session.json"


def _username_fingerprint(username: str) -> str:
    return hashlib.sha256(username.strip().casefold().encode("utf-8")).hexdigest()[:16]


def cookie_jar_to_list(jar: RequestsCookieJar) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for cookie in jar:
        items.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path or "/",
                "secure": bool(cookie.secure),
                "expires": cookie.expires,
                "rest": dict(cookie._rest) if getattr(cookie, "_rest", None) else {},
            }
        )
    return items


def list_to_cookie_jar(items: list[dict[str, Any]]) -> RequestsCookieJar:
    jar = RequestsCookieJar()
    now = time.time()
    for item in items:
        expires = item.get("expires")
        if expires is not None:
            try:
                if float(expires) <= now:
                    continue
            except (TypeError, ValueError):
                pass
        cookie = create_cookie(
            name=item["name"],
            value=item.get("value") or "",
            domain=item.get("domain"),
            path=item.get("path") or "/",
            secure=bool(item.get("secure")),
            expires=expires,
            rest=item.get("rest") or {},
        )
        jar.set_cookie(cookie)
    return jar


def save_session(
    path: Path,
    *,
    username: str,
    base_url: str,
    jar: RequestsCookieJar,
    login_method: str | None,
) -> None:
    if not session_persist_enabled():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SESSION_STATE_VERSION,
        "saved_at": time.time(),
        "username_fp": _username_fingerprint(username),
        "base_url": base_url.rstrip("/"),
        "login_method": login_method,
        "cookies": cookie_jar_to_list(jar),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_session(
    path: Path,
    *,
    username: str,
    base_url: str,
) -> dict[str, Any] | None:
    if not session_persist_enabled() or not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if document.get("username_fp") != _username_fingerprint(username):
        return None
    if (document.get("base_url") or "").rstrip("/") != base_url.rstrip("/"):
        return None

    saved_at = document.get("saved_at")
    try:
        age = time.time() - float(saved_at)
    except (TypeError, ValueError):
        return None
    if age > _max_age_seconds() or age < 0:
        return None

    cookies = document.get("cookies")
    if not isinstance(cookies, list) or not cookies:
        return None

    return {
        "jar": list_to_cookie_jar(cookies),
        "login_method": document.get("login_method") or "restored-session",
        "saved_at": saved_at,
        "age_seconds": age,
    }


def clear_session(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
