"""Session, JWT and request core of the OBS student API.

OBS is a Vue SPA. Auth reuses the same İTÜ SSO cookies as Ninova, then
exchanges them for a short-lived JWT via ``/ogrenci/auth/jwt``.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests

from .client import NinovaAuthError, NinovaClient
from .obs_errors import ObsError

DEFAULT_OBS_BASE_URL = "https://obs.itu.edu.tr"
JWT_PATH = "/ogrenci/auth/jwt"
STUDENT_HOME = "/ogrenci/"


class ObsSessionCore:
    """SSO session, short-lived JWT and the authorised request core."""

    def __init__(
        self,
        ninova_client: NinovaClient | None = None,
        base_url: str | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("NINOVA_OBS_BASE_URL") or DEFAULT_OBS_BASE_URL
        ).rstrip("/")
        parsed_base = urlparse(self.base_url)
        if parsed_base.scheme != "https" or (parsed_base.hostname or "").lower() != "obs.itu.edu.tr":
            raise ObsError("NINOVA_OBS_BASE_URL must be https://obs.itu.edu.tr")
        self._ninova = ninova_client
        self._jwt: str | None = None
        self._jwt_obtained_at: float | None = None
        self._registration_check_lock = threading.Lock()
        self._last_registration_check: float | None = None
        self._jwt_ttl_seconds = float(os.getenv("NINOVA_OBS_JWT_TTL_SECONDS") or "1500")

    @property
    def ninova(self) -> NinovaClient:
        if self._ninova is None:
            self._ninova = NinovaClient()
        return self._ninova

    @property
    def session(self) -> requests.Session:
        return self.ninova.session

    def _safe_request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        return self.ninova._safe_request(method, url, **kwargs)

    def ensure_ready(self) -> dict[str, Any]:
        """Ensure Ninova/SSO session exists and the OBS JWT is genuinely valid.

        Forces a real verification and a fresh token rather than trusting
        cached state, since callers use this to answer "can I reach OBS right
        now", not "did we think so up to ``_jwt_ttl_seconds`` ago". A stale
        Ninova session or an expired JWT does not reliably surface as a
        clean error from OBS (see ``_looks_like_obs_login_page``), so a soft
        check here would keep reporting success after access has quietly died.
        """
        self.ninova.ensure_logged_in(verify=True)
        self._safe_request("GET", self.base_url + STUDENT_HOME, timeout=30)
        token = self._get_jwt(force=True)
        return {
            "obs_base_url": self.base_url,
            "jwt_present": bool(token),
            "login_method": self.ninova.login_method,
        }

    def _looks_like_obs_login_page(self, response: requests.Response) -> bool:
        """True if OBS answered with the İTÜ SSO login page instead of data.

        A dead session commonly does not surface as 401/403 here: OBS (or
        the SSO gateway in front of it) can return HTTP 200 with the login
        page's HTML body. Treating that as a normal response means a stale
        session silently returns login-page markup as if it were the
        requested data, instead of a diagnosable error.
        """
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "json" in content_type:
            return False
        text = response.text
        if not text or text.lstrip()[:1] in {"{", "["}:
            return False
        return self.ninova._looks_like_login_page(response, html=text)

    def _get_jwt(self, *, force: bool = False) -> str:
        now = time.time()
        if (
            not force
            and self._jwt
            and self._jwt_obtained_at is not None
            and (now - self._jwt_obtained_at) < self._jwt_ttl_seconds
        ):
            return self._jwt

        self.ninova.ensure_logged_in()
        self._safe_request("GET", self.base_url + STUDENT_HOME, timeout=30)
        response = self._safe_request("GET", self.base_url + JWT_PATH, timeout=30)
        token = self._jwt_from_response(response)
        self._jwt = token
        self._jwt_obtained_at = now
        return token

    def _jwt_from_response(self, response: requests.Response) -> str:
        """The token in the JWT endpoint's answer, or an auth error when it holds none."""
        if response.status_code != 200 or not response.text.strip():
            raise NinovaAuthError(
                "Could not obtain OBS JWT. Log in via Ninova credentials and open "
                "https://obs.itu.edu.tr/ogrenci/ in a browser to verify access."
            )
        token = response.text.strip().strip('"')
        # A dead Ninova session can make this endpoint return the SSO login
        # page's HTML instead of a token; a bare dot-count check would often
        # accept that HTML as a plausible-looking token (page markup easily
        # contains 2+ literal dots), so the login-page shape is checked too.
        if token.count(".") < 2 or self._looks_like_obs_login_page(response):
            raise NinovaAuthError(
                "OBS returned a login page instead of a JWT. The İTÜ SSO session is "
                "stale; call refresh_session and try again."
            )
        return token

    def _headers(self) -> dict[str, str]:
        token = self._get_jwt()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/plain, */*",
            "Referer": self.base_url + STUDENT_HOME,
            "X-Requested-With": "XMLHttpRequest",
        }

    def api_get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self._api_request("GET", path, params=params)

    def _api_request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        request_options: dict[str, Any] = {"params": params}
        if json_body is not None:
            request_options["json"] = json_body
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        response = self._send_authorised(method, url, request_options)
        if response.status_code in {401, 403} or self._looks_like_obs_login_page(response):
            response = self._resend_after_session_refresh(method, url, path, request_options)
        if response.status_code >= 400:
            # Some OBS endpoints return 500 when data is not yet published.
            raise ObsError(f"OBS API {path} failed with HTTP {response.status_code}")
        return _decoded_body(response)

    def _send_authorised(
        self, method: str, url: str, request_options: dict[str, Any],
    ) -> requests.Response:
        """Throttle, then send one request carrying the current JWT."""
        self.ninova._throttle()
        return self._safe_request(
            method,
            url,
            headers=self._headers(),
            **request_options,
            timeout=45,
        )

    def _resend_after_session_refresh(
        self, method: str, url: str, path: str, request_options: dict[str, Any],
    ) -> requests.Response:
        """Re-verify the SSO session, mint a new JWT and send the request once more."""
        # A stale JWT alone doesn't explain a login-page body; the
        # underlying Ninova/SSO session itself is usually what died, so
        # this re-verifies that too rather than just re-minting a token
        # against cookies that are already dead.
        self.ninova.ensure_logged_in(verify=True)
        self._get_jwt(force=True)
        response = self._send_authorised(method, url, request_options)
        if self._looks_like_obs_login_page(response):
            raise ObsError(
                f"OBS API {path} kept returning a login page after refreshing the "
                "session. Call refresh_session, or check access manually at "
                "https://obs.itu.edu.tr/ogrenci/."
            )
        return response


def _decoded_body(response: requests.Response) -> Any:
    """JSON when the answer says or looks like JSON, text otherwise, ``None`` when empty."""
    if not response.content:
        return None
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "json" in content_type or response.text[:1] in {"{", "["}:
        return response.json()
    return response.text
