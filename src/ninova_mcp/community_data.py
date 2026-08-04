"""Cross-check client for an independently maintained prerequisite dataset.

A community-run project maintains its own scrape of every İTÜ course's
prerequisite expression, published as a plain pipe-delimited text file. It is
a third-party project, not an official İTÜ source, and carries no such
guarantee — but it expresses the same Ve/Veya rules OBS publishes, in the same
shape, which makes it useful as a second, independently maintained opinion
layered on top of the official OBS branch table this server already reads.

This client exists only to flag disagreement between the two sources. It is
never the source of truth: when the community dataset and OBS disagree, OBS
wins and the disagreement is surfaced as a warning, not resolved silently in
either direction.
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


class CrossCheckDataError(RuntimeError):
    """The community cross-check dataset could not be fetched or parsed."""


_OPERATOR_RE = re.compile(r"(veya|ve)")


def space_out_operators(expression: str) -> str:
    """Insert spaces around the source's unspaced 've'/'veya' operators.

    The source concatenates operators onto adjacent tokens, e.g.
    ``"MIN DDveya BLG 322E"``. The shared boolean-tree tokenizer in
    ``prerequisites.py`` relies on word-boundary regex matches to recognise
    operators, so this normalises spacing before handing the string off to it.
    Every match of "veya" or "ve" gets spaced out; the expression column
    contains no other lowercase text these could collide with.
    """
    return _OPERATOR_RE.sub(r" \1 ", expression)


class CrossCheckDataClient:
    """Fetch and cache a community-maintained course/prerequisite dataset."""

    # Points at a community-run scrape of the same OBS-published prerequisite
    # data, kept as a runtime default so the client works out of the box;
    # override with PREREQ_CROSSCHECK_BASE_URL to point at any equivalent feed.
    DEFAULT_BASE_URL = "https://raw.githubusercontent.com/itu-helper/data/refs/heads/main"
    DEFAULT_CACHE_TTL = 6 * 60 * 60.0

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        base_url: str | None = None,
        cache_ttl: float | None = None,
    ) -> None:
        raw_base = (base_url or os.getenv("PREREQ_CROSSCHECK_BASE_URL") or self.DEFAULT_BASE_URL).rstrip("/")
        parsed = urlparse(raw_base)
        if parsed.scheme != "https" or not parsed.hostname:
            raise CrossCheckDataError("PREREQ_CROSSCHECK_BASE_URL must be an https:// URL")
        self.base_url = raw_base
        self._allowed_host = parsed.hostname.lower()
        self._session = session
        ttl = cache_ttl if cache_ttl is not None else parse_ttl_seconds(
            os.getenv("PREREQ_CROSSCHECK_CACHE_TTL_SECONDS"), self.DEFAULT_CACHE_TTL
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

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != self._allowed_host:
            raise CrossCheckDataError(f"Cross-check request destination is not allowlisted: {url}")

    def _throttle(self) -> None:
        if self._min_request_interval <= 0:
            return
        remaining = self._min_request_interval - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def get_courses(self) -> dict[str, dict[str, Any]]:
        """Return every course keyed by its code, parsed from the pipe-delimited feed.

        Columns: code | name | language | credit | akts | prerequisite
        expression | credit/class requirement | description.
        """
        cached = self._cache.get("courses")
        if cached is not None:
            return cached  # type: ignore[return-value]

        url = self.base_url + "/courses.psv"
        self._throttle()
        try:
            response = request_with_safe_redirects(
                self.session, "GET", url, validate_url=self._validate_url, timeout=30
            )
        except requests.RequestException as exc:
            raise CrossCheckDataError(f"Cross-check request failed: {url}: {exc}") from exc
        if response.status_code >= 400:
            raise CrossCheckDataError(f"Cross-check source returned HTTP {response.status_code} for courses.psv")

        courses: dict[str, dict[str, Any]] = {}
        for line in response.text.splitlines():
            fields = line.split("|")
            if len(fields) < 6:
                continue
            code = fields[0].strip().upper()
            if not code:
                continue
            courses[code] = {
                "code": code,
                "name": fields[1].strip(),
                "language": fields[2].strip(),
                "credit": fields[3].strip(),
                "akts": fields[4].strip(),
                "prerequisite_expression": fields[5].strip(),
                "credit_requirement_text": fields[6].strip() if len(fields) > 6 else "",
            }
        self._cache.set("courses", courses)
        return courses

    def get_course_prerequisite_tree(self, course_code: str) -> dict[str, Any] | None:
        """Parse one course's cross-check-source prerequisite expression into a tree.

        Returns ``None`` when the course is absent from the dataset — callers
        must report that as 'comparison unavailable', never as agreement.
        """
        from .prerequisites import parse_credit_requirement, parse_prerequisite_expression

        row = self.get_courses().get(course_code.upper())
        if row is None:
            return None
        expression = space_out_operators(row["prerequisite_expression"])
        tree = parse_prerequisite_expression(expression) if expression.strip() else None
        return {
            "tree": tree,
            "credit_requirement": parse_credit_requirement(row.get("credit_requirement_text", "")),
            "raw_expression": row["prerequisite_expression"],
        }
