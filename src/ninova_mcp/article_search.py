"""Article-level search in the open OpenAlex index, with İTÜ access links.

The library's licensed databases (ProQuest, Wiley, ScienceDirect...) can only
be searched inside each platform, through the student's own browser session.
OpenAlex indexes the same journal articles openly: title, authors, journal,
year, abstract, DOI, citation count and any open access copy. Each result gets
an ``itu_access_url`` that goes through the library's OpenAthens redirector, so
the article opens with İTÜ's subscription when the student follows it in their
browser. Only ``api.openalex.org`` is called, over https. No İTÜ credentials
are used.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

import requests

from .cache import TtlCache, parse_ttl_seconds
from .client import DEFAULT_HEADERS, DEFAULT_TIMEOUT, NinovaError
from .http_security import request_with_safe_redirects, require_https_host
from .library_databases import web_url

OPENALEX_HOST = "api.openalex.org"
WORKS_URL = f"https://{OPENALEX_HOST}/works"
OPENATHENS_REDIRECTOR = "https://go.openathens.net/redirector/itu.edu.tr?url="
ARTICLE_TYPES = ("article", "review", "book-chapter", "dissertation", "preprint")
SORTS = {"relevance": None, "newest": "publication_date:desc", "most_cited": "cited_by_count:desc"}
ABSTRACT_CHARS = 1200
MAX_AUTHORS = 6
SELECT_FIELDS = (
    "id,doi,title,publication_year,type,authorships,primary_location,"
    "open_access,cited_by_count,abstract_inverted_index,language"
)
_DOI_RE = re.compile(r"^https?://doi\.org/", re.IGNORECASE)


class ArticleSearchError(NinovaError):
    """Article index request or input error."""


def _validate_url(url: str) -> None:
    try:
        require_https_host(url, {OPENALEX_HOST})
    except ValueError as exc:
        raise ArticleSearchError("Only the OpenAlex API can be read.") from exc


def rebuild_abstract(inverted: dict[str, list[int]] | None) -> str:
    """OpenAlex stores abstracts as word -> positions; put the words back in order."""
    if not isinstance(inverted, dict) or not inverted:
        return ""
    slots: dict[int, str] = {}
    for word, positions in inverted.items():
        for position in positions or []:
            if isinstance(position, int) and 0 <= position < 5000:
                slots[position] = word
    return " ".join(slots[i] for i in sorted(slots))


def itu_access_url(doi_url: str | None) -> str | None:
    """Wrap a DOI link in the İTÜ OpenAthens redirector."""
    if not doi_url or not _DOI_RE.match(doi_url):
        return None
    return OPENATHENS_REDIRECTOR + quote(doi_url, safe=":/")


class ArticleSearchClient:
    def __init__(self, *, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update(DEFAULT_HEADERS)
        self._cache: TtlCache[Any] = TtlCache(
            parse_ttl_seconds(os.getenv("NINOVA_ARTICLE_CACHE_TTL_SECONDS"), 1800.0)
        )

    def search(
        self,
        query: str,
        *,
        from_year: int | None = None,
        to_year: int | None = None,
        open_access_only: bool = False,
        article_type: str | None = None,
        sort: str = "relevance",
        limit: int = 10,
        page: int = 1,
    ) -> dict[str, Any]:
        query = " ".join(str(query or "").split())
        if len(query) < 2:
            raise ArticleSearchError("Give a search query of at least two characters.")
        if len(query) > 300:
            raise ArticleSearchError("Search query is too long (300 characters at most).")
        if sort not in SORTS:
            raise ArticleSearchError(f"sort must be one of {', '.join(SORTS)}.")
        if article_type is not None and article_type not in ARTICLE_TYPES:
            raise ArticleSearchError(f"article_type must be one of {', '.join(ARTICLE_TYPES)}.")
        limit = max(1, min(int(limit), 25))
        page = max(1, min(int(page), 40))

        filters = [f"type:{article_type}" if article_type else "type:article|review"]
        if from_year:
            filters.append(f"from_publication_date:{int(from_year)}-01-01")
        if to_year:
            filters.append(f"to_publication_date:{int(to_year)}-12-31")
        if open_access_only:
            filters.append("is_oa:true")
        params: dict[str, Any] = {
            "search": query,
            "filter": ",".join(filters),
            "per_page": limit,
            "page": page,
            "select": SELECT_FIELDS,
        }
        if SORTS[sort]:
            params["sort"] = SORTS[sort]
        api_key = os.getenv("NINOVA_OPENALEX_API_KEY")
        if api_key:
            params["api_key"] = api_key.strip()

        cache_key = repr(sorted((k, v) for k, v in params.items() if k != "api_key"))
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            response = request_with_safe_redirects(
                self.session, "GET", WORKS_URL, validate_url=_validate_url, params=params, timeout=DEFAULT_TIMEOUT
            )
        except requests.RequestException as exc:
            raise ArticleSearchError(f"Article index request failed: {exc.__class__.__name__}") from exc
        if response.status_code == 429:
            raise ArticleSearchError(
                "The open article index rate limit was reached. Try again later, or set "
                "NINOVA_OPENALEX_API_KEY to a free OpenAlex key."
            )
        if response.status_code >= 400:
            raise ArticleSearchError(f"Article index returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ArticleSearchError("Article index returned unreadable data.") from exc

        works = [self._work(raw) for raw in payload.get("results") or [] if isinstance(raw, dict)]
        total = (payload.get("meta") or {}).get("count")
        result = {
            "query": query,
            "total_count": total,
            "page": page,
            "count": len(works),
            "has_more": isinstance(total, int) and page * limit < total,
            "articles": works,
            "access_note": (
                "itu_access_url opens the article through İTÜ's OpenAthens sign-in (library "
                "account) in the student's browser; open_access_url needs no sign-in. "
                "Coverage is OpenAlex's, not the library catalog's."
            ),
            "untrusted_external_content": True,
        }
        return self._cache.set(cache_key, result)

    @staticmethod
    def _work(raw: dict[str, Any]) -> dict[str, Any]:
        authors = [
            ((a.get("author") or {}).get("display_name") or "").strip()
            for a in raw.get("authorships") or []
            if isinstance(a, dict)
        ]
        authors = [a for a in authors if a]
        location = raw.get("primary_location") or {}
        source = location.get("source") or {}
        open_access = raw.get("open_access") or {}
        abstract = rebuild_abstract(raw.get("abstract_inverted_index"))
        doi = raw.get("doi") if isinstance(raw.get("doi"), str) else None
        return {
            "title": " ".join(str(raw.get("title") or "").split()) or None,
            "authors": authors[:MAX_AUTHORS],
            "more_authors": max(0, len(authors) - MAX_AUTHORS),
            "year": raw.get("publication_year"),
            "journal": source.get("display_name"),
            "type": raw.get("type"),
            "language": raw.get("language"),
            "cited_by_count": raw.get("cited_by_count"),
            "doi": doi,
            "abstract": abstract[:ABSTRACT_CHARS] + ("..." if len(abstract) > ABSTRACT_CHARS else ""),
            "is_open_access": bool(open_access.get("is_oa")),
            "open_access_url": web_url(open_access.get("oa_url")),
            "itu_access_url": itu_access_url(doi),
            "openalex_id": raw.get("id"),
        }
