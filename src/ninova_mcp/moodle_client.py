"""Read-only client for İTÜ Moodle (moodle.itu.edu.tr).

Moodle has its own sign-in form, not the girisv3 single sign-on that Ninova
and OBS use, but it accepts the same İTÜ username and password. The password
is only ever sent to ``moodle.itu.edu.tr``: every request, and every redirect
hop, is validated against that one host, and request bodies are limited to it.

The session lives in memory only. When Moodle reports that the session has
expired the client signs in once more and retries the call.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import requests

from .cache import TtlCache, parse_ttl_seconds
from .client import (
    DEFAULT_HEADERS,
    DEFAULT_TIMEOUT,
    NinovaAuthError,
    NinovaCredentials,
    NinovaError,
    _request_delay_seconds,
)
from .http_security import request_with_safe_redirects, require_https_host
from .parsing_common import clean_text, make_soup

MOODLE_HOST = "moodle.itu.edu.tr"
MOODLE_BASE_URL = f"https://{MOODLE_HOST}"
MAX_RESOURCE_BYTES = 40 * 1024 * 1024
MAX_DISCUSSIONS = 20
MAX_POST_CHARS = 6000
COURSE_CLASSIFICATIONS = ("all", "inprogress", "future", "past", "favourites", "hidden")
_SESSION_ERROR_CODES = {"servicerequireslogin", "invalidsesskey", "requireloginerror", "sessiontimedout"}
_SESSKEY_RE = re.compile(r'"sesskey"\s*:\s*"([A-Za-z0-9]{6,})"')
_LOGIN_TOKEN_RE = re.compile(r'name="logintoken"\s+value="([^"]+)"')
_CRN_RE = re.compile(r"\(CRN:\s*(\d+)\)", re.IGNORECASE)
_COURSE_ID_RE = re.compile(r"^[0-9]{1,9}$")
_MODULE_PATH_RE = re.compile(r"^/mod/([a-z_]+)/view\.php$")


class MoodleError(NinovaError):
    """İTÜ Moodle request, sign-in or parsing error."""


class MoodleAuthError(MoodleError, NinovaAuthError):
    """Moodle rejected the credentials or the session."""


def _validate_url(url: str) -> None:
    try:
        require_https_host(url, {MOODLE_HOST})
    except ValueError as exc:
        raise MoodleError("Only https://moodle.itu.edu.tr URLs are allowed.") from exc


def _int_id(value: Any, label: str) -> int:
    text = str(value).strip()
    if not _COURSE_ID_RE.match(text):
        raise MoodleError(f"{label} must be a number, for example 17354.")
    return int(text)


def _moodle_username(raw: str) -> str:
    """Moodle signs in with the plain username, not the e-mail address."""
    return raw.split("@", 1)[0].strip()


def parse_course_label(fullname: str, shortname: str) -> dict[str, Any]:
    """Split the course title Moodle shows into name, CRN and term code."""
    crn_match = _CRN_RE.search(fullname)
    title = _CRN_RE.sub("", fullname).strip(" -")
    term_code = shortname.split("-", 1)[0] if re.match(r"^\d{6}-", shortname) else None
    return {"title": title or fullname, "crn": crn_match.group(1) if crn_match else None, "term_code": term_code}


def _visible_text(node: Any) -> str:
    for hidden in node.select(".accesshide, .sr-only, .visually-hidden"):
        hidden.decompose()
    return clean_text(node.get_text(" ", strip=True))


class MoodleClient:
    def __init__(self, *, session: requests.Session | None = None, credentials: NinovaCredentials | None = None) -> None:
        self.base_url = MOODLE_BASE_URL
        self._credentials = credentials
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._sesskey: str | None = None
        self._lock = threading.RLock()
        self._min_request_interval = _request_delay_seconds()
        self._last_request_at = 0.0
        self._cache: TtlCache[Any] = TtlCache(
            parse_ttl_seconds(os.getenv("NINOVA_MOODLE_CACHE_TTL_SECONDS"), 120.0)
        )

    # -- transport ---------------------------------------------------------

    @property
    def credentials(self) -> NinovaCredentials:
        if self._credentials is None:
            self._credentials = NinovaCredentials.from_env()
        return self._credentials

    def _throttle(self) -> None:
        if self._min_request_interval <= 0:
            return
        remaining = self._min_request_interval - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self._throttle()
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        try:
            return request_with_safe_redirects(
                self.session,
                method,
                url,
                validate_url=_validate_url,
                body_hosts={MOODLE_HOST},
                **kwargs,
            )
        except requests.RequestException as exc:
            raise MoodleError(f"Moodle request failed: {exc.__class__.__name__}") from exc

    def _absolute(self, path_or_url: str) -> str:
        url = urljoin(self.base_url + "/", path_or_url)
        _validate_url(url)
        return url

    @staticmethod
    def _is_login_url(url: str) -> bool:
        return urlsplit(url).path.rstrip("/").endswith("/login/index.php")

    # -- sign-in -----------------------------------------------------------

    def login(self) -> None:
        with self._lock:
            self._sesskey = None
            self.session.cookies.clear()
            page = self._request("GET", f"{self.base_url}/login/index.php?lang=en")
            token = _LOGIN_TOKEN_RE.search(page.text)
            if not token:
                raise MoodleAuthError("Moodle sign-in form not found; İTÜ may have changed the page.")
            creds = self.credentials
            result = self._request(
                "POST",
                f"{self.base_url}/login/index.php",
                data={
                    "logintoken": token.group(1),
                    "username": _moodle_username(creds.username),
                    "password": creds.password,
                    "anchor": "",
                },
            )
            if self._is_login_url(result.url):
                soup = make_soup(result.text)
                alert = soup.select_one("#loginerrormessage, .loginerrors, .alert-danger")
                detail = clean_text(alert.get_text(" ", strip=True)) if alert else "unknown error"
                raise MoodleAuthError(f"Moodle sign-in failed: {detail}")
            self._sesskey = self._read_sesskey(result.text)
            if not self._sesskey:
                home = self._request("GET", f"{self.base_url}/my/")
                self._sesskey = self._read_sesskey(home.text)
            if not self._sesskey:
                raise MoodleAuthError("Signed in to Moodle but could not read the session key.")

    @staticmethod
    def _read_sesskey(html: str) -> str | None:
        match = _SESSKEY_RE.search(html)
        return match.group(1) if match else None

    def _ensure_session(self) -> str:
        with self._lock:
            if not self._sesskey:
                self.login()
            if self._sesskey is None:
                raise MoodleAuthError("Moodle session is not available.")
            return self._sesskey

    def auth_status(self) -> dict[str, Any]:
        try:
            self._ensure_session()
        except MoodleAuthError as exc:
            return {"authenticated": False, "error": str(exc)}
        return {"authenticated": True, "host": MOODLE_HOST}

    # -- JSON services -----------------------------------------------------

    def _ajax(self, method: str, args: dict[str, Any]) -> Any:
        for attempt in (0, 1):
            sesskey = self._ensure_session()
            response = self._request(
                "POST",
                f"{self.base_url}/lib/ajax/service.php",
                params={"sesskey": sesskey, "info": method},
                json=[{"index": 0, "methodname": method, "args": args}],
            )
            try:
                payload = response.json()
                entry = payload[0]
            except (ValueError, IndexError, KeyError, TypeError) as exc:
                if attempt == 0 and self._is_login_url(response.url):
                    self._sesskey = None
                    continue
                raise MoodleError("Moodle returned an unreadable service response.") from exc
            if not entry.get("error"):
                return entry.get("data")
            code = str((entry.get("exception") or {}).get("errorcode") or "")
            if attempt == 0 and code in _SESSION_ERROR_CODES:
                self._sesskey = None
                continue
            raise MoodleError(f"Moodle service error: {code or 'unknown'}")
        raise MoodleError("Moodle session could not be re-established.")

    def _get_html(self, url: str) -> tuple[str, str]:
        for attempt in (0, 1):
            self._ensure_session()
            response = self._request("GET", url)
            if self._is_login_url(response.url) and not self._is_login_url(url):
                if attempt == 0:
                    self._sesskey = None
                    continue
                raise MoodleAuthError("Moodle redirected to the sign-in page.")
            if response.status_code == 404:
                raise MoodleError("Moodle page not found.")
            if response.status_code >= 400:
                raise MoodleError(f"Moodle returned HTTP {response.status_code}.")
            return response.text, response.url
        raise MoodleError("Moodle session could not be re-established.")

    # -- reads -------------------------------------------------------------

    def list_courses(self, classification: str = "all") -> dict[str, Any]:
        if classification not in COURSE_CLASSIFICATIONS:
            raise MoodleError(f"classification must be one of {', '.join(COURSE_CLASSIFICATIONS)}.")
        cached = self._cache.get(("courses", classification))
        if cached is not None:
            return cached
        data = self._ajax(
            "core_course_get_enrolled_courses_by_timeline_classification",
            {
                "offset": 0,
                "limit": 0,
                "classification": classification,
                "sort": "fullname",
                "customfieldname": "",
                "customfieldvalue": "",
            },
        )
        courses = []
        for raw in (data or {}).get("courses", []):
            label = parse_course_label(str(raw.get("fullname") or ""), str(raw.get("shortname") or ""))
            courses.append(
                {
                    "course_id": raw.get("id"),
                    "name": label["title"],
                    "crn": label["crn"],
                    "term_code": label["term_code"],
                    "start": raw.get("startdate") or None,
                    "end": raw.get("enddate") or None,
                    "url": f"{self.base_url}/course/view.php?id={raw.get('id')}",
                }
            )
        result = {"classification": classification, "count": len(courses), "courses": courses}
        self._cache.set(("courses", classification), result)
        return result

    def get_course(self, course_id: Any) -> dict[str, Any]:
        cid = _int_id(course_id, "course_id")
        html, final_url = self._get_html(f"{self.base_url}/course/view.php?id={cid}")
        soup = make_soup(html)
        title_node = soup.select_one("h1, .page-header-headings")
        sections: list[dict[str, Any]] = []
        for section in soup.select("li.section, [data-for='section']"):
            name = section.get("data-sectionname") or ""
            if not name:
                heading = section.select_one(".sectionname, h3")
                name = clean_text(heading.get_text(" ", strip=True)) if heading else ""
            activities = []
            for item in section.select("li.activity, [data-for='cmitem']"):
                link = item.select_one("a[href*='/mod/']")
                if link is None:
                    continue
                href = urljoin(final_url, link.get("href") or "")
                parts = urlsplit(href)
                module = _MODULE_PATH_RE.match(parts.path)
                if parts.hostname != MOODLE_HOST or not module:
                    continue
                label = item.select_one(".instancename") or link
                activities.append(
                    {
                        "type": module.group(1),
                        "module_id": (parse_qs(parts.query).get("id") or [None])[0],
                        "name": _visible_text(label),
                        "url": href,
                    }
                )
            if activities or name:
                sections.append({"name": name, "activities": activities})
        return {
            "course_id": cid,
            "title": clean_text(title_node.get_text(" ", strip=True)) if title_node else None,
            "url": final_url,
            "sections": sections,
            "activity_count": sum(len(s["activities"]) for s in sections),
            "untrusted_external_content": True,
        }

    def get_announcements(self, course_id: Any, limit: int = 5) -> dict[str, Any]:
        limit = max(1, min(int(limit), MAX_DISCUSSIONS))
        course = self.get_course(course_id)
        forum = next(
            (
                a
                for s in course["sections"]
                for a in s["activities"]
                if a["type"] == "forum"
            ),
            None,
        )
        if forum is None:
            return {"course_id": course["course_id"], "announcements": [], "forum_url": None,
                    "untrusted_external_content": True}
        html, _ = self._get_html(forum["url"])
        rows = make_soup(html).select("tr.discussion")
        posts = []
        for row in rows[:limit]:
            link = row.select_one("a[href*='discuss.php']")
            if link is None:
                continue
            discuss_url = self._absolute(link.get("href") or "")
            posts.append(self._read_discussion(discuss_url))
        return {
            "course_id": course["course_id"],
            "forum_url": forum["url"],
            "announcement_count": len(rows),
            "announcements": posts,
            "untrusted_external_content": True,
        }

    def _read_discussion(self, url: str) -> dict[str, Any]:
        html, final_url = self._get_html(url)
        soup = make_soup(html)
        post = soup.select_one("article.forum-post-container, [data-region='post']")
        if post is None:
            return {"url": final_url, "subject": None, "body": None}
        subject = post.select_one("[data-region-content='forum-post-core-subject'], h3")
        body = post.select_one(".post-content-container")
        stamp = post.select_one("time")
        author = post.select_one("header a[href*='user/view.php'], address a")
        attachments = []
        for link in post.select("a[href*='pluginfile.php']"):
            href = urljoin(final_url, link.get("href") or "")
            if urlsplit(href).hostname == MOODLE_HOST:
                attachments.append({"name": clean_text(link.get_text(" ", strip=True)), "url": href})
        text = clean_text(body.get_text("\n", strip=True)) if body else ""
        return {
            "subject": clean_text(subject.get_text(" ", strip=True)) if subject else None,
            "author": clean_text(author.get_text(" ", strip=True)) if author else None,
            "posted": (stamp.get("datetime") or clean_text(stamp.get_text(" ", strip=True))) if stamp else None,
            "body": text[:MAX_POST_CHARS],
            "body_truncated": len(text) > MAX_POST_CHARS,
            "attachments": attachments,
            "url": final_url,
        }

    def get_upcoming_events(self, days: int = 30, limit: int = 25) -> dict[str, Any]:
        days = max(1, min(int(days), 365))
        limit = max(1, min(int(limit), 100))
        now = int(time.time())
        data = self._ajax(
            "core_calendar_get_action_events_by_timesort",
            {"timesortfrom": now, "timesortto": now + days * 86400, "limitnum": limit},
        )
        events = []
        for raw in (data or {}).get("events", []):
            course = raw.get("course") or {}
            events.append(
                {
                    "name": clean_text(str(raw.get("name") or "")),
                    "course": clean_text(str(course.get("fullname") or "")) or None,
                    "due_timestamp": raw.get("timesort"),
                    "type": raw.get("modulename") or raw.get("eventtype"),
                    "url": raw.get("url"),
                }
            )
        return {"days": days, "count": len(events), "events": events, "untrusted_external_content": True}

    def download_resource(self, url: str) -> tuple[bytes, str, str | None, str]:
        """Fetch one Moodle file: returns (bytes, final_url, content_type, filename)."""
        target = self._absolute(url)
        path = urlsplit(target).path
        if not (path.startswith("/pluginfile.php/") or _MODULE_PATH_RE.match(path)):
            raise MoodleError("Only Moodle file links (pluginfile.php or a resource page) can be read.")
        self._ensure_session()
        response = self._request("GET", target, stream=True)
        if self._is_login_url(response.url):
            self._sesskey = None
            self._ensure_session()
            response = self._request("GET", target, stream=True)
            if self._is_login_url(response.url):
                raise MoodleAuthError("Moodle redirected to the sign-in page.")
        if response.status_code >= 400:
            raise MoodleError(f"Moodle returned HTTP {response.status_code}.")
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            total += len(chunk)
            if total > MAX_RESOURCE_BYTES:
                raise MoodleError("File is too large for text extraction.")
            chunks.append(chunk)
        final_path = urlsplit(response.url).path
        filename = requests.utils.unquote(final_path.rsplit("/", 1)[-1]) or "file"
        return b"".join(chunks), response.url, response.headers.get("Content-Type"), filename
