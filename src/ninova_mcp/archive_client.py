"""Read-only client for the İTÜ ders arşivi (yatuk/itu-archive).

OBS only publishes the currently active term. The archive scrapes it daily and
keeps every past term, so questions OBS cannot answer — who taught a course five
years ago, which season it opens in, how fast a section filled — become
answerable. The archive is static JSON on GitHub Pages, so this client is a
plain cached HTTPS reader with the same host-allowlist discipline as
``ItuPublicClient``.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import requests

from .cache import TtlCache, parse_ttl_seconds
from .client import DEFAULT_HEADERS, _request_delay_seconds
from .http_security import request_with_safe_redirects


class ItuArchiveError(RuntimeError):
    """The archive could not be fetched or did not contain the requested data."""


class ItuArchiveClient:
    """Fetch static archive JSON with a TTL cache.

    The archive is regenerated once a day, so a long default TTL is correct:
    re-fetching more often only costs latency.
    """

    DEFAULT_BASE_URL = "https://itu-ders.com/data"
    DEFAULT_CACHE_TTL = 6 * 60 * 60.0

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        base_url: str | None = None,
        cache_ttl: float | None = None,
    ) -> None:
        raw_base = (base_url or os.getenv("ITU_ARCHIVE_BASE_URL") or self.DEFAULT_BASE_URL).rstrip("/")
        parsed = urlparse(raw_base)
        # The base URL is local operator config (env or constructor), never
        # anything read off the network, so a fork may point this at its own
        # Pages host. HTTPS stays mandatory.
        if parsed.scheme != "https" or not parsed.hostname:
            raise ItuArchiveError("ITU_ARCHIVE_BASE_URL must be an https:// URL")
        self.base_url = raw_base
        self._allowed_host = parsed.hostname.lower()
        self._session = session
        ttl = cache_ttl if cache_ttl is not None else parse_ttl_seconds(
            os.getenv("ITU_ARCHIVE_CACHE_TTL_SECONDS"), self.DEFAULT_CACHE_TTL
        )
        self._cache: TtlCache[Any] = TtlCache(ttl)
        self._min_request_interval = _request_delay_seconds()
        self._last_request_at = 0.0

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(DEFAULT_HEADERS)
        return self._session

    # -- internals --------------------------------------------------------

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != self._allowed_host:
            raise ItuArchiveError(f"Archive request destination is not allowlisted: {url}")

    def _throttle(self) -> None:
        if self._min_request_interval <= 0:
            return
        remaining = self._min_request_interval - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _get_json(self, path: str, *, optional: bool = False) -> Any:
        """GET one archive JSON document.

        ``optional=True`` turns a 404 into ``None`` — the archive genuinely has
        gaps (2024-2025 Güz exists in no source), and a missing branch file is
        an answer rather than a failure.
        """
        if not path.startswith("/"):
            path = "/" + path
        cache_key = f"archive:{path}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        url = self.base_url + path
        self._throttle()
        try:
            response = request_with_safe_redirects(
                self.session,
                "GET",
                url,
                validate_url=self._validate_url,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ItuArchiveError(f"Archive request failed: {url}: {exc}") from exc

        if response.status_code == 404 and optional:
            return None
        if response.status_code >= 400:
            raise ItuArchiveError(f"Archive returned HTTP {response.status_code} for {path}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ItuArchiveError(f"Archive document is not JSON: {path}") from exc
        self._cache.set(cache_key, payload)
        return payload

    # -- documents --------------------------------------------------------

    def get_index(self) -> dict[str, Any]:
        """Term list, current term, and last scrape time."""
        payload = self._get_json("/index.json")
        if not isinstance(payload, dict):
            raise ItuArchiveError("Archive index.json has an unexpected shape")
        return payload

    def get_term_meta(self, slug: str) -> dict[str, Any]:
        """Branch list and section counts for one term."""
        payload = self._get_json(f"/terms/{slug}/meta.json")
        if not isinstance(payload, dict):
            raise ItuArchiveError(f"Archive meta.json has an unexpected shape: {slug}")
        return payload

    def get_term_branch(self, slug: str, branch: str) -> list[dict[str, Any]]:
        """Every section record for one branch in one term."""
        payload = self._get_json(f"/terms/{slug}/branches/{branch.upper()}.json", optional=True)
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise ItuArchiveError(f"Archive branch file has an unexpected shape: {slug}/{branch}")
        return payload

    def get_course_history(self, branch: str) -> dict[str, Any]:
        """Cross-term history for every course in one branch."""
        payload = self._get_json(f"/history/courses/{branch.upper()}.json", optional=True)
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ItuArchiveError(f"Archive course history has an unexpected shape: {branch}")
        return payload

    def get_instructor_history(self, letter: str) -> dict[str, Any]:
        """Cross-term history for instructors whose index letter is ``letter``."""
        payload = self._get_json(f"/history/instructors/{letter.lower()}.json", optional=True)
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ItuArchiveError(f"Archive instructor history has an unexpected shape: {letter}")
        return payload

    def get_instructor_names(self) -> list[list[Any]]:
        """``[name, index_letter, term_count, section_count]`` for every instructor."""
        payload = self._get_json("/history/names.json")
        if not isinstance(payload, list):
            raise ItuArchiveError("Archive names.json has an unexpected shape")
        return payload

    def get_course_codes(self) -> list[list[Any]]:
        """``[code, name, branch, term_count]`` for every archived course."""
        payload = self._get_json("/history/codes.json")
        if not isinstance(payload, list):
            raise ItuArchiveError("Archive codes.json has an unexpected shape")
        return payload

    def get_quota(self, slug: str) -> dict[str, Any]:
        """Derived fill summary for one term, keyed by CRN."""
        payload = self._get_json(f"/quota/{slug}.json", optional=True)
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ItuArchiveError(f"Archive quota file has an unexpected shape: {slug}")
        return payload

    # -- extra datasets ----------------------------------------------------
    #
    # These return ``None`` for a 404 instead of an empty container: the tools
    # built on them must tell "the archive never recorded this file" apart from
    # "the file exists and holds nothing that matches".

    def get_grades(self, branch: str) -> list[dict[str, Any]] | None:
        """Letter grade distribution records for every course in one branch."""
        segment = branch_path_segment(branch)
        payload = self._get_json(f"/grades/{segment}.json", optional=True)
        if payload is None:
            return None
        if not isinstance(payload, list):
            raise ItuArchiveError(f"Archive grades file has an unexpected shape: {segment}")
        return payload

    def get_catalog(self, branch: str) -> dict[str, Any] | None:
        """Catalog entries (description, outcomes, weekly topics) for one branch."""
        segment = branch_path_segment(branch)
        payload = self._get_json(f"/catalog/{segment}.json", optional=True)
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ItuArchiveError(f"Archive catalog file has an unexpected shape: {segment}")
        return payload

    def get_prereq_reverse(self) -> dict[str, Any]:
        """Course code -> courses that list it as a prerequisite.

        Deliberately the small reverse index: ``prereq/graph.json`` is several
        megabytes and is never fetched by this client.
        """
        payload = self._get_json("/prereq/reverse.json")
        if not isinstance(payload, dict):
            raise ItuArchiveError("Archive prereq/reverse.json has an unexpected shape")
        return payload

    def get_term_search(self, slug: str) -> list[list[Any]] | None:
        """Positional section rows for every branch of one term."""
        segment = term_path_segment(slug)
        payload = self._get_json(f"/terms/{segment}/search.json", optional=True)
        if payload is None:
            return None
        if not isinstance(payload, list):
            raise ItuArchiveError(f"Archive search.json has an unexpected shape: {segment}")
        return payload

    def get_exams(self, slug: str) -> dict[str, Any] | None:
        """Exam schedule for one term; most terms have no such file."""
        segment = term_path_segment(slug)
        payload = self._get_json(f"/exams/{segment}.json", optional=True)
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ItuArchiveError(f"Archive exams file has an unexpected shape: {segment}")
        return payload

    def get_status(self) -> dict[str, Any]:
        """Last scrape run: timestamps, section count, partial/failed flags."""
        payload = self._get_json("/status.json")
        if not isinstance(payload, dict):
            raise ItuArchiveError("Archive status.json has an unexpected shape")
        return payload


_BRANCH_SEGMENT_PATTERN = re.compile(r"[A-Z]{2,4}")
_TERM_SEGMENT_PATTERN = re.compile(r"\d{4}-\d{4}-(?:guz|bahar|yaz)")


def branch_path_segment(branch: str) -> str:
    """Return ``branch`` as a safe URL path segment, or raise.

    A branch code is interpolated into the request path, so anything that is
    not 2-4 ASCII letters (slashes, dots, percent escapes, query strings) is
    rejected before a URL is ever built.
    """
    candidate = str(branch or "").strip().upper()
    if not _BRANCH_SEGMENT_PATTERN.fullmatch(candidate):
        raise ItuArchiveError(f"Branş kodu geçersiz: {branch!r}. 'BLG' gibi 2-4 harf olmalı.")
    return candidate


def term_path_segment(slug: str) -> str:
    """Return ``slug`` as a safe URL path segment, or raise."""
    candidate = str(slug or "").strip().lower()
    if not _TERM_SEGMENT_PATTERN.fullmatch(candidate):
        raise ItuArchiveError(
            f"Dönem kodu geçersiz: {slug!r}. '2025-2026-guz' biçiminde olmalı "
            "(guz, bahar veya yaz)."
        )
    return candidate
