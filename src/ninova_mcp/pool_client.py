"""Public client for the Faculty of Science and Letters pool course sites.

The physics, chemistry and mathematics pool courses (FIZ, KIM, MAT) publish
their syllabi, quiz guides and announcements on their own sites rather than
in Ninova. The sites are public, so no credentials are used. Only the host
allowlist below can be read, over https, with every redirect validated.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from .cache import TtlCache, parse_ttl_seconds
from .client import DEFAULT_HEADERS, DEFAULT_TIMEOUT, NinovaError
from .http_security import request_with_safe_redirects, require_https_host
from .parsing_common import clean_text, make_soup
from .text_extract import MAX_EXTRACT_BYTES, extract_text_from_bytes

POOL_SITES: dict[str, str] = {
    "FIZ": "fizikhavuz.itu.edu.tr",
    "KIM": "kimyahavuz.itu.edu.tr",
    "MAT": "mathavuz.itu.edu.tr",
}
HUB_HOST = "fefhavuz.itu.edu.tr"
POOL_HOSTS = frozenset({*POOL_SITES.values(), HUB_HOST, f"www.{POOL_SITES['FIZ']}"})
_DOCUMENT_EXT_RE = re.compile(r"\.(pdf|docx?|pptx?|txt)$", re.IGNORECASE)
_MATERIAL_PATH_RE = re.compile(r"^/(docs|files)/", re.IGNORECASE)
_COURSE_PAGE_RE = re.compile(r"^/mat/catalog\.php$")
_CODE_RE = re.compile(r"^([A-Za-z]{3})\s*0*(\d{3})([A-Za-z]{0,2})$")
MAX_LINKS_PER_SITE = 80


class PoolSiteError(NinovaError):
    """Pool course site request or input error."""


def _validate_url(url: str) -> None:
    try:
        require_https_host(url, POOL_HOSTS)
    except ValueError as exc:
        raise PoolSiteError("Only the İTÜ pool course sites (fizik, kimya, mat, fef havuz) can be read.") from exc


def normalize_pool_code(raw: str) -> tuple[str, str]:
    """``'fiz 102e'`` -> ``('FIZ', 'FIZ102E')``."""
    match = _CODE_RE.match(raw.strip())
    if not match or match.group(1).upper() not in POOL_SITES:
        raise PoolSiteError("Give a pool course code such as FIZ 102E, KIM 101 or MAT 103E.")
    prefix = match.group(1).upper()
    return prefix, f"{prefix}{match.group(2)}{match.group(3).upper()}"


def _compact(text: str) -> str:
    return re.sub(r"[\s\-_]+", "", text).upper()


class PoolSiteClient:
    def __init__(self, *, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._cache: TtlCache[Any] = TtlCache(
            parse_ttl_seconds(os.getenv("NINOVA_POOL_CACHE_TTL_SECONDS"), 600.0)
        )

    def _get(self, url: str, *, stream: bool = False) -> requests.Response:
        try:
            response = request_with_safe_redirects(
                self.session, "GET", url, validate_url=_validate_url, timeout=DEFAULT_TIMEOUT, stream=stream
            )
        except requests.RequestException as exc:
            raise PoolSiteError(f"Pool site request failed: {exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            raise PoolSiteError(f"Pool site returned HTTP {response.status_code}.")
        return response

    def _site_links(self, host: str) -> list[dict[str, str]]:
        cached = self._cache.get(f"links:{host}")
        if cached is not None:
            return cached
        base = f"https://{host}/"
        response = self._get(base)
        if response.encoding is None or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        soup = make_soup(response.text)
        seen: set[str] = set()
        links: list[dict[str, str]] = []
        for anchor in soup.select("a[href]"):
            url = urljoin(base, anchor.get("href") or "").split("#", 1)[0]
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.hostname not in POOL_HOSTS or url in seen:
                continue
            is_document = bool(_DOCUMENT_EXT_RE.search(parts.path)) and (
                _MATERIAL_PATH_RE.match(parts.path) or parts.hostname == HUB_HOST
            )
            is_course_page = bool(_COURSE_PAGE_RE.match(parts.path))
            if not (is_document or is_course_page):
                continue
            seen.add(url)
            text = clean_text(anchor.get_text(" ", strip=True))
            links.append({"title": text or parts.path.rsplit("/", 1)[-1], "url": url,
                          "kind": "document" if is_document else "course_page"})
            if len(links) >= MAX_LINKS_PER_SITE:
                break
        return self._cache.set(f"links:{host}", links)

    def list_documents(self, course_code: str | None = None) -> dict[str, Any]:
        if course_code:
            prefix, code = normalize_pool_code(course_code)
            hosts = [POOL_SITES[prefix]]
        else:
            prefix, code, hosts = None, None, list(POOL_SITES.values())
        documents: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for host in hosts:
            try:
                links = self._site_links(host)
            except PoolSiteError as exc:
                errors.append({"site": host, "error": str(exc)})
                continue
            for link in links:
                haystack = _compact(f"{link['title']} {urlsplit(link['url']).path} {urlsplit(link['url']).query}")
                if code and _compact(code) not in haystack and link["kind"] == "course_page":
                    continue
                documents.append({"site": host, **link})
        if code:
            # Syllabi are often published once for a course and its English twin
            # under a file name that carries no code (Monday_TR.pdf), so a code
            # filter must not hide a document the title labels with that code.
            titled = [d for d in documents if _compact(code) in _compact(d["title"])]
            general = [d for d in documents if d["kind"] == "document" and d not in titled]
            documents = titled + general
        return {
            "course_code": code,
            "sites": hosts,
            "count": len(documents),
            "documents": documents,
            "errors": errors,
            "untrusted_external_content": True,
        }

    def read_document(self, url: str, max_chars: int = 20_000) -> dict[str, Any]:
        _validate_url(url)
        max_chars = max(1_000, min(int(max_chars), 200_000))
        response = self._get(url, stream=True)
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            total += len(chunk)
            if total > MAX_EXTRACT_BYTES:
                raise PoolSiteError("File is too large for text extraction.")
            chunks.append(chunk)
        data = b"".join(chunks)
        path = urlsplit(response.url).path
        ext = os.path.splitext(path)[1].lower() or ".html"
        extracted = extract_text_from_bytes(data, extension=ext, max_chars=max_chars)
        extracted.update(
            {
                "url": response.url,
                "content_type": response.headers.get("Content-Type"),
                "size_bytes": len(data),
                "untrusted_external_content": True,
            }
        )
        return extracted
