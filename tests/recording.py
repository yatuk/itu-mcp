"""Offline test double: a ``requests`` transport adapter that never opens a socket.

It records where each request would have gone (the host the HTTP library
itself would connect to) and answers from a script. Tests that claim "nothing
was sent to a foreign host" assert on ``hosts``.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import requests
from requests.adapters import BaseAdapter
from urllib3.util import parse_url

Reply = tuple[int, dict[str, str], bytes]
HTML_OK: Reply = (200, {"Content-Type": "text/html; charset=utf-8"}, b"<html><body><h1>ok</h1></body></html>")


def redirect(location: str, status: int = 302) -> Reply:
    return (status, {"Location": location}, b"")


class RecordingAdapter(BaseAdapter):
    """Answers every request locally and remembers its destination."""

    def __init__(self, script: dict[str, Reply | list[Reply] | Callable[[Any], Reply]] | None = None) -> None:
        super().__init__()
        self.script = dict(script or {})
        self.requests: list[requests.PreparedRequest] = []

    @property
    def hosts(self) -> list[str]:
        return [parse_url(item.url).host for item in self.requests]

    @property
    def urls(self) -> list[str]:
        return [str(item.url) for item in self.requests]

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        del kwargs
        self.requests.append(request)
        entry = self.script.get(str(request.url), HTML_OK)
        if callable(entry):
            entry = entry(request)
        if isinstance(entry, list):
            entry = entry.pop(0) if len(entry) > 1 else entry[0]
        status, headers, body = entry
        response = requests.Response()
        response.request = request
        response.url = str(request.url)
        response.status_code = status
        response.headers.update(headers)
        response._content = body
        response._content_consumed = True
        response.encoding = "utf-8"
        return response

    def close(self) -> None:
        return None


def offline_app(test: Any, script: dict[str, Any] | None = None, **environment: str) -> tuple[Any, RecordingAdapter]:
    """A ``NinovaMcpApp`` whose authenticated session can only reach the recorder.

    The state directory is a fresh temporary directory, the session counts as
    logged in, and the environment is restored when the test ends.
    """
    from ninova_mcp.server import NinovaMcpApp

    stack = test.enterContext(ExitStack())
    state_dir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="itu-offline-"))).resolve()
    values = {
        "NINOVA_USERNAME": "dummy",
        "NINOVA_PASSWORD": "dummy-password",
        "NINOVA_STATE_DIR": str(state_dir),
        "NINOVA_REQUEST_DELAY_MS": "0",
        "NINOVA_SESSION_PERSIST": "0",
        "NINOVA_DISABLE_PLAYWRIGHT_FALLBACK": "1",
        **environment,
    }
    stack.enter_context(patch.dict(os.environ, values))
    for name in (
        "NINOVA_BASE_URL", "NINOVA_EXTRA_SSO_HOSTS", "NINOVA_EXTRA_READ_DIRS",
        "NINOVA_EXTRA_DOWNLOAD_DIRS", "NINOVA_MAX_DOWNLOAD_MB", "NINOVA_ALLOW_SENSITIVE_OUTPUT",
    ):
        if name not in environment:
            os.environ.pop(name, None)
    stack.enter_context(patch("ninova_mcp.server.load_ninova_env"))
    app = NinovaMcpApp()
    adapter = RecordingAdapter(script)
    client = app.client
    client.session.mount("https://", adapter)
    client.session.mount("http://", adapter)
    client.last_login_at = "2026-01-01T00:00:00+00:00"
    client.login_method = "test"
    return app, adapter
