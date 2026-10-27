"""İTÜ Library database list (kutuphane.itu.edu.tr/arastirma/veritabanlari).

The page is filled by a public JSON service. This client reads the full list
once, keeps it in a TTL cache and searches it locally, so a query matches the
database name, its subjects, its publisher and its description, not only the
name as the site's own box does. Only ``service.library.itu.edu.tr`` is read,
over https, with every redirect validated. No credentials are used.
"""

from __future__ import annotations

import html
import os
import re
import unicodedata
from typing import Any

import requests

from .cache import TtlCache, parse_ttl_seconds
from .client import DEFAULT_HEADERS, DEFAULT_TIMEOUT, NinovaError
from .http_security import request_with_safe_redirects, require_https_host

DATABASES_HOST = "service.library.itu.edu.tr"
FULL_LIST_URL = f"https://{DATABASES_HOST}/web/api/Veritabanlari/FullList"
PAGE_URL = "https://kutuphane.itu.edu.tr/arastirma/veritabanlari"
MAX_LIST_BYTES = 8 * 1024 * 1024
SUBJECT_TYPE_ID = 1
CONTENT_TYPE_ID = 2
PUBLISHER_TYPE_ID = 3
DESCRIPTION_CHARS = 320
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_FOLD = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"})


class LibraryDatabasesError(NinovaError):
    """Library database list request or input error."""


def _validate_url(url: str) -> None:
    try:
        require_https_host(url, {DATABASES_HOST})
    except ValueError as exc:
        raise LibraryDatabasesError("Only the İTÜ Library database service can be read.") from exc


def fold(text: str) -> str:
    """Lower-case and strip Turkish diacritics so 'Mühendislik' matches 'muhendislik'."""
    text = unicodedata.normalize("NFKD", text.translate(_FOLD)).lower()
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def plain_text(markup: str | None) -> str:
    if not markup:
        return ""
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", markup))).strip()


def web_url(value: Any) -> str | None:
    """Return the value if it is an http(s) link, else None (no javascript:, data:...)."""
    text = str(value or "").strip()
    return text if re.match(r"^https?://[^\s]+$", text, re.IGNORECASE) else None


def _names(filters: list[dict[str, Any]], type_id: int) -> list[str]:
    seen: list[str] = []
    for item in filters:
        if item.get("FiltreTurId") != type_id:
            continue
        name = item.get("Adi_en") or item.get("Adi")
        if name and name not in seen:
            seen.append(str(name))
    return seen


def _tokens(query: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", fold(query)) if len(t) >= 2]


class LibraryDatabasesClient:
    def __init__(self, *, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._cache: TtlCache[Any] = TtlCache(
            parse_ttl_seconds(os.getenv("NINOVA_LIBRARY_DATABASES_CACHE_TTL_SECONDS"), 21_600.0)
        )

    def _load(self) -> list[dict[str, Any]]:
        cached = self._cache.get("full")
        if cached is not None:
            return cached
        try:
            response = request_with_safe_redirects(
                self.session, "GET", FULL_LIST_URL, validate_url=_validate_url, timeout=DEFAULT_TIMEOUT
            )
            if response.status_code >= 400:
                raise LibraryDatabasesError(f"Library database service returned HTTP {response.status_code}.")
            if len(response.content) > MAX_LIST_BYTES:
                raise LibraryDatabasesError("Library database list is larger than expected.")
            payload = response.json()
        except requests.RequestException as exc:
            raise LibraryDatabasesError(f"Library database request failed: {exc.__class__.__name__}") from exc
        except ValueError as exc:
            raise LibraryDatabasesError("Library database service returned unreadable data.") from exc
        rows = payload.get("Liste") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise LibraryDatabasesError("Library database list has an unexpected shape.")
        records = [self._record(raw) for raw in rows if isinstance(raw, dict)]
        return self._cache.set("full", records)

    @staticmethod
    def _record(raw: dict[str, Any]) -> dict[str, Any]:
        filters = [f for f in (raw.get("Filtreler") or []) if isinstance(f, dict)]
        description = plain_text(raw.get("Aciklama_en") or raw.get("Aciklama"))
        description_tr = plain_text(raw.get("Aciklama"))
        subjects = _names(filters, SUBJECT_TYPE_ID)
        publishers = _names(filters, PUBLISHER_TYPE_ID)
        content_types = _names(filters, CONTENT_TYPE_ID)
        record_id = raw.get("Id")
        return {
            "id": record_id,
            "name": plain_text(raw.get("Adi")),
            "content": raw.get("Icerik") or None,
            "subjects": subjects,
            "publishers": publishers,
            "content_types": content_types,
            "description": description[:DESCRIPTION_CHARS] + ("..." if len(description) > DESCRIPTION_CHARS else ""),
            "url": web_url(raw.get("Link")),
            "off_campus_url": web_url(raw.get("KampusDisiLink")),
            "is_new": bool(raw.get("YeniMi")),
            "open_access": bool(raw.get("AcikErisim")),
            "page_url": f"{PAGE_URL}#{record_id}",
            "_haystack": {
                "name": fold(plain_text(raw.get("Adi"))),
                "tags": fold(" ".join(subjects + publishers + content_types)),
                "text": fold(f"{description} {description_tr}"),
            },
        }

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in record.items() if not k.startswith("_")}

    def search(
        self,
        query: str | None = None,
        subject: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 50))
        records = self._load()
        tokens = _tokens(query or "")
        subject_fold = fold(subject).strip() if subject else ""
        scored: list[tuple[int, dict[str, Any]]] = []
        for record in records:
            hay = record["_haystack"]
            if subject_fold and not any(subject_fold in fold(s) for s in record["subjects"]):
                continue
            score = 0
            if tokens:
                # Any word may match; a database that matches more words, or
                # matches in its name or subjects, ranks higher.
                hits = [t for t in tokens if t in f"{hay['name']} {hay['tags']} {hay['text']}"]
                if not hits:
                    continue
                score = 20 * len(hits) + sum(6 * (t in hay["name"]) + 3 * (t in hay["tags"]) + (t in hay["text"]) for t in hits)
            scored.append((score, record))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["name"].lower()))
        matches = [self._public(record) for _, record in scored]
        result: dict[str, Any] = {
            "query": query or None,
            "subject": subject or None,
            "total_databases": len(records),
            "match_count": len(matches),
            "databases": matches[:limit],
            "truncated": len(matches) > limit,
            "page_url": PAGE_URL,
            "access_note": (
                "On campus the url works directly. Off campus use off_campus_url and sign in "
                "with the library OpenAthens account (library membership required)."
            ),
            "untrusted_external_content": True,
        }
        if not tokens and not subject_fold:
            counts: dict[str, int] = {}
            for record in records:
                for name in record["subjects"]:
                    counts[name] = counts.get(name, 0) + 1
            result["subjects"] = [{"name": n, "count": c} for n, c in sorted(counts.items())]
        return result
