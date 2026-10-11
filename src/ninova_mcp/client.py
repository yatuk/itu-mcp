from __future__ import annotations

import os
import re
import time
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import requests

from .credentials import MISSING_CREDENTIALS_MESSAGE, resolve_credentials
from .env import load_ninova_env
from .http_security import (
    RequestTarget,
    request_with_safe_redirects,
    require_https_host,
)
from .operator_settings import EXTRA_SSO_HOSTS_ENV, extra_sso_hosts
from .parsing import clean_text, make_soup, normalize_lookup_text, normalize_url
from .session_store import (
    clear_session,
    default_session_path,
    load_session,
    save_session,
    session_persist_enabled,
)

DEFAULT_TIMEOUT = 30
DEFAULT_REQUEST_DELAY_SECONDS = 0.12
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}
LOGIN_URL_RE = re.compile(r"girisv3\.itu\.edu\.tr/Login\.aspx", re.IGNORECASE)

# Exact hosts, never a suffix match. Evidence for each one is in the code that
# uses it: NINOVA_HOST is the only value NINOVA_BASE_URL may have, the single
# sign-on form lives on SSO_LOGIN_HOST (LOGIN_URL_RE above), PORTAL_HOST is
# PORTAL_BASE_URL below, OBS_HOST is the only value NINOVA_OBS_BASE_URL may
# have (ObsClient sends its requests through this client's session), and
# HELP_DESK_HOST is HELP_BASE in help_tickets.
NINOVA_HOST = "ninova.itu.edu.tr"
SSO_LOGIN_HOST = "girisv3.itu.edu.tr"
SSO_LOGIN_PATH = "/login.aspx"
PORTAL_HOST = "portal.itu.edu.tr"
OBS_HOST = "obs.itu.edu.tr"
HELP_DESK_HOST = "yardim.itu.edu.tr"


def _request_delay_seconds() -> float:
    raw = os.getenv("NINOVA_REQUEST_DELAY_MS")
    if raw is None or not str(raw).strip():
        return DEFAULT_REQUEST_DELAY_SECONDS
    try:
        return max(0.0, float(raw) / 1000.0)
    except ValueError:
        return DEFAULT_REQUEST_DELAY_SECONDS


class NinovaError(RuntimeError):
    """Base Ninova error."""


class NinovaAuthError(NinovaError):
    """Authentication failed."""


@dataclass(slots=True)
class NinovaCredentials:
    username: str
    password: str

    @classmethod
    def from_env(cls) -> "NinovaCredentials":
        load_ninova_env()
        # Environment first, then the OS keychain: see credentials.py.
        resolved = resolve_credentials()
        if not resolved.username or not resolved.password:
            raise NinovaAuthError(MISSING_CREDENTIALS_MESSAGE)
        return cls(username=resolved.username, password=resolved.password)


class NinovaClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("NINOVA_BASE_URL") or "https://ninova.itu.edu.tr").rstrip("/")
        try:
            base_target = require_https_host(self.base_url, {NINOVA_HOST})
        except ValueError as exc:
            raise NinovaError("NINOVA_BASE_URL must be https://ninova.itu.edu.tr") from exc
        # The one host that a URL or path coming from a tool argument may name.
        self.ninova_host = base_target.host
        self.credentials = NinovaCredentials.from_env()
        self._replace_session()
        self.last_login_at: str | None = None
        self.login_method: str | None = None
        self._min_request_interval = _request_delay_seconds()
        self._last_request_at = 0.0
        state_root = os.getenv("NINOVA_STATE_DIR")
        self.session_path = default_session_path(state_root)
        self._try_restore_session()

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)
        return session

    def _replace_session(self) -> None:
        """Swap in a fresh session, closing the old one's connection pool.

        ``requests.Session`` has no ``__del__``, so a discarded session's
        pooled connections are only released deterministically via an
        explicit close — otherwise they linger until GC gets to them, which
        matters on a long-lived server re-logging in repeatedly.
        """
        old_session = getattr(self, "session", None)
        self.session = self._build_session()
        if old_session is not None:
            old_session.close()

    def _try_restore_session(self) -> bool:
        restored = load_session(
            self.session_path,
            username=self.credentials.username,
            base_url=self.base_url,
        )
        if restored is None:
            return False
        self._replace_session()
        for cookie in restored["jar"]:
            self.session.cookies.set_cookie(cookie)
        # Soft mark; ensure_logged_in(verify=True) / first get will re-login if dead.
        self.last_login_at = datetime.now(tz=UTC).isoformat()
        self.login_method = restored.get("login_method") or "restored-session"
        return True

    def _persist_session(self) -> None:
        if not session_persist_enabled():
            return
        save_session(
            self.session_path,
            username=self.credentials.username,
            base_url=self.base_url,
            jar=self.session.cookies,
            login_method=self.login_method,
        )

    def _clear_persisted_session(self) -> None:
        clear_session(self.session_path)

    def _throttle(self) -> None:
        """Space out requests so bulk syncs do not hammer Ninova."""
        if self._min_request_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self._min_request_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _dashboard_url(self) -> str:
        return f"{self.base_url}/Kampus1"

    def _entry_login_url(self) -> str:
        return f"{self.base_url}/Login.aspx?ReturnUrl=%2fmembers%2fogrenci.default.aspx"

    def _decode_response(self, response: requests.Response) -> str:
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or response.encoding
        return response.text

    def _looks_like_login_page(self, response: requests.Response, html: str | None = None) -> bool:
        parsed = urlparse(response.url)
        if LOGIN_URL_RE.search(response.url):
            return True
        if parsed.path.lower().endswith("/login.aspx") and parsed.netloc.startswith("girisv3."):
            return True
        text = html if html is not None else self._decode_response(response)
        return (
            "ContentPlaceHolder1_tbUserName" in text
            and "ContentPlaceHolder1_tbPassword" in text
        )

    def _extract_login_error(self, html: str) -> str | None:
        soup = make_soup(html)
        selectors = [
            ".validation-summary-errors",
            ".error",
            "#ContentPlaceHolder1_lblMessage",
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = clean_text(node.get_text(" ", strip=True))
                if text:
                    return text
        text = clean_text(soup.get_text(" ", strip=True))
        normalized_text = normalize_lookup_text(text)
        for marker in (
            "hatali",
            "hatali kullanici",
            "yanlis",
            "invalid",
            "unsuccessful",
            "failed",
        ):
            if marker in normalized_text:
                return text[:300]
        return None

    def _build_login_payload(self, login_html: str) -> dict[str, str]:
        soup = make_soup(login_html)
        payload: dict[str, str] = {}
        for input_tag in soup.select("input[name]"):
            input_name = input_tag.get("name")
            if not input_name:
                continue
            payload[input_name] = input_tag.get("value", "")

        payload["ctl00$ContentPlaceHolder1$tbUserName"] = self.credentials.username
        payload["ctl00$ContentPlaceHolder1$tbPassword"] = self.credentials.password
        payload["ctl00$ContentPlaceHolder1$btnLogin"] = payload.get(
            "ctl00$ContentPlaceHolder1$btnLogin", "Login"
        )
        return payload

    def _mark_logged_in(self, method: str) -> None:
        self.last_login_at = datetime.now(tz=UTC).isoformat()
        self.login_method = method

    def login(self, force: bool = False) -> dict[str, Any]:
        if force:
            self._replace_session()
            self._clear_persisted_session()
            self.last_login_at = None
            self.login_method = None
        elif self.last_login_at is not None and self.is_authenticated():
            self._persist_session()
            return self.session_info()

        ninova_flow = self._ninova_flow_hosts()
        self._throttle()
        response = self._safe_request(
            "GET", self._entry_login_url(), allowed_hosts=ninova_flow, timeout=DEFAULT_TIMEOUT
        )
        html = self._decode_response(response)
        if not self._looks_like_login_page(response, html=html):
            self._throttle()
            dashboard = self._safe_request(
                "GET", self._dashboard_url(), allowed_hosts=ninova_flow, timeout=DEFAULT_TIMEOUT
            )
            dashboard_html = self._decode_response(dashboard)
            if self._looks_like_login_page(dashboard, html=dashboard_html):
                raise NinovaAuthError(
                    "Unexpected login flow while opening Ninova dashboard. "
                    "The ITU login page markup may have changed."
                )
            self._mark_logged_in("existing-session")
            self._persist_session()
            return self.session_info()

        payload = self._build_login_payload(html)
        if "ctl00$ContentPlaceHolder1$tbUserName" not in payload and "ContentPlaceHolder1_tbUserName" not in html:
            raise NinovaAuthError(
                "Could not find Ninova login form fields on the page. "
                "ITU may have changed the login markup; try enabling Playwright "
                "fallback (do not set NINOVA_DISABLE_PLAYWRIGHT_FALLBACK=1)."
            )
        # The payload carries the password. It goes to the sign-on host only:
        # a page that merely looks like the login form on any other host gets
        # nothing, and a redirect cannot replay the form elsewhere.
        login_url = self._require_sso_login_url(response.url)
        self._throttle()
        login_response = self._safe_request(
            "POST",
            login_url,
            data=payload,
            headers={"Referer": login_url},
            allowed_hosts=ninova_flow,
            body_hosts={SSO_LOGIN_HOST},
            timeout=DEFAULT_TIMEOUT,
        )
        login_html = self._decode_response(login_response)
        if self._looks_like_login_page(login_response, html=login_html):
            error = self._extract_login_error(login_html)
            if self._playwright_allowed():
                try:
                    return self._login_with_playwright()
                except Exception as exc:  # pragma: no cover - best-effort fallback
                    detail = error or str(exc)
                    raise NinovaAuthError(
                        f"Ninova login failed: {detail}. "
                        "Check username/password, or install playwright fallback: "
                        'pip install "itu-mcp[playwright]" && playwright install chromium'
                    ) from exc
            raise NinovaAuthError(
                f"Ninova login failed: {error or 'unknown error'}. "
                "If credentials are correct, ITU may require browser login "
                "(2FA/CAPTCHA) or the form layout changed."
            )

        self._throttle()
        dashboard = self._safe_request(
            "GET", self._dashboard_url(), allowed_hosts=ninova_flow, timeout=DEFAULT_TIMEOUT
        )
        dashboard_html = self._decode_response(dashboard)
        if self._looks_like_login_page(dashboard, html=dashboard_html):
            raise NinovaAuthError(
                "Login completed but Ninova redirected back to the login page. "
                "Credentials may be wrong, or the session cookie was not accepted."
            )

        self._mark_logged_in("requests")
        self._persist_session()
        return self.session_info()

    def _playwright_allowed(self) -> bool:
        return os.getenv("NINOVA_DISABLE_PLAYWRIGHT_FALLBACK") not in {"1", "true", "TRUE"}

    def _login_with_playwright(self) -> dict[str, Any]:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - optional fallback
            raise NinovaAuthError("Playwright fallback is unavailable.") from exc

        with sync_playwright() as playwright:  # pragma: no cover - login fallback
            browser = None
            try:
                launch_kwargs = {"headless": True}
                try:
                    browser = playwright.chromium.launch(channel="chrome", **launch_kwargs)
                except Exception:
                    browser = playwright.chromium.launch(**launch_kwargs)
                context = browser.new_context(
                    user_agent=DEFAULT_HEADERS["User-Agent"],
                    locale="tr-TR",
                )
                page = context.new_page()
                page.goto(self._entry_login_url(), wait_until="domcontentloaded", timeout=60000)
                # Never type the password into a page on any other host.
                self._require_sso_login_url(page.url)
                page.locator("#ContentPlaceHolder1_tbUserName").fill(self.credentials.username)
                page.locator("#ContentPlaceHolder1_tbPassword").fill(self.credentials.password)
                page.locator("#ContentPlaceHolder1_btnLogin").click()
                try:
                    page.wait_for_url(
                        lambda url: "girisv3.itu.edu.tr/Login.aspx" not in url,
                        timeout=60000,
                    )
                except PlaywrightTimeoutError as exc:
                    error_text = clean_text(page.locator("body").inner_text())[:300]
                    raise NinovaAuthError(f"Ninova login failed: {error_text}") from exc

                cookies = context.cookies()
                self._replace_session()
                for cookie in cookies:
                    self.session.cookies.set(
                        cookie["name"],
                        cookie["value"],
                        domain=cookie.get("domain"),
                        path=cookie.get("path", "/"),
                    )
                self._mark_logged_in("playwright")
                self._persist_session()
                return self.session_info()
            finally:
                if browser is not None:
                    browser.close()

    def ensure_logged_in(self, *, verify: bool = False) -> dict[str, Any]:
        if self.last_login_at is None:
            return self.login()
        if verify and not self.is_authenticated():
            self._clear_persisted_session()
            return self.login(force=True)
        if session_persist_enabled():
            self._persist_session()
        return self.session_info()

    def is_authenticated(self) -> bool:
        self._throttle()
        response = self._safe_request(
            "GET",
            self._dashboard_url(),
            allowed_hosts=self._ninova_flow_hosts(),
            timeout=DEFAULT_TIMEOUT,
        )
        html = self._decode_response(response)
        return not self._looks_like_login_page(response, html=html)

    def session_info(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "username": self.credentials.username,
            "last_login_at": self.last_login_at,
            "login_method": self.login_method,
            # One cookie name can be set for several hosts; each is named once.
            "cookie_names": sorted({cookie.name for cookie in self.session.cookies}),
            "request_delay_ms": int(self._min_request_interval * 1000),
            "session_persist": session_persist_enabled(),
            "session_path": str(self.session_path) if session_persist_enabled() else None,
        }

    def _internal_hosts(self) -> frozenset[str]:
        """Every host this client may contact from its own fixed code paths.

        Tool arguments never reach these: a URL or path coming from a tool is
        limited to the Ninova host by ``ninova_url``.
        """
        return (
            frozenset({self.ninova_host, SSO_LOGIN_HOST, PORTAL_HOST, OBS_HOST, HELP_DESK_HOST})
            | extra_sso_hosts()
        )

    def _ninova_flow_hosts(self) -> frozenset[str]:
        """Hosts a Ninova request may pass through: Ninova and the sign-on page."""
        return frozenset({self.ninova_host, SSO_LOGIN_HOST}) | extra_sso_hosts()

    def _check_host(self, url: str, allowed_hosts: Collection[str]) -> RequestTarget:
        """Raise NinovaError unless ``url`` is HTTPS on exactly one of ``allowed_hosts``.

        The host is read from the URL the HTTP library will send, so a URL
        that ``urlparse`` and the HTTP library read differently cannot pass.
        """
        try:
            return require_https_host(url, allowed_hosts)
        except ValueError as exc:
            raise NinovaError(
                f"URL is not allowed: {exc} Only these hosts are permitted here: "
                f"{', '.join(sorted(allowed_hosts))}. If the İTÜ sign-on flow now passes through "
                f"another itu.edu.tr host, the operator can add it with {EXTRA_SSO_HOSTS_ENV}."
            ) from exc

    def _check_domain(self, url: str) -> None:
        """Raise NinovaError if the URL is not one of the exact internal İTÜ hosts."""
        self._check_host(url, self._internal_hosts())

    def _require_sso_login_url(self, url: str) -> str:
        """Return the sign-on URL that may receive the password, or raise."""
        try:
            target = require_https_host(url, {SSO_LOGIN_HOST})
        except ValueError as exc:
            raise NinovaAuthError(
                "The login form was not served by the İTÜ sign-on host "
                f"({SSO_LOGIN_HOST}). The password was not sent."
            ) from exc
        if not urlparse(target.url).path.lower().endswith(SSO_LOGIN_PATH):
            raise NinovaAuthError(
                "The login form was not served from the İTÜ sign-on page. The password was not sent."
            )
        return target.url

    def ninova_url(self, url_or_path: str) -> str:
        """Turn a tool-supplied URL or path into a Ninova URL, or raise.

        This is the only way a tool argument becomes a request URL. The host
        must be the configured Ninova host exactly: no other İTÜ host, no
        port, no user information.
        """
        try:
            if isinstance(url_or_path, str) and url_or_path.startswith("//"):
                # A scheme-relative reference names a host of its own.
                raise ValueError("a path must not start with '//'.")
            url = normalize_url(url_or_path, self.base_url)
            require_https_host(url, {self.ninova_host})
        except ValueError as exc:
            raise NinovaError(
                f"Only https://{self.ninova_host} URLs and Ninova paths are accepted here: {exc}"
            ) from exc
        return url

    def _require_final_host(self, response: requests.Response, host: str) -> None:
        try:
            require_https_host(response.url, {host})
        except ValueError as exc:
            response.close()
            raise NinovaError(
                f"The request ended on another host than {host}; the response was discarded."
            ) from exc

    def _safe_request(
        self,
        method: str,
        url: str,
        *,
        allowed_hosts: Collection[str] | None = None,
        body_hosts: Collection[str] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Request an allowlisted URL and validate each redirect before use.

        ``allowed_hosts`` defaults to the internal host set. ``body_hosts``
        limits which hosts may receive a request body (see http_security).
        """
        hosts = frozenset(allowed_hosts) if allowed_hosts is not None else self._internal_hosts()

        def validate(candidate: str) -> None:
            self._check_host(candidate, hosts)

        try:
            return request_with_safe_redirects(
                self.session,
                method,
                url,
                validate_url=validate,
                body_hosts=body_hosts,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise NinovaError(f"ITU request failed safely: {exc}") from exc

    def get(self, url_or_path: str, *, stream: bool = False) -> requests.Response:
        # Validated before anything is sent, including the login request.
        url = self.ninova_url(url_or_path)
        hosts = self._ninova_flow_hosts()
        self.ensure_logged_in()
        self._throttle()
        response = self._safe_request(
            "GET", url, allowed_hosts=hosts, timeout=DEFAULT_TIMEOUT, stream=stream
        )
        is_login_page = (
            self._looks_like_login_page(response, html="")
            if stream
            else self._looks_like_login_page(response)
        )
        if is_login_page:
            self.login(force=True)
            self._throttle()
            response = self._safe_request(
                "GET", url, allowed_hosts=hosts, timeout=DEFAULT_TIMEOUT, stream=stream
            )
            if self._looks_like_login_page(response, html="" if stream else None):
                response.close()
                raise NinovaAuthError(
                    "Ninova kept returning the login page after a fresh login. "
                    "Call refresh_session and try again."
                )
        # A Ninova page must not be able to bounce an authenticated read to
        # another host and have that host's answer returned as the result.
        self._require_final_host(response, self.ninova_host)
        response.raise_for_status()
        return response

    def get_html(self, url_or_path: str) -> tuple[str, requests.Response]:
        response = self.get(url_or_path, stream=False)
        html = self._decode_response(response)
        return html, response

    # -- portal.itu.edu.tr -------------------------------------------------

    PORTAL_BASE_URL = "https://portal.itu.edu.tr"

    def get_help_desk_html(self, url: str) -> tuple[str, requests.Response]:
        """Fetch a help desk page with the SSO session.

        Internal use only: callers build ``url`` from a validated ticket ID.
        The first request must go to the help desk host itself.
        """
        self._check_host(url, {HELP_DESK_HOST})
        self.ensure_logged_in()
        self._throttle()
        response = self._safe_request("GET", url, timeout=DEFAULT_TIMEOUT)
        if self._looks_like_login_page(response):
            self.login(force=True)
            self._throttle()
            response = self._safe_request("GET", url, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        return self._decode_response(response), response

    def get_portal_html(self, path: str = "/apps/default/") -> tuple[str, str]:
        """Fetch an İTÜ Portal page (uses the same SSO session as Ninova).

        Returns ``(html, final_url)``.
        """
        url = f"{self.PORTAL_BASE_URL}{path}" if path.startswith("/") else path
        self._check_host(url, {PORTAL_HOST})
        self.ensure_logged_in()
        self._throttle()
        response = self._safe_request("GET", url, timeout=DEFAULT_TIMEOUT)
        self._check_domain(response.url)
        if self._looks_like_login_page(response):
            self.login(force=True)
            self._throttle()
            response = self._safe_request("GET", url, timeout=DEFAULT_TIMEOUT)
            self._check_domain(response.url)
        response.raise_for_status()
        html = self._decode_response(response)
        return html, response.url

    def post_multipart(
        self,
        url_or_path: str,
        *,
        data: dict[str, str],
        files: list[tuple[str, tuple[str, bytes, str]]],
        referer: str | None = None,
        timeout: int = 120,
    ) -> requests.Response:
        """POST a multipart ASP.NET WebForms submission (assignment upload).

        The target must be on the Ninova host, and the body is never sent to
        any other host, not even through a redirect.
        """
        url = self.ninova_url(url_or_path)
        hosts = self._ninova_flow_hosts()
        self.ensure_logged_in()
        headers = {"Referer": referer or url}
        request_options: dict[str, Any] = {
            "data": data,
            "files": files or None,
            "headers": headers,
            "allowed_hosts": hosts,
            "body_hosts": {self.ninova_host},
            "timeout": timeout,
        }
        self._throttle()
        response = self._safe_request("POST", url, **request_options)
        if self._looks_like_login_page(response):
            self.login(force=True)
            self._throttle()
            response = self._safe_request("POST", url, **request_options)
        if not self._looks_like_login_page(response):
            self._require_final_host(response, self.ninova_host)
        response.raise_for_status()
        return response
