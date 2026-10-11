"""Transport of the public (no-auth) OBS client: allowlist, pacing, cache."""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlparse

import requests

from .cache import TtlCache
from .client import DEFAULT_HEADERS, _request_delay_seconds
from .http_security import request_with_safe_redirects
from .obs_errors import ObsError


class ObsPublicTransport:
    """Allowlisted, paced requests to the public OBS pages and the shared cache."""

    DEFAULT_BASE_URL = "https://obs.itu.edu.tr"
    DEFAULT_CACHE_TTL = 3600.0  # 1 hour — course catalog rarely changes
    DEFAULT_REQUEST_DELAY_SECONDS = 0.12
    ALLOWED_PUBLIC_HOSTS = frozenset({"obs.itu.edu.tr", "www.takvim.sis.itu.edu.tr"})
    DEFAULT_SCHEDULE_CACHE_TTL = 60.0

    def __init__(
        self,
        session: requests.Session | None = None,
        base_url: str | None = None,
        cache_ttl: float | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("NINOVA_OBS_BASE_URL") or self.DEFAULT_BASE_URL).rstrip("/")
        parsed_base = urlparse(self.base_url)
        if parsed_base.scheme != "https" or (parsed_base.hostname or "").lower() != "obs.itu.edu.tr":
            raise ObsError("NINOVA_OBS_BASE_URL must be https://obs.itu.edu.tr")
        self._session = session
        cttl = cache_ttl if cache_ttl is not None else float(
            os.getenv("NINOVA_OBS_PUBLIC_CACHE_TTL_SECONDS") or self.DEFAULT_CACHE_TTL
        )
        self._cache: TtlCache[Any] = TtlCache(cttl)
        self._schedule_cache_ttl = float(
            os.getenv("NINOVA_PUBLIC_SCHEDULE_CACHE_TTL_SECONDS")
            or self.DEFAULT_SCHEDULE_CACHE_TTL
        )
        self._min_request_interval = _request_delay_seconds()
        self._last_request_at = 0.0

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": DEFAULT_HEADERS.get("User-Agent", "itu-mcp/0.2"),
                "Accept-Language": DEFAULT_HEADERS.get("Accept-Language", "tr-TR,tr;q=0.9,en;q=0.7"),
            })
        return self._session

    # -- internal helpers -------------------------------------------------

    def _throttle(self) -> None:
        if self._min_request_interval <= 0:
            return
        now = time.monotonic()
        remaining = self._min_request_interval - (now - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in self.ALLOWED_PUBLIC_HOSTS
        ):
            raise ObsError("OBS public request destination is not allowlisted")

    def _safe_request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        try:
            return request_with_safe_redirects(
                self.session,
                method,
                url,
                validate_url=self._validate_url,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise ObsError(f"OBS public request failed safely: {exc}") from exc

    def _get_html(self, path: str, *, params: dict[str, Any] | None = None) -> tuple[str, str]:
        """GET a public OBS page and return ``(html, final_url)``."""
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        self._throttle()
        response = self._safe_request("GET", url, params=params, timeout=30)
        if response.status_code >= 400:
            raise ObsError(
                f"OBS public endpoint {path} returned HTTP {response.status_code}"
            )
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            # OBS serves UTF-8 without saying so. Guessing is the fallback only:
            # on a short body the guess depends on which detector is installed.
            try:
                return response.content.decode("utf-8"), response.url
            except UnicodeDecodeError:
                response.encoding = response.apparent_encoding or response.encoding
        return response.text, response.url

    def _post_json(self, path: str, *, data: dict[str, Any]) -> Any:
        """POST form data to an exact public OBS route and decode JSON."""
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        self._throttle()
        response = self._safe_request("POST", url, data=data, timeout=30)
        if response.status_code >= 400:
            raise ObsError(f"OBS public endpoint {path} returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ObsError(f"OBS public endpoint {path} did not return JSON") from exc
