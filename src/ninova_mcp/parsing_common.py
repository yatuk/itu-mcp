"""Shared helpers of the HTML parsers: soup, text, Turkish dates, URLs and file names.

Imports none of the other ``parsing_*`` modules.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from .http_security import reject_unsafe_url_text


def make_soup(html: str) -> BeautifulSoup:
    """Parse HTML with lxml, falling back to the stdlib parser.

    lxml is the declared default (and what the tests run against), but it is a
    compiled extension. In a packaged distribution where the vendored lxml does
    not match the running Python's ABI it would raise ``FeatureNotFound``; the
    fallback keeps the server working with ``html.parser`` instead of crashing.
    """
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


COURSE_PATH_RE = re.compile(r"^/Sinif/\d+\.\d+/?$")
# 4-digit numbers cover capstone/design courses (CEN 4901E); the two-letter tail
# covers English and lab variants such as FIZ 101EL.
COURSE_CODE_RE = re.compile(r"\b[A-Z]{2,}\s*\d{3,4}[A-Z]{0,2}\b")
FILE_EXTENSIONS = {
    ".7z",
    ".csv",
    ".doc",
    ".docx",
    ".gz",
    ".jpeg",
    ".jpg",
    ".md",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".txt",
    ".xls",
    ".xlsx",
    ".zip",
}
DATE_RE = re.compile(r"\d{1,2}\s+[A-Za-zÇĞİÖŞÜçğıöşü]+\s+\d{4}(?:\s+\d{2}:\d{2})?")
FILENAME_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
TURKIYE_TZ = timezone(timedelta(hours=3))
MONTH_NAMES = {
    "ocak": 1,
    "subat": 2,
    "şubat": 2,
    "mart": 3,
    "nisan": 4,
    "mayis": 5,
    "mayıs": 5,
    "haziran": 6,
    "temmuz": 7,
    "agustos": 8,
    "ağustos": 8,
    "eylul": 9,
    "eylül": 9,
    "ekim": 10,
    "kasim": 11,
    "kasım": 11,
    "aralik": 12,
    "aralık": 12,
}


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def normalize_lookup_text(value: str | None) -> str:
    cleaned = clean_text(value).casefold()
    cleaned = cleaned.translate(str.maketrans({"ı": "i", "İ": "i"}))
    cleaned = unicodedata.normalize("NFKD", cleaned)
    return "".join(ch for ch in cleaned if not unicodedata.combining(ch))


def normalize_url(url_or_path: str, base_url: str) -> str:
    """Turn a URL or a Ninova path into an absolute URL.

    Raises ``UnsafeUrlError`` (a ``ValueError``) for text that URL parsers read
    differently: a backslash, whitespace, a control character or user
    information. ``urlparse`` and the HTTP library disagree about the host of
    such a URL, so it is refused here instead of being passed on. This only
    builds the URL; which host may be contacted is decided by the client.
    """
    if not url_or_path:
        raise ValueError("url_or_path is required")
    reject_unsafe_url_text(url_or_path, check_userinfo=False)
    parsed = urlparse(url_or_path)
    if parsed.scheme and parsed.netloc:
        absolute = url_or_path
    elif url_or_path.startswith("/"):
        absolute = f"{base_url.rstrip('/')}{url_or_path}"
    else:
        absolute = urljoin(f"{base_url.rstrip('/')}/", url_or_path)
    reject_unsafe_url_text(absolute)
    return absolute


def is_internal_ninova_url(url: str, base_url: str) -> bool:
    parsed = urlparse(url)
    base = urlparse(base_url)
    return parsed.netloc == base.netloc


def is_course_root(url: str) -> bool:
    return bool(COURSE_PATH_RE.match(urlparse(url).path))


def link_kind(url: str, base_url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme in {"mailto", "tel"}:
        return "external"
    extension = Path(parsed.path).suffix.lower()
    if extension in FILE_EXTENSIONS:
        return "file"
    if is_internal_ninova_url(url, base_url):
        return "page"
    return "external"


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return value or "page"


def sanitize_filename(value: str | None) -> str:
    decoded = unquote(value or "")
    decoded = clean_text(decoded)
    decoded = decoded.replace("\u2215", "/")
    decoded = FILENAME_UNSAFE_RE.sub("-", decoded)
    decoded = re.sub(r"\s+", " ", decoded).strip(" .")
    return decoded or "download"


def parse_ninova_datetime(value: str | None) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None

    match = DATE_RE.search(text)
    if not match:
        return None

    parts = clean_text(match.group(0)).split()
    if len(parts) < 3:
        return None

    day = int(parts[0])
    month_name = normalize_lookup_text(parts[1])
    month = MONTH_NAMES.get(month_name)
    if month is None:
        return None

    year = int(parts[2])
    hour = 0
    minute = 0
    if len(parts) >= 4 and ":" in parts[3]:
        hour, minute = (int(piece) for piece in parts[3].split(":", 1))

    return datetime(year, month, day, hour, minute, tzinfo=TURKIYE_TZ)


def ninova_datetime_iso(value: str | None) -> str | None:
    parsed = parse_ninova_datetime(value)
    return parsed.isoformat() if parsed else None


def split_trailing_ninova_datetime(value: str | None) -> tuple[str | None, str | None, str | None]:
    text = clean_text(value)
    if not text:
        return None, None, None

    match = DATE_RE.search(text)
    if not match:
        return text, None, None

    prefix = clean_text(text[: match.start()])
    date_text = clean_text(match.group(0))
    return prefix or None, date_text, ninova_datetime_iso(date_text)


_DAY_MONTH = r"\d{1,2}\s+[A-Za-zÇĞİÖŞÜçğıöşü]+(?:\s+\d{4})?"
_CLOCK = r"\d{1,2}:\d{2}"
# A start/end cell whose two dates sit above their two times flattens to
# "25 Şubat - 31 Aralık 00:00 18:00": both times trail both dates.
_SPLIT_RANGE_RE = re.compile(rf"({_DAY_MONTH})\s*-\s*({_DAY_MONTH})\s+({_CLOCK})\s+({_CLOCK})")
_ORDERED_RANGE_RE = re.compile(rf"({_DAY_MONTH}(?:\s+{_CLOCK})?)\s*-\s*({_DAY_MONTH}(?:\s+{_CLOCK})?)")


def _is_day_month(value: str) -> bool:
    parts = value.split()
    return len(parts) >= 2 and normalize_lookup_text(parts[1]) in MONTH_NAMES


def split_date_range(value: str | None) -> dict[str, Any] | None:
    """Split a ``start - end`` date cell into its two ends.

    Handles both the readable order (``25 Şubat 00:00 - 31 Aralık 18:00``) and
    the flattened one where the times were appended after both dates. Returns
    ``None`` for anything that is not a range of two Turkish dates. ISO values
    are added only when the source carries a year; Ninova's dashboard does not.
    """
    text = clean_text(value)
    if not text:
        return None
    match = _SPLIT_RANGE_RE.fullmatch(text)
    if match:
        start = f"{clean_text(match.group(1))} {match.group(3)}"
        end = f"{clean_text(match.group(2))} {match.group(4)}"
    else:
        match = _ORDERED_RANGE_RE.fullmatch(text)
        if not match:
            return None
        start, end = clean_text(match.group(1)), clean_text(match.group(2))
    if not (_is_day_month(start) and _is_day_month(end)):
        return None
    result: dict[str, Any] = {"start": start, "end": end, "raw": text}
    for key, part in (("start_iso", start), ("end_iso", end)):
        iso = ninova_datetime_iso(part)
        if iso:
            result[key] = iso
    return result


_YEARLESS_DATE_RE = re.compile(rf"(\d{{1,2}})\s+([A-Za-zÇĞİÖŞÜçğıöşü]+)(?:\s+({_CLOCK}))?")


def infer_yearless_datetime(value: str | None, *, today: date | None = None) -> str | None:
    """ISO form of a ``07 Ekim 15:20`` date, taking the year that puts it nearest to today.

    Returns ``None`` when the text is not exactly such a date (one that names
    its year is parsed by ``ninova_datetime_iso``). The year is a guess, so
    callers mark the result as inferred.
    """
    match = _YEARLESS_DATE_RE.fullmatch(clean_text(value))
    if not match:
        return None
    month = MONTH_NAMES.get(normalize_lookup_text(match.group(2)))
    if month is None:
        return None
    hour, minute = (int(piece) for piece in match.group(3).split(":")) if match.group(3) else (0, 0)
    reference = today or datetime.now(tz=TURKIYE_TZ).date()
    candidates: list[datetime] = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidates.append(datetime(year, month, int(match.group(1)), hour, minute, tzinfo=TURKIYE_TZ))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda item: abs((item.date() - reference).days)).isoformat()


def _looks_like_date(value: str) -> bool:
    return bool(DATE_RE.search(clean_text(value)))


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
