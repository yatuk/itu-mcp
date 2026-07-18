"""Shared outbound HTTP safeguards.

Redirects are followed manually so every destination is validated before the
next request is sent. This closes the SSRF gap left by checking only a final URL.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
DEFAULT_MAX_REDIRECTS = 10


def request_with_safe_redirects(
    session: requests.Session,
    method: str,
    url: str,
    *,
    validate_url: Callable[[str], None],
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    **kwargs: Any,
) -> requests.Response:
    """Send a request while validating every redirect target in advance."""

    request_kwargs = dict(kwargs)
    request_kwargs.pop("allow_redirects", None)
    current_method = method.upper()
    current_url = url
    history: list[requests.Response] = []

    for redirect_count in range(max_redirects + 1):
        validate_url(current_url)
        response = session.request(
            current_method,
            current_url,
            allow_redirects=False,
            **request_kwargs,
        )
        validate_url(response.url or current_url)

        if response.status_code not in REDIRECT_STATUS_CODES:
            response.history = history
            return response

        location = response.headers.get("Location")
        if not location:
            response.history = history
            return response
        if redirect_count >= max_redirects:
            response.close()
            raise requests.TooManyRedirects(
                f"Exceeded {max_redirects} redirects for {url}"
            )

        target_url = urljoin(response.url or current_url, location)
        validate_url(target_url)

        old_host = (urlparse(current_url).hostname or "").lower()
        new_host = (urlparse(target_url).hostname or "").lower()
        if old_host != new_host and "headers" in request_kwargs:
            headers = dict(request_kwargs.get("headers") or {})
            headers.pop("Authorization", None)
            headers.pop("authorization", None)
            request_kwargs["headers"] = headers

        # Match requests/browser behavior. A 307/308 preserves method and body.
        if response.status_code == 303 or (
            response.status_code in {301, 302} and current_method == "POST"
        ):
            current_method = "GET"
            for body_key in ("data", "files", "json"):
                request_kwargs.pop(body_key, None)

        request_kwargs.pop("params", None)
        history.append(response)
        response.close()
        current_url = target_url

    raise requests.TooManyRedirects(f"Exceeded {max_redirects} redirects for {url}")
