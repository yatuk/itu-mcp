"""Shared outbound HTTP safeguards.

Redirects are followed manually so every destination is validated before the
next request is sent. This closes the SSRF gap left by checking only a final URL.

Validation always runs on the URL that ``requests`` will really put on the
wire. ``urllib.parse`` and ``requests``/urllib3 do not agree on where the
authority of a URL ends (a backslash ends it for urllib3 but not for
``urlparse``), so a host check on the caller's string can pass for one host
while the connection goes to another. ``request_target`` therefore prepares the
URL the way ``requests`` does, reads the host with the parser ``requests``
uses, and the helper below sends exactly that prepared string.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests
from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url

REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
DEFAULT_MAX_REDIRECTS = 10
_BODY_KEYS = ("data", "files", "json")


class UnsafeUrlError(requests.exceptions.InvalidURL):
    """A URL was refused before any request was sent.

    It is a ``requests`` exception (and a ``ValueError``), so the wrappers that
    already turn ``requests.RequestException`` into their own error type keep
    working unchanged.
    """


@dataclass(frozen=True, slots=True)
class RequestTarget:
    """Where a request will really go: the prepared URL and its parts."""

    url: str
    scheme: str
    host: str
    port: int | None


def _is_forbidden_char(char: str) -> bool:
    return char == "\\" or char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F


def _authority_end(url: str) -> int:
    """Index where the authority of an absolute URL ends, for every parser.

    Everything up to the first ``/``, ``?`` or ``#`` after ``//`` is treated as
    authority. A backslash also ends it for urllib3, but backslashes are
    refused outright before this is used. Text without ``//`` has no
    authority at all (a relative reference), which is reported as ``-1``.
    """
    start = url.find("//")
    if start < 0:
        return -1
    end = len(url)
    for mark in "/?#":
        position = url.find(mark, start + 2)
        if position != -1:
            end = min(end, position)
    return end


def reject_unsafe_url_text(
    url: str,
    *,
    allow_space_after_authority: bool = False,
    check_userinfo: bool = True,
) -> None:
    """Refuse URL text that parsers are known to read differently.

    Refused: a backslash, any whitespace, any ASCII control character and
    userinfo (``user@`` or ``user:pass@``). ``allow_space_after_authority`` is
    only for redirect ``Location`` values: a server may send a literal space in
    a path or query, which preparing the URL percent-encodes. It never allows
    a space in the scheme or authority, and never a tab, newline or backslash.
    ``check_userinfo=False`` is for a relative reference that has not been
    joined to its base yet; the joined URL is then checked in full.
    """
    if not isinstance(url, str) or not url:
        raise UnsafeUrlError("URL is empty.")
    authority_end = _authority_end(url) if allow_space_after_authority else len(url)
    for index, char in enumerate(url):
        if not _is_forbidden_char(char):
            continue
        if char == " " and index > authority_end:
            continue
        raise UnsafeUrlError(
            "URL contains a backslash, whitespace or a control character and was refused."
        )
    start = url.find("//")
    if check_userinfo and start >= 0 and "@" in url[start + 2:max(_authority_end(url), 0)]:
        raise UnsafeUrlError("URL contains user information (user@host) and was refused.")


def request_target(
    url: str,
    *,
    method: str = "GET",
    params: Any = None,
    allow_space_after_authority: bool = False,
) -> RequestTarget:
    """Return the URL ``requests`` will send for ``url`` and where it points.

    Raises ``UnsafeUrlError`` when the text is unsafe, cannot be prepared, has
    no host, or is not stable under a second preparation (the helper sends the
    prepared string, and ``requests`` prepares it once more).
    """
    reject_unsafe_url_text(url, allow_space_after_authority=allow_space_after_authority)
    try:
        prepared = requests.Request(method.upper(), url, params=params).prepare().url
        if not prepared:
            raise UnsafeUrlError("URL could not be prepared.")
        reject_unsafe_url_text(prepared)
        again = requests.Request(method.upper(), prepared).prepare().url
        parsed = parse_url(prepared)
    except UnsafeUrlError:
        raise
    except (requests.RequestException, LocationParseError, ValueError, UnicodeError) as exc:
        raise UnsafeUrlError(f"URL could not be prepared safely: {exc}") from exc
    if again != prepared:
        raise UnsafeUrlError("URL does not have a stable prepared form and was refused.")
    if parsed.auth is not None:
        raise UnsafeUrlError("URL contains user information (user@host) and was refused.")
    host = (parsed.host or "").lower()
    scheme = (parsed.scheme or "").lower()
    if not host or not scheme:
        raise UnsafeUrlError("URL has no scheme or host.")
    return RequestTarget(url=prepared, scheme=scheme, host=host, port=parsed.port)


def require_https_host(
    url: str,
    allowed_hosts: Collection[str],
    *,
    allow_any_port: bool = False,
) -> RequestTarget:
    """Return the target of ``url`` if it is HTTPS on one of ``allowed_hosts``.

    The comparison is exact and case-insensitive on the host ``requests`` will
    connect to. A trailing-dot host, another scheme, userinfo and (unless
    ``allow_any_port``) a port other than 443 are refused.
    """
    target = request_target(url)
    if target.scheme != "https":
        raise UnsafeUrlError("Only HTTPS URLs are permitted.")
    if target.host not in allowed_hosts:
        raise UnsafeUrlError(f"Host is not allowed: {target.host}")
    if not allow_any_port and target.port not in (None, 443):
        raise UnsafeUrlError(f"Port is not allowed: {target.port}")
    return target


def request_with_safe_redirects(
    session: requests.Session,
    method: str,
    url: str,
    *,
    validate_url: Callable[[str], None],
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    body_hosts: Collection[str] | None = None,
    **kwargs: Any,
) -> requests.Response:
    """Send a request while validating every destination in advance.

    ``validate_url`` receives the prepared URL (query included): the exact
    URL ``requests`` puts on the wire. Without ``params`` that prepared string
    is what ``session.request`` is given. With ``params`` the prepared URL
    without the query is passed together with the unchanged ``params``, after
    checking that this pair prepares to the validated URL; callers and test
    doubles therefore still see ``params`` as a keyword. Redirect targets are
    always sent as the prepared string. ``body_hosts``, when given,
    names the only hosts that may receive a request body (form data, files,
    JSON): a request carrying one to any other host is refused, which also
    stops a 307/308 redirect from replaying a login form or an upload elsewhere.
    """

    request_kwargs = dict(kwargs)
    request_kwargs.pop("allow_redirects", None)
    params = request_kwargs.pop("params", None)
    current_method = method.upper()
    current = request_target(url, method=current_method, params=params)
    send_url = current.url
    if params is not None:
        send_url = request_target(url, method=current_method).url
        if request_target(send_url, method=current_method, params=params).url != current.url:
            raise UnsafeUrlError("URL and query parameters do not prepare to a stable URL.")
        request_kwargs["params"] = params
    history: list[requests.Response] = []

    for redirect_count in range(max_redirects + 1):
        validate_url(current.url)
        if body_hosts is not None and current.host not in body_hosts and any(
            request_kwargs.get(key) is not None for key in _BODY_KEYS
        ):
            raise UnsafeUrlError(
                f"Refusing to send a request body to {current.host}."
            )
        response = session.request(
            current_method,
            send_url,
            allow_redirects=False,
            **request_kwargs,
        )
        validate_url(response.url or current.url)

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

        try:
            reject_unsafe_url_text(
                location, allow_space_after_authority=True, check_userinfo=False
            )
            target = request_target(
                urljoin(response.url or current.url, location),
                allow_space_after_authority=True,
            )
            validate_url(target.url)
        except Exception:
            response.close()
            raise

        if current.host != target.host and "headers" in request_kwargs:
            headers = dict(request_kwargs.get("headers") or {})
            headers.pop("Authorization", None)
            headers.pop("authorization", None)
            request_kwargs["headers"] = headers

        # Match requests/browser behavior. A 307/308 preserves method and body.
        if response.status_code == 303 or (
            response.status_code in {301, 302} and current_method == "POST"
        ):
            current_method = "GET"
            for body_key in _BODY_KEYS:
                request_kwargs.pop(body_key, None)

        request_kwargs.pop("params", None)
        history.append(response)
        response.close()
        current = target
        send_url = target.url

    raise requests.TooManyRedirects(f"Exceeded {max_redirects} redirects for {url}")
