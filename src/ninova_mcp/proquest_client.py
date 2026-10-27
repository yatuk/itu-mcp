"""Opt-in ProQuest search through the student's own İTÜ library account.

ProQuest is licensed to İTÜ and reached off campus through OpenAthens. This
client signs in the way a browser does: ProQuest's sign-in for "Istanbul
Technical University" leads to the OpenAthens form for itu.edu.tr, which takes
the university number and the library password (``NINOVA_LIBRARY_ID`` /
``NINOVA_LIBRARY_PIN``), and a SAML response is posted back to ProQuest.

It is off unless the operator sets ``NINOVA_PROQUEST_ENABLE=1``. The licence
allows personal research use, not systematic or automated harvesting, so the
client is deliberately small: it returns one page of search results (titles,
authors, source, links) per call, waits between requests, caps the number of
searches per day and never downloads full text. The password goes only to
``login.openathens.net``, the SAML response only to ProQuest's service
provider, and every hop is validated against a fixed host list.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import date
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import requests

from .cache import TtlCache, parse_ttl_seconds
from .client import DEFAULT_HEADERS, DEFAULT_TIMEOUT, NinovaError
from .http_security import request_with_safe_redirects, require_https_host
from .parsing_common import clean_text, make_soup

ENABLE_ENV = "NINOVA_PROQUEST_ENABLE"
SP_HOST = "shibboleth-sp.prod.proquest.com"
IDP_HOST = "login.openathens.net"
PQ_HOST = "www.proquest.com"
ALLOWED_HOSTS = frozenset({SP_HOST, IDP_HOST, PQ_HOST})
ITU_ENTITY = "https://idp.itu.edu.tr/entity"
# login.openathens.net serves every member organisation; this is İTÜ's tenant.
ITU_IDP_PATH_PREFIX = "/auth/itu.edu.tr/"
SIGN_IN_URL = (
    f"https://{SP_HOST}/Shibboleth.sso/DS?entityID={quote(ITU_ENTITY, safe='')}"
    f"&target={quote(f'https://{SP_HOST}/ONE_SEARCH/PRODWWW', safe='')}"
)
SEARCH_PAGE_URL = f"https://{PQ_HOST}/index"
OPENATHENS_REDIRECTOR = "https://go.openathens.net/redirector/itu.edu.tr?url="
MIN_INTERVAL_SECONDS = 3.0  # between two search submissions
DEFAULT_DAILY_SEARCHES = 40
SNIPPET_CHARS = 400
_DOCVIEW_RE = re.compile(r"/docview/(\d{5,15})(?:/|$)")
_YEAR_RE = re.compile(r"\((?:[A-Za-z]{3,9}\.? )?(\d{4})\)|\b(19\d{2}|20\d{2})\b")
_COUNT_RE = re.compile(r"([\d.,]+)\s+(?:results?|sonu)")
_CITED_RE = re.compile(r"Times cited\s+(\d+)", re.IGNORECASE)


class ProQuestError(NinovaError):
    """ProQuest search, sign-in or configuration error."""


def proquest_enabled() -> bool:
    return (os.getenv(ENABLE_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def _validate_url(url: str) -> None:
    try:
        require_https_host(url, ALLOWED_HOSTS)
    except ValueError as exc:
        raise ProQuestError("ProQuest sign-in left the expected hosts; nothing more was sent.") from exc


def itu_link(docview_id: str) -> str:
    return OPENATHENS_REDIRECTOR + f"https://{PQ_HOST}/docview/{docview_id}"


def _form_fields(form: Any) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for node in form.select("input[name], textarea[name]"):
        if (node.get("type") or "").lower() in {"checkbox", "radio", "submit", "button"}:
            continue
        fields.append((str(node["name"]), str(node.get("value") or node.get_text() or "")))
    return fields


class ProQuestClient:
    def __init__(self, *, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update(DEFAULT_HEADERS)
        self._lock = threading.RLock()
        self._signed_in = False
        self._last_request_at = 0.0
        self._day = date.today()
        self._searches_today = 0
        self._cache: TtlCache[Any] = TtlCache(
            parse_ttl_seconds(os.getenv("NINOVA_PROQUEST_CACHE_TTL_SECONDS"), 1800.0), max_entries=200
        )

    # -- guards --------------------------------------------------------------

    @staticmethod
    def _credentials() -> tuple[str, str]:
        if not proquest_enabled():
            raise ProQuestError(
                f"ProQuest search is off. The operator can turn it on with {ENABLE_ENV}=1; it signs in "
                "with the library account and is meant for personal research use only."
            )
        university_id = (os.getenv("NINOVA_LIBRARY_ID") or "").strip()
        password = os.getenv("NINOVA_LIBRARY_PIN") or ""
        if not university_id or not password:
            raise ProQuestError(
                "ProQuest search needs the library account: NINOVA_LIBRARY_ID (university number) "
                "and NINOVA_LIBRARY_PIN (library password)."
            )
        return university_id, password

    def _daily_limit(self) -> int:
        try:
            return max(1, min(int(os.getenv("NINOVA_PROQUEST_DAILY_SEARCHES") or DEFAULT_DAILY_SEARCHES), 200))
        except ValueError:
            return DEFAULT_DAILY_SEARCHES

    def _count_search(self) -> None:
        today = date.today()
        if today != self._day:
            self._day, self._searches_today = today, 0
        if self._searches_today >= self._daily_limit():
            raise ProQuestError(
                "The daily ProQuest search limit of this server was reached; search in the browser instead."
            )
        self._searches_today += 1

    def _request(
        self, method: str, url: str, *, body_host: str | None = None, search: bool = False, **kwargs: Any
    ) -> requests.Response:
        # Only search submissions are spaced out. The sign-in hops and the page
        # that carries the search form follow each other as they do in a browser.
        if search:
            wait = MIN_INTERVAL_SECONDS - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        try:
            return request_with_safe_redirects(
                self.session,
                method,
                url,
                validate_url=_validate_url,
                body_hosts={body_host} if body_host else set(),
                **kwargs,
            )
        except requests.RequestException as exc:
            raise ProQuestError(f"ProQuest request failed: {exc.__class__.__name__}") from exc

    # -- sign-in -------------------------------------------------------------

    def sign_in(self) -> None:
        university_id, password = self._credentials()
        with self._lock:
            self._signed_in = False
            self.session.cookies.clear()
            page = self._request("GET", SIGN_IN_URL)
            if urlsplit(page.url).hostname != IDP_HOST or not urlsplit(page.url).path.startswith(ITU_IDP_PATH_PREFIX):
                raise ProQuestError("The İTÜ OpenAthens sign-in page did not open; nothing was sent.")
            form = make_soup(page.text).find("form")
            if form is None or form.find("input", attrs={"name": "password"}) is None:
                raise ProQuestError("OpenAthens sign-in form not found; the page may have changed.")
            action = urljoin(page.url, form.get("action") or page.url)
            if urlsplit(action).hostname != IDP_HOST or not urlsplit(action).path.startswith(ITU_IDP_PATH_PREFIX):
                raise ProQuestError("OpenAthens form points outside İTÜ's sign-in; the password was not sent.")
            data = dict(_form_fields(form))
            data["username"] = university_id
            data["password"] = password
            response = self._request("POST", action, data=data, body_host=IDP_HOST, headers={"Referer": page.url})
            response = self._follow_saml(response)
            if urlsplit(response.url).hostname != PQ_HOST:
                error = make_soup(response.text).select_one(".alert, .error, .text-danger")
                detail = clean_text(error.get_text(" ", strip=True)) if error else ""
                raise ProQuestError(
                    "OpenAthens sign-in failed" + (f": {detail}" if detail else "") +
                    ". Check the university number and library password."
                )
            self._signed_in = True

    def _follow_saml(self, response: requests.Response) -> requests.Response:
        for _ in range(4):
            form = make_soup(response.text).find("form")
            if form is None or form.find("input", attrs={"name": "SAMLResponse"}) is None:
                return response
            action = urljoin(response.url, form.get("action") or "")
            # The assertion goes to ProQuest's service provider and nowhere else.
            if urlsplit(action).hostname != SP_HOST:
                raise ProQuestError("SAML response addressed to an unexpected host; it was not sent.")
            response = self._request("POST", action, data=dict(_form_fields(form)), body_host=SP_HOST)
        return response

    # -- search --------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        full_text_only: bool = True,
        peer_reviewed_only: bool = False,
        from_year: int | None = None,
        to_year: int | None = None,
    ) -> dict[str, Any]:
        query = " ".join(str(query or "").split())
        if len(query) < 2 or len(query) > 300:
            raise ProQuestError("Give a search query of 2 to 300 characters.")
        if from_year or to_year:
            low = int(from_year or 1800)
            high = int(to_year or date.today().year)
            query = f"({query}) AND pd({low}-{high})"
        self._credentials()
        cache_key = repr((query, bool(full_text_only), bool(peer_reviewed_only)))
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return {
                    **cached,
                    "cached": True,
                    "searches_left_today": self._daily_limit() - self._searches_today,
                }
            self._count_search()
            for attempt in (0, 1):
                if not self._signed_in:
                    self.sign_in()
                page = self._request("GET", SEARCH_PAGE_URL)
                form = make_soup(page.text).select_one("form#searchForm")
                if form is None or "accountid" not in page.text:
                    self._signed_in = False
                    if attempt == 0:
                        continue
                    raise ProQuestError("ProQuest did not recognise the İTÜ session.")
                action = urljoin(page.url, form.get("action") or "")
                if urlsplit(action).hostname != PQ_HOST:
                    raise ProQuestError("ProQuest search form points to another host.")
                data = [(k, v) for k, v in _form_fields(form) if k != "searchTerm"]
                data.append(("searchTerm", query))
                if full_text_only:
                    data.append(("fullTextLimit", "on"))
                if peer_reviewed_only:
                    data.append(("peerReviewLimit", "on"))
                results = self._request(
                    "POST", action, data=data, body_host=PQ_HOST, search=True, headers={"Referer": page.url}
                )
                parsed = self.parse_results(results.text)
                parsed.update(
                    {
                        "query": query,
                        "searches_left_today": self._daily_limit() - self._searches_today,
                        "access_note": (
                            "itu_url opens the article in the student's browser through the İTÜ "
                            "OpenAthens sign-in. Full text is read there, not downloaded by this tool."
                        ),
                        "untrusted_external_content": True,
                    }
                )
                # An empty page is not cached: it may be a parsing miss, not "no results".
                if parsed["count"]:
                    self._cache.set(cache_key, parsed)
                return {**parsed, "cached": False}
        raise ProQuestError("ProQuest search failed.")

    @staticmethod
    def parse_results(html: str) -> dict[str, Any]:
        soup = make_soup(html)
        total = None
        heading = soup.select_one("#pqResultsCount, .resultsCount, h1")
        if heading is not None:
            match = _COUNT_RE.search(heading.get_text(" ", strip=True))
            if match:
                total = int(re.sub(r"[.,]", "", match.group(1)))
        articles: list[dict[str, Any]] = []
        for item in soup.select("li.resultItem"):
            title_node = item.select_one("h3 a, .truncatedResultsTitle")
            title = clean_text(title_node.get_text(" ", strip=True)) if title_node else ""
            docview = None
            for link in item.select("a[href*='/docview/']"):
                match = _DOCVIEW_RE.search(link.get("href") or "")
                if match:
                    docview = match.group(1)
                    break
            if not title or not docview:
                continue
            author_node = item.select_one(".truncatedAuthor")
            authors = (
                [a.strip() for a in clean_text(author_node.get_text(" ", strip=True)).rstrip(".").split(";") if a.strip() and a.strip().rstrip(".").lower() not in {"vs", "et al", "and others"}]
                if author_node else []
            )
            source_node = item.select_one(".jnlArticle, .newspaperArticle, .dissertpub, .titleAuthorETC span:last-child")
            source = clean_text(source_node.get_text(" ", strip=True)) if source_node else None
            year = None
            if source:
                ym = _YEAR_RE.search(source)
                year = int(ym.group(1) or ym.group(2)) if ym else None
            type_node = item.select_one(".source-type-label")
            text = clean_text(item.get_text(" ", strip=True))
            cited = _CITED_RE.search(text)
            snippets = [clean_text(s.get_text(" ", strip=True)) for s in item.select(".abstract, .snippet, .results-snippets")]
            articles.append(
                {
                    "title": title,
                    "authors": authors[:6],
                    "source": source,
                    "year": year,
                    "source_type": clean_text(type_node.get_text(" ", strip=True)) if type_node else None,
                    "has_full_text": bool(item.select_one("a[href*='/fulltext/'], a[href*='/fulltextPDF/']")),
                    "has_pdf": bool(item.select_one("a[href*='/fulltextPDF/']")),
                    "times_cited": int(cited.group(1)) if cited else None,
                    "snippet": " ".join(snippets)[:SNIPPET_CHARS] or None,
                    "proquest_id": docview,
                    "itu_url": itu_link(docview),
                }
            )
        return {"total_count": total, "count": len(articles), "articles": articles}
