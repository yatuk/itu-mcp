from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

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


def _with_inferred_dates(rows: list[Any], today: date | None) -> list[Any]:
    """Add ``date_iso`` to dashboard rows whose date has no year.

    A header-less two-cell row (text, date) becomes a small record so the date
    is a field of its own. Start/end ranges are left alone: the nearest-year
    rule is not safe for a window that can span most of a year.
    """
    result: list[Any] = []
    for row in rows:
        if isinstance(row, list) and len(row) == 2 and infer_yearless_datetime(row[1], today=today):
            row = {"text": row[0], "date": row[1]}
        if isinstance(row, dict) and "date_iso" not in row:
            for value in list(row.values()):
                iso = infer_yearless_datetime(value, today=today) if isinstance(value, str) else None
                if iso:
                    row = {**row, "date_iso": iso, "date_year_inferred": True}
                    break
        result.append(row)
    return result


def _iter_headings(soup: BeautifulSoup) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    for tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        for tag in soup.find_all(tag_name):
            text = clean_text(tag.get_text(" ", strip=True))
            if text:
                headings.append({"level": int(tag_name[1]), "text": text})
    return headings


def _extract_link_context(anchor: Tag) -> str:
    context = clean_text(anchor.parent.get_text(" ", strip=True))
    if len(context) > 240:
        context = context[:237] + "..."
    return context


def _unique_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for link in links:
        key = (link["url"], link["text"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(link)
    return unique


def _extract_links(soup: BeautifulSoup, base_url: str, page_url: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for anchor in soup.find_all("a", href=True):
        # Surrounding whitespace is dropped and inner whitespace encoded, so a
        # sloppy href is still a usable link instead of a refused URL.
        href = re.sub(r"\s", "%20", str(anchor.get("href") or "").strip())
        if not href or href.startswith("javascript:"):
            continue
        absolute = urljoin(page_url, href)
        text = clean_text(anchor.get_text(" ", strip=True)) or clean_text(anchor.get("title")) or absolute
        links.append(
            {
                "text": text,
                "url": absolute,
                "kind": link_kind(absolute, base_url),
                "context": _extract_link_context(anchor),
            }
        )
    return _unique_links(links)


def _table_to_rows(table: Tag) -> dict[str, Any]:
    # Nested date/layout tables belong to their enclosing cell, not this table.
    own_rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
    headers = []
    rows: list[list[str]] = []
    for row in own_rows:
        nodes = row.find_all(["td", "th"], recursive=False)
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in nodes]
        if cells:
            if not rows and all(cell.name == "th" for cell in nodes):
                headers = cells
            rows.append(cells)

    # A start/end cell is rewritten in reading order, and its two ends are
    # also given separately under "date_ranges" (with the original text).
    ranges_by_row: list[dict[int, dict[str, Any]]] = []
    for row in rows:
        found: dict[int, dict[str, Any]] = {}
        for position, cell_text in enumerate(row):
            date_range = split_date_range(cell_text)
            if date_range:
                found[position] = date_range
                row[position] = f"{date_range['start']} - {date_range['end']}"
        ranges_by_row.append(found)

    structured_rows: list[dict[str, Any] | list[str]] = []
    if headers and rows and len(headers) == len(rows[0]):
        for row, found in zip(rows[1:], ranges_by_row[1:], strict=True):
            structured: dict[str, Any] = dict(zip(headers, row, strict=False))
            named = {headers[position]: value for position, value in found.items() if position < len(headers)}
            if named and "date_ranges" not in structured:
                structured["date_ranges"] = named
            structured_rows.append(structured)
    else:
        structured_rows = rows

    return {
        "headers": headers,
        "rows": structured_rows,
    }


def _extract_tables(soup: BeautifulSoup) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for index, table in enumerate(soup.find_all("table"), start=1):
        title = None
        caption = table.find("caption")
        if caption:
            title = clean_text(caption.get_text(" ", strip=True))
        tables.append({"index": index, "title": title, **_table_to_rows(table)})
    return tables


def parse_html_page(url: str, html: str, base_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    body_text = clean_text(soup.get_text(" ", strip=True))
    links = _extract_links(soup, base_url=base_url, page_url=url)
    tables = _extract_tables(soup)
    headings = _iter_headings(soup)
    attachments = [link for link in links if link["kind"] == "file"]
    text_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()

    return {
        "url": url,
        "title": title,
        "headings": headings,
        "links": links,
        "attachments": attachments,
        "tables": tables,
        "text": body_text,
        "text_hash": text_hash,
        "text_excerpt": body_text[:4000],
    }


def _context_from_anchor(anchor: Tag) -> str:
    node: Tag | None = anchor.parent if isinstance(anchor.parent, Tag) else anchor
    for _ in range(4):
        if node is None:
            break
        text = clean_text(node.get_text(" ", strip=True))
        if text and len(text) < 300 and text != clean_text(anchor.get_text(" ", strip=True)):
            return text
        node = node.parent if isinstance(node.parent, Tag) else None
    return clean_text(anchor.get_text(" ", strip=True))


def _extract_course_title(anchor: Tag) -> str:
    anchor_text = clean_text(anchor.get_text(" ", strip=True))
    script = anchor.find_next_sibling("script")
    if script:
        script_text = script.string or script.get_text(" ", strip=True)
        match = re.search(r"font-weight:bold;\">([^<]+)<", script_text)
        if match:
            return clean_text(match.group(1))
    return anchor_text


_YEAR_LIKE_NUMBER_RE = re.compile(r"(?:19|20)\d{2}")
_COURSE_GROUP_RE = re.compile(r"/Sinif/(\d+)\.\d+/?$")
_CRN_LABEL_RE = re.compile(r"\bCRN\s*:?\s*(\d{3,6})\b", re.I)


def _course_group(url: str) -> str | None:
    """First id of ``/Sinif/<course>.<section>``: shared by sections of one course."""
    match = _COURSE_GROUP_RE.search(urlparse(url).path)
    return match.group(1) if match else None


def _is_year_like_code(code: str) -> bool:
    """``CEN 2025`` reads as a branch followed by a year, not as a course number."""
    number = re.sub(r"^[A-Z]+\s*", "", code)
    return bool(_YEAR_LIKE_NUMBER_RE.fullmatch(number))


def _pick_code(text: str) -> tuple[str | None, bool]:
    """Return ``(code, uncertain)`` from text, never asserting a year-like number."""
    candidates = [clean_text(match) for match in COURSE_CODE_RE.findall(text)]
    for candidate in candidates:
        if not _is_year_like_code(candidate):
            return candidate, False
    return None, bool(candidates)


def _course_own_text(anchor: Tag, page_url: str, base_url: str) -> str:
    """Text that belongs to the anchor's course but to none of its section links.

    Section links carry free-text labels ("CEN 2025 Summer Internship"), so
    the course code is looked for outside them. The walk goes up from the
    anchor and stops before a container that also holds another course.
    """
    group = _course_group(urljoin(page_url, anchor["href"]))

    def is_course_anchor(tag: Tag) -> bool:
        absolute = urljoin(page_url, tag.get("href") or "")
        return is_internal_ninova_url(absolute, base_url) and is_course_root(absolute)

    def outside_section_links(piece: Any, container: Tag) -> bool:
        for parent in piece.parents:
            if parent.name in {"script", "style"} or (parent.name == "a" and is_course_anchor(parent)):
                return False
            if parent is container:
                break
        return True

    node = anchor.parent
    for _ in range(5):
        if not isinstance(node, Tag) or node.name in {"body", "html", "[document]"}:
            break
        if any(
            is_course_anchor(other) and _course_group(urljoin(page_url, other["href"])) != group
            for other in node.find_all("a", href=True)
        ):
            break
        text = clean_text(" ".join(
            str(piece) for piece in node.find_all(string=True) if outside_section_links(piece, node)
        ))
        if COURSE_CODE_RE.search(text):
            return text
        node = node.parent
    return ""


def extract_courses(html: str, page_url: str, base_url: str) -> list[dict[str, Any]]:
    """List the course links of the dashboard, one row per enrolled section.

    ``code`` comes from the text next to the section links, not from the link
    labels. When only a label is available the code read from it is flagged
    with ``code_source: "section_label"``, and a branch followed by a year is
    never reported as a code (``code`` is null and ``code_uncertain`` true).
    ``section`` and ``crn`` tell two sections of one course apart.
    """
    soup = make_soup(html)
    courses: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(page_url, anchor["href"])
        if not is_internal_ninova_url(absolute, base_url) or not is_course_root(absolute):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        context = _context_from_anchor(anchor)
        title = _extract_course_title(anchor)
        label = clean_text(anchor.get_text(" ", strip=True))
        crn_match = _CRN_LABEL_RE.search(label)

        code, uncertain = _pick_code(_course_own_text(anchor, page_url, base_url))
        source = "course_element" if code else None
        if code is None:
            code, label_uncertain = _pick_code(context)
            uncertain = uncertain or label_uncertain
            source = "section_label" if code else None
        course: dict[str, Any] = {
            "code": code,
            "title": title,
            "url": absolute,
            "context": context,
            "section": label if label and label != title else None,
            "crn": crn_match.group(1) if crn_match else None,
        }
        if source == "section_label" or (code is None and uncertain):
            if source:
                course["code_source"] = source
            course["code_uncertain"] = True
        courses.append(course)
    return courses


def extract_named_table(html: str, heading_text: str) -> list[dict[str, Any] | list[str]]:
    soup = make_soup(html)
    matcher = normalize_lookup_text(heading_text)
    target: Tag | None = None
    for tag in soup.find_all(True):
        text = normalize_lookup_text(tag.get_text(" ", strip=True))
        if text == matcher:
            target = tag
            break
    if target is None:
        return []
    table = target.find_next("table")
    if table is None:
        return []
    return _table_to_rows(table)["rows"]


def summarize_dashboard(
    page_data: dict[str, Any], html: str, base_url: str, *, today: date | None = None,
) -> dict[str, Any]:
    def table(heading: str) -> list[Any]:
        return _with_inferred_dates(extract_named_table(html, heading), today)

    return {
        "page": {
            "url": page_data["url"],
            "title": page_data["title"],
            "headings": page_data["headings"],
        },
        "courses": extract_courses(html, page_data["url"], base_url=base_url),
        "recent_announcements": table("Son Duyurular"),
        "recent_assignments": table("Son Odevler"),
        "recent_messages": table("Son Mesajlar"),
        "links": page_data["links"],
    }


def _looks_like_date(value: str) -> bool:
    return bool(DATE_RE.search(clean_text(value)))


def _extract_labeled_value(text: str, label: str, next_labels: list[str]) -> str | None:
    normalized = clean_text(text)
    pattern = re.escape(label) + r"\s*(.*?)\s*(?=" + "|".join(re.escape(item) for item in next_labels) + r"|$)"
    match = re.search(pattern, normalized, flags=re.DOTALL)
    if not match:
        return None
    value = clean_text(match.group(1))
    return value or None


def _remove_once(value: str, token: str | None) -> str:
    if not token:
        return value
    index = value.find(token)
    if index == -1:
        return value
    return value[:index] + value[index + len(token) :]


def _join_virtual_path(parent_path: str, name: str) -> str:
    parent = parent_path.rstrip("/") or "/"
    if parent == "/":
        return f"/{name}"
    return f"{parent}/{name}"


def _find_table_by_headers(soup: BeautifulSoup, headers: list[str]) -> Tag | None:
    target = [normalize_lookup_text(item) for item in headers]
    for table in soup.find_all("table"):
        row_headers = [
            normalize_lookup_text(cell.get_text(" ", strip=True))
            for cell in table.find_all("th")
        ]
        if row_headers == target:
            return table
    return None


def _extract_assignment_counts(cell: Tag) -> tuple[int | None, int | None]:
    raw_text = clean_text(cell.get_text(" ", strip=True))
    counts_match = re.search(
        r"toplam\s+(\d+)\s+adet dosyanin?\s+(\d+)\s+adedini",
        normalize_lookup_text(raw_text),
        flags=re.IGNORECASE,
    )
    if counts_match:
        return int(counts_match.group(1)), int(counts_match.group(2))

    strong_values = [
        int(match.group(0))
        for strong in cell.select("strong.uyari")
        if (match := re.search(r"\d+", clean_text(strong.get_text(" ", strip=True))))
    ]
    if len(strong_values) >= 2:
        return strong_values[0], strong_values[1]
    return None, None


def extract_announcements_list(html: str, page_url: str, base_url: str) -> list[dict[str, Any]]:
    soup = make_soup(html)
    items: list[dict[str, Any]] = []
    cards = soup.select("div.duyuruGoruntule")
    if not cards:
        # Fallback: any block that looks like an announcement card with h2>a to Duyuru.
        cards = [
            anchor.find_parent("div")
            for anchor in soup.select("h2 a[href*='Duyuru'], h2 a[href*='duyuru']")
            if anchor.find_parent("div") is not None
        ]
    for card in cards:
        if card is None:
            continue
        title_anchor = card.select_one("h2 a[href]")
        if not title_anchor:
            continue
        title = clean_text(title_anchor.get_text(" ", strip=True))
        url = urljoin(page_url, title_anchor["href"])

        content = card.select_one("div.icerik")
        content_text = clean_text(content.get_text(" ", strip=True) if content else "")
        course_title = None
        class_name = None
        class_url = None
        if content:
            strong = content.find("strong")
            if strong:
                course_title = clean_text(strong.get_text(" ", strip=True))
            class_anchor = content.find("a", href=True)
            if class_anchor and "/Sinif/" in class_anchor["href"] and "/Duyuru/" not in class_anchor["href"]:
                class_name = clean_text(class_anchor.get_text(" ", strip=True))
                class_url = urljoin(page_url, class_anchor["href"])

        date_candidates = [
            clean_text(span.get_text(" ", strip=True))
            for span in card.select("span.tarih")
            if clean_text(span.get_text(" ", strip=True))
        ]
        published_at = next((item for item in date_candidates if _looks_like_date(item)), None)
        author = None
        trailing_spans = [
            clean_text(span.get_text(" ", strip=True))
            for span in card.select("div.tarih span.tarih")
            if clean_text(span.get_text(" ", strip=True))
        ]
        if trailing_spans:
            author = trailing_spans[-1]
            if _looks_like_date(author) and len(trailing_spans) > 1:
                author = trailing_spans[-2]

        summary = content_text
        summary = _remove_once(summary, course_title)
        summary = _remove_once(summary, class_name)
        summary = _remove_once(summary, published_at)
        summary = clean_text(summary.strip("/ "))

        items.append(
            {
                "title": title,
                "url": url,
                "course_title": course_title,
                "class_name": class_name,
                "class_url": class_url,
                "published_at": published_at,
                "author": author,
                "summary": summary,
            }
        )
    return items


_HELP_BOX_RE = re.compile(r"\bYardım\s+Diğer sınıflarınıza\b")


def extract_announcement_detail(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    page = parse_html_page(page_url, html, base_url=base_url)
    title = page["headings"][0]["text"] if page["headings"] else page["title"]
    published_at: str | None = None
    body_text: str | None = None

    # The announcement has its own card; reading it keeps the page's help box
    # and footer, which also say "Yardım", out of the body.
    soup = make_soup(html)
    card = soup.select_one("div.duyuruGoruntule")
    content = card.select_one("div.icerik") if card else None
    if card is not None and content is not None:
        heading = card.find(["h1", "h2", "h3"])
        if heading is not None and clean_text(heading.get_text(" ", strip=True)):
            title = clean_text(heading.get_text(" ", strip=True))
        for span in card.select("span.tarih"):
            candidate = clean_text(span.get_text(" ", strip=True))
            if published_at is None and _looks_like_date(candidate):
                published_at = candidate
                if content in span.parents:
                    span.decompose()
        body_text = clean_text(content.get_text(" ", strip=True))

    if body_text is None:
        body_text = page["text"]
        if title in body_text:
            body_text = body_text.split(title, 1)[1]
        published_at_match = DATE_RE.search(body_text)
        published_at = published_at_match.group(0) if published_at_match else None
        if published_at:
            body_text = body_text.split(published_at, 1)[1]
        help_box = _HELP_BOX_RE.search(body_text)
        if help_box:
            body_text = body_text[: help_box.start()]
        elif "Yardım" in body_text:
            body_text = body_text.rsplit("Yardım", 1)[0]
    return {
        "title": title,
        "url": page["url"],
        "published_at": published_at,
        "published_at_iso": ninova_datetime_iso(published_at),
        "body_text": clean_text(body_text),
    }


def extract_assignments_list(html: str, page_url: str, base_url: str) -> list[dict[str, Any]]:
    soup = make_soup(html)
    # Prefer the known GridView id, then class="data", then any table that contains
    # an assignment-style h2+link cell (Ninova has changed markup before).
    table = soup.find("table", id=re.compile("gvOdevListesi", re.I))
    if table is None:
        table = soup.find("table", class_=re.compile(r"\bdata\b", re.I))
    if table is None:
        for candidate in soup.find_all("table"):
            if candidate.select_one("h2 a[href*='Odev'], h2 a[href*='odev']"):
                table = candidate
                break
    if table is None:
        return []

    items: list[dict[str, Any]] = []
    for cell in table.find_all("td"):
        title_anchor = cell.select_one("h2 a[href]")
        if not title_anchor:
            continue
        title = clean_text(title_anchor.get_text(" ", strip=True))
        url = urljoin(page_url, title_anchor["href"])
        raw_text = clean_text(cell.get_text(" ", strip=True))
        course_title = _extract_labeled_value(raw_text, "Ders :", ["Sınıf :", "Teslim Başlangıcı :"])
        class_name = _extract_labeled_value(raw_text, "Sınıf :", ["Teslim Başlangıcı :"])
        submission_start = _extract_labeled_value(raw_text, "Teslim Başlangıcı :", ["Teslim Bitişi :"])
        submission_end = _extract_labeled_value(
            raw_text,
            "Teslim Bitişi :",
            ["Ödevde istenen toplam", "Ödevi Görüntüle"],
        )
        requested_file_count, uploaded_file_count = _extract_assignment_counts(cell)
        class_url = None
        for anchor in cell.find_all("a", href=True):
            href = anchor["href"]
            if "/Sinif/" in href and "/Odev/" not in href:
                class_url = urljoin(page_url, href)
                break
        upload_anchor = next(
            (anchor for anchor in cell.find_all("a", href=True) if "Ödevi Yükle" in clean_text(anchor.get_text(" ", strip=True))),
            None,
        )
        items.append(
            {
                "title": title,
                "url": url,
                "course_title": course_title,
                "class_name": class_name,
                "class_url": class_url,
                "submission_start": submission_start,
                "submission_end": submission_end,
                "requested_file_count": requested_file_count,
                "uploaded_file_count": uploaded_file_count,
                "upload_url": urljoin(page_url, upload_anchor["href"]) if upload_anchor else None,
            }
        )
    return items


def extract_assignment_detail(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    page = parse_html_page(page_url, html, base_url=base_url)
    text = page["text"]
    title = page["headings"][0]["text"] if page["headings"] else page["title"]
    submission_start = _extract_labeled_value(text, "Teslim Başlangıcı", ["Teslim Bitişi", "Ödevi Yükle"])
    submission_end = _extract_labeled_value(text, "Teslim Bitişi", ["Ödevi Yükle", "Ödev Açıklaması"])
    description = _extract_labeled_value(text, "Ödev Açıklaması", [
        "Yüklediğiniz ödev dosyalarını indirin", "Kaynak Dosyalar", "İstenen Dosyalar", "Yardım",
    ])

    source_files: list[dict[str, Any]] = []
    source_table = _find_table_by_headers(soup, ["Dosyalar", "Boyut", "Tarih"])
    if source_table is not None:
        for row in source_table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            anchor = cells[0].find("a", href=True)
            source_files.append(
                {
                    "name": clean_text(cells[0].get_text(" ", strip=True)),
                    "url": urljoin(page_url, anchor["href"]) if anchor else None,
                    "size": clean_text(cells[1].get_text(" ", strip=True)),
                    "date": clean_text(cells[2].get_text(" ", strip=True)),
                }
            )

    required_files: list[dict[str, Any]] = []
    required_table = _find_table_by_headers(soup, ["Açıklama", "Uzantılar"])
    if required_table is not None:
        for row in required_table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            required_files.append(
                {
                    "description": clean_text(cells[0].get_text(" ", strip=True)),
                    "extensions": clean_text(cells[1].get_text(" ", strip=True)),
                }
            )

    upload_anchor = next(
        (anchor for anchor in soup.find_all("a", href=True) if "Ödevi Yükle" in clean_text(anchor.get_text(" ", strip=True))),
        None,
    )

    return {
        "title": title,
        "url": page["url"],
        "submission_start": submission_start,
        "submission_end": submission_end,
        "description": description,
        "source_files": source_files,
        "required_files": required_files,
        "upload_url": urljoin(page_url, upload_anchor["href"]) if upload_anchor else None,
    }


def _parse_upload_row(cells: list[Tag], page_url: str, *, index: int) -> dict[str, Any]:
    status_text = clean_text(cells[0].get_text(" ", strip=True))
    normalized_status = normalize_lookup_text(status_text)
    anchors = [
        anchor
        for anchor in cells[0].find_all("a", href=True)
        if not clean_text(anchor.get("href", "")).startswith("javascript:")
    ]
    file_anchor = next((anchor for anchor in anchors if clean_text(anchor.get_text(" ", strip=True))), None)
    uploaded = False
    if "henuz gondermediniz" in normalized_status:
        uploaded = False
    elif any(token in normalized_status for token in ("gonderdiniz", "yuklediniz", "teslim ettiniz")):
        uploaded = True
    elif file_anchor is not None:
        uploaded = True

    description = None
    strong = cells[0].find("strong")
    if strong:
        description = clean_text(strong.get_text(" ", strip=True))

    file_input = cells[0].find("input", attrs={"type": "file"}) or (
        cells[1].find("input", attrs={"type": "file"}) if len(cells) > 1 else None
    )
    if file_input is None:
        for cell in cells:
            file_input = cell.find("input", attrs={"type": "file"})
            if file_input is not None:
                break

    extensions = clean_text(cells[1].get_text(" ", strip=True)) if len(cells) > 1 else None
    # Prefer extensions cell without the file input text noise.
    if len(cells) > 1 and file_input is not None and file_input in cells[1].descendants:
        # extensions usually live in last non-file cell; keep raw cell text.
        pass

    allowed_extensions: list[str] = []
    if extensions:
        for piece in re.split(r"[;,\s]+", extensions):
            piece = piece.strip().lower()
            if piece.startswith("*."):
                allowed_extensions.append(piece[1:])  # ".pdf"
            elif piece.startswith(".") and len(piece) > 1:
                allowed_extensions.append(piece)

    return {
        "index": index,
        "description": description or status_text,
        "extensions": extensions,
        "allowed_extensions": allowed_extensions,
        "status_text": status_text,
        "uploaded": uploaded,
        "file_name": clean_text(file_anchor.get_text(" ", strip=True)) if file_anchor else None,
        "file_url": urljoin(page_url, file_anchor["href"]) if file_anchor else None,
        "field_name": file_input.get("name") if file_input is not None else None,
        "field_id": file_input.get("id") if file_input is not None else None,
    }


def extract_assignment_upload_status(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    table = _find_table_by_headers(soup, ["Açıklama", "Uzantılar"])
    if table is None:
        return {}

    requested_file_count = 0
    uploaded_file_count = 0
    upload_items: list[dict[str, Any]] = []

    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        requested_file_count += 1
        item = _parse_upload_row(cells, page_url, index=requested_file_count)
        if item["uploaded"]:
            uploaded_file_count += 1
        upload_items.append(item)

    return {
        "requested_file_count": requested_file_count,
        "uploaded_file_count": uploaded_file_count,
        "upload_items": upload_items,
    }


def extract_assignment_upload_form(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    """Parse the ASP.NET multipart assignment upload form (OdevGonder)."""
    soup = make_soup(html)
    form = soup.find("form")
    if form is None:
        return {
            "url": page_url,
            "ok": False,
            "error": "No HTML form found on the upload page.",
        }

    action = form.get("action") or page_url
    form_url = urljoin(page_url, action)
    hidden_fields: dict[str, str] = {}
    for input_tag in form.select("input[type='hidden'][name]"):
        name = input_tag.get("name")
        if name:
            hidden_fields[name] = input_tag.get("value") or ""

    # Submit control is usually a LinkButton: javascript:__doPostBack('...$lbEkle','')
    submit_event_target = None
    submit_label = None
    for anchor in form.find_all("a", href=True):
        href = anchor.get("href") or ""
        text = clean_text(anchor.get_text(" ", strip=True))
        match = re.search(r"__doPostBack\(\s*'([^']+)'\s*,\s*'([^']*)'\s*\)", href)
        if not match:
            continue
        target = match.group(1)
        if "lbEkle" in target or normalize_lookup_text(text) in {
            "odevi gonder",
            "odevi gonder",
            "gonder",
            "submit",
            "upload",
        }:
            submit_event_target = target
            submit_label = text or target
            break
        if submit_event_target is None and "ContentPlaceHolder1" in target and "Header" not in target:
            # Fallback: first content postback that is not language switch.
            submit_event_target = target
            submit_label = text or target

    if submit_event_target is None:
        # Some themes render a real submit button.
        for input_tag in form.select("input[type='submit'][name], button[name]"):
            submit_event_target = input_tag.get("name")
            submit_label = input_tag.get("value") or clean_text(input_tag.get_text(" ", strip=True))
            break

    status = extract_assignment_upload_status(html, page_url, base_url=base_url)
    slots = status.get("upload_items") or []

    page = parse_html_page(page_url, html, base_url=base_url)
    title = page["headings"][0]["text"] if page["headings"] else page["title"]

    return {
        "ok": True,
        "url": page_url,
        "form_url": form_url,
        "title": title,
        "enctype": form.get("enctype") or "multipart/form-data",
        "method": (form.get("method") or "post").lower(),
        "hidden_fields": hidden_fields,
        "submit_event_target": submit_event_target,
        "submit_label": submit_label,
        "requested_file_count": status.get("requested_file_count", len(slots)),
        "uploaded_file_count": status.get("uploaded_file_count", 0),
        "slots": slots,
        "error": None if submit_event_target else "Could not find the assignment submit control (Ödevi Gönder).",
    }


def match_upload_slot(
    slots: list[dict[str, Any]],
    *,
    slot_index: int | None = None,
    slot_description: str | None = None,
) -> dict[str, Any]:
    """Resolve a single upload slot by 1-based index or fuzzy description."""
    if not slots:
        raise ValueError("No upload slots found on the assignment page.")

    if slot_index is not None:
        for slot in slots:
            if slot.get("index") == slot_index:
                return slot
        raise ValueError(
            f"Upload slot index {slot_index} not found. "
            f"Valid indices: {', '.join(str(s.get('index')) for s in slots)}"
        )

    if slot_description:
        target = normalize_lookup_text(slot_description)
        exact = [
            slot
            for slot in slots
            if target and target == normalize_lookup_text(slot.get("description"))
        ]
        if len(exact) == 1:
            return exact[0]
        fuzzy = [
            slot
            for slot in slots
            if target and target in normalize_lookup_text(slot.get("description"))
        ]
        if len(fuzzy) == 1:
            return fuzzy[0]
        if len(fuzzy) > 1 or len(exact) > 1:
            options = ", ".join(f"{s.get('index')}:{s.get('description')}" for s in (fuzzy or exact))
            raise ValueError(f"Ambiguous slot description {slot_description!r}. Matches: {options}")
        options = ", ".join(f"{s.get('index')}:{s.get('description')}" for s in slots)
        raise ValueError(f"No slot matching {slot_description!r}. Available: {options}")

    if len(slots) == 1:
        return slots[0]
    options = ", ".join(f"{s.get('index')}:{s.get('description')}" for s in slots)
    raise ValueError(
        "Multiple upload slots exist; pass slot_index or slot_description. "
        f"Available: {options}"
    )


def extension_allowed(filename: str, allowed_extensions: list[str] | None) -> bool:
    if not allowed_extensions:
        return True
    suffix = Path(filename).suffix.lower()
    if not suffix:
        return False
    return suffix in {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in allowed_extensions}


def extract_file_directory(
    html: str,
    page_url: str,
    base_url: str,
    current_path: str = "/",
) -> dict[str, Any]:
    soup = make_soup(html)
    table = _find_table_by_headers(soup, ["Dosyalar", "Boyut", "Tarih"])
    entries: list[dict[str, Any]] = []
    if table is not None:
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            if len(cells) == 1 and "herhangi bir dosya bulunmamaktadır" in normalize_lookup_text(cells[0].get_text(" ", strip=True)):
                continue
            if len(cells) < 3:
                continue
            anchor = cells[0].find("a", href=True)
            if anchor is None:
                continue
            icon = cells[0].find("img")
            icon_src = icon.get("src", "") if icon else ""
            icon_name = Path(urlparse(icon_src).path).stem
            entry_type = "folder" if "folder" in icon_name else "file"
            name = clean_text(anchor.get_text(" ", strip=True))
            entry_path = _join_virtual_path(current_path, name)
            entries.append(
                {
                    "name": name,
                    "path": entry_path,
                    "parent_path": current_path,
                    "entry_type": entry_type,
                    "icon": icon_name,
                    "size": clean_text(cells[1].get_text(" ", strip=True)),
                    "date": clean_text(cells[2].get_text(" ", strip=True)),
                    "url": urljoin(page_url, anchor["href"]),
                    "download_url": urljoin(page_url, anchor["href"]) if entry_type == "file" else None,
                }
            )
    return {
        "current_path": current_path,
        "url": page_url,
        "entries": entries,
    }


def _is_noise_table(table: dict[str, Any]) -> bool:
    joined = clean_text(" ".join(" ".join(row) if isinstance(row, list) else " ".join(str(value) for value in row.values()) for row in table["rows"]))
    normalized = normalize_lookup_text(joined)
    return (
        "hos geldiniz" in normalized
        or "dersler yardim hakkinda ninova" in normalized
    )


def _pairs_from_rows(rows: list[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    extras: list[list[str]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        if len(row) == 2:
            result[row[0]] = row[1]
        else:
            extras.append(row)
    if extras:
        result["_extras"] = extras
    return result


def extract_course_info(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    page = parse_html_page(page_url, html, base_url=base_url)
    tables = [table for table in page["tables"] if not _is_noise_table(table)]
    identity: dict[str, Any] = {}
    class_meta: dict[str, Any] = {}
    weekly_schedule: list[dict[str, str]] = []
    course_details: dict[str, Any] = {}
    weekly_plan: list[dict[str, Any] | list[str]] = []

    if len(tables) >= 1:
        for row in tables[0]["rows"]:
            if not isinstance(row, list):
                continue
            if row[:2] == ["Ders Adı", "Türkçe"] and len(row) >= 3:
                identity["Ders Adı Türkçe"] = row[2]
            elif row and row[0] == "İngilizce" and len(row) >= 2:
                identity["Ders Adı İngilizce"] = row[1]
            elif len(row) == 2:
                identity[row[0]] = row[1]
            else:
                identity.setdefault("_extras", []).append(row)

    if len(tables) >= 2:
        class_meta = _pairs_from_rows(tables[1]["rows"])

    if len(tables) >= 3:
        for row in tables[2]["rows"]:
            if isinstance(row, list) and len(row) >= 2:
                weekly_schedule.append({"time": row[0], "location": row[1]})

    if len(tables) >= 4:
        details_rows = tables[3]["rows"]
        structured_details: dict[str, Any] = {}
        extras: list[list[str]] = []
        for row in details_rows:
            if not isinstance(row, list):
                continue
            if len(row) == 2:
                structured_details[row[0]] = row[1]
            else:
                extras.append(row)
        if extras:
            structured_details["_extras"] = extras
        course_details = structured_details

    if len(tables) >= 5:
        weekly_plan = tables[4]["rows"]

    return {
        "url": page["url"],
        "title": page["title"],
        "headings": page["headings"],
        "identity": identity,
        "class_meta": class_meta,
        "weekly_schedule": weekly_schedule,
        "course_details": course_details,
        "weekly_plan": weekly_plan,
    }


def extract_course_sections(html: str, page_url: str, base_url: str) -> list[dict[str, Any]]:
    soup = make_soup(html)
    course_path_match = re.search(r"(/Sinif/\d+\.\d+)", urlparse(page_url).path)
    if not course_path_match:
        return []

    course_path = course_path_match.group(1)
    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(page_url, anchor["href"])
        parsed_path = urlparse(absolute).path
        if not (parsed_path == course_path or parsed_path.startswith(course_path + "/")):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        text = clean_text(anchor.get_text(" ", strip=True))
        if not text:
            continue
        section_path = parsed_path[len(course_path) :].strip("/")
        sections.append(
            {
                "name": text,
                "path": section_path or "/",
                "url": absolute,
            }
        )
    return sections


def extract_gradebook(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    page = parse_html_page(page_url, html, base_url=base_url)

    target_table: Tag | None = None
    for table in soup.find_all("table"):
        headers = [clean_text(cell.get_text(" ", strip=True)) for cell in table.find_all("th")]
        if headers and any(normalize_lookup_text(header) == "not" for header in headers):
            target_table = table
            break

    student_name = None
    weighted_average = None
    grade_items: list[dict[str, Any]] = []
    if target_table is not None:
        headers = [clean_text(cell.get_text(" ", strip=True)) for cell in target_table.find_all("th")]
        if headers:
            first_header = headers[0]
            if normalize_lookup_text(first_header) not in {"not", "aciklama", "açıklama"}:
                student_name = first_header

        for row in target_table.find_all("tr")[1:]:
            cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            if len(cells) < 2:
                continue
            label = cells[0]
            score = cells[1]
            description = cells[2] if len(cells) >= 3 else None
            if normalize_lookup_text(label) == "agirlikli ortalamaniz":
                weighted_average = score or None
                continue
            grade_items.append(
                {
                    "title": label,
                    "score": score or None,
                    "description": description or None,
                }
            )

    return {
        "url": page["url"],
        "student_name": student_name,
        "weighted_average": weighted_average,
        "count": len(grade_items),
        "grades": grade_items,
    }


def extract_message_board(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    table = _find_table_by_headers(soup, ["Mesaj Başlığı", "Son Mesaj"])
    topics: list[dict[str, Any]] = []
    if table is not None:
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            title_anchor = cells[0].find("a", href=True)
            title = clean_text(title_anchor.get_text(" ", strip=True)) if title_anchor else clean_text(cells[0].get_text(" ", strip=True))
            topic_text = clean_text(cells[0].get_text(" ", strip=True))
            last_message_text = clean_text(cells[1].get_text(" ", strip=True))
            author, last_message_at, last_message_at_iso = split_trailing_ninova_datetime(last_message_text)
            topics.append(
                {
                    "title": title,
                    "summary": topic_text if topic_text != title else None,
                    "url": urljoin(page_url, title_anchor["href"]) if title_anchor else None,
                    "last_message_text": last_message_text,
                    "last_message_author": author,
                    "last_message_at": last_message_at,
                    "last_message_at_iso": last_message_at_iso,
                }
            )

    return {
        "url": page_url,
        "count": len(topics),
        "topics": topics,
    }


def extract_message_thread_detail(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    page = parse_html_page(page_url, html, base_url=base_url)
    table = _find_table_by_headers(soup, ["Gönderen", "Mesaj"])
    posts: list[dict[str, Any]] = []
    if table is not None:
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            sender_text = clean_text(cells[0].get_text(" ", strip=True))
            if normalize_lookup_text(sender_text) == "mesaj":
                continue
            author, sent_at, sent_at_iso = split_trailing_ninova_datetime(sender_text)
            posts.append(
                {
                    "author": author or sender_text,
                    "sent_at": sent_at,
                    "sent_at_iso": sent_at_iso,
                    "body_text": clean_text(cells[1].get_text(" ", strip=True)),
                }
            )

    return {
        "title": page["headings"][0]["text"] if page["headings"] else page["title"],
        "url": page["url"],
        "count": len(posts),
        "posts": posts,
        "attachments": page["attachments"],
    }


def extract_attendance(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    page = parse_html_page(page_url, html, base_url=base_url)

    student_name = None
    student_table = _find_table_by_headers(soup, ["Öğrenci"])
    if student_table is not None:
        rows = student_table.find_all("tr")
        if len(rows) >= 2:
            cells = rows[1].find_all("td")
            if cells:
                student_name = clean_text(cells[0].get_text(" ", strip=True))

    attendance_table: Tag | None = None
    for table in soup.find_all("table"):
        headers = [clean_text(cell.get_text(" ", strip=True)) for cell in table.find_all("th")]
        if headers and normalize_lookup_text(headers[0]) == "hafta":
            attendance_table = table
            break

    # Ninova pages show attendance from two independent systems (native Ninova
    # and Kepler) under separate headings; instructors may use either one, and
    # the page itself warns the two can disagree. Report which table this data
    # actually came from so callers don't mistake Kepler data for a native
    # Ninova record.
    source_system = None
    if attendance_table is not None:
        if attendance_table is _table_after_heading(soup, "Kepler Yoklama Bilgileri"):
            source_system = "kepler"
        elif attendance_table is _table_after_heading(soup, "Ninova Yoklama Bilgileri"):
            source_system = "ninova"

    disclaimer_tag = soup.find(class_="uyari")
    disclaimer = clean_text(disclaimer_tag.get_text(" ", strip=True)) if disclaimer_tag else None

    # The grid prints 0 both for "absent" and for a session nobody has entered
    # yet (a whole term of future weeks shows as zeros). A zero is therefore
    # counted as an absence only in a week that also holds a 1, which shows
    # attendance was taken that week. Every other zero is "unmarked or absent".
    headers: list[str] = []
    weeks: list[dict[str, Any]] = []
    if attendance_table is not None:
        headers = [clean_text(cell.get_text(" ", strip=True)) for cell in attendance_table.find_all("th")]
        for row in attendance_table.find_all("tr")[1:]:
            nodes = row.find_all("td")
            cells = [clean_text(cell.get_text(" ", strip=True)) for cell in nodes]
            if not cells:
                continue
            sessions = []
            for column_name, value, node in zip(headers[1:], cells[1:], nodes[1:], strict=False):
                marks = re.findall(r"[01]", value)
                session: dict[str, Any] = {
                    "name": column_name,
                    "value": value,
                    "present_count": marks.count("1"),
                    "zero_count": marks.count("0"),
                }
                # Tooltips or image labels, when the page has them, are passed
                # on untouched: they may say what a zero means.
                hints = [
                    clean_text(str(tag.get(attribute)))
                    for tag in [node, *node.find_all(True)]
                    for attribute in ("title", "alt")
                    if tag.get(attribute)
                ]
                if hints:
                    session["hints"] = list(dict.fromkeys(hints))
                sessions.append(session)
            recorded = any(session["present_count"] for session in sessions)
            for session in sessions:
                zeros = session.pop("zero_count")
                session["absent_count"] = zeros if recorded else 0
                session["unmarked_or_absent_count"] = 0 if recorded else zeros
            weeks.append(
                {
                    "week": cells[0],
                    "status": "recorded" if recorded else "unmarked_or_absent",
                    "sessions": sessions,
                }
            )

    result: dict[str, Any] = {
        "url": page["url"],
        "student_name": student_name,
        "source_system": source_system,
        "disclaimer": disclaimer,
        "headers": headers,
        "count": len(weeks),
        "weeks": weeks,
    }
    _total_attendance(result)
    return result


_WEEK_NUMBER_RE = re.compile(r"(\d{1,2})")


def _total_attendance(result: dict[str, Any]) -> None:
    sessions = [session for week in result["weeks"] for session in week["sessions"]]
    result["total_present_marks"] = sum(session["present_count"] for session in sessions)
    result["total_absent_marks"] = sum(session["absent_count"] for session in sessions)
    result["total_unmarked_or_absent_marks"] = sum(session["unmarked_or_absent_count"] for session in sessions)
    future = sum(session.get("future_count", 0) for session in sessions)
    if future or "total_future_marks" in result:
        result["total_future_marks"] = future
    result["absence_counts_reliable"] = result["total_unmarked_or_absent_marks"] == 0
    result.pop("note", None)
    if not result["absence_counts_reliable"]:
        result["note"] = (
            "The attendance grid shows 0 both for an absence and for a session that has not been "
            "entered. Zeros in weeks without any recorded 1 are counted in "
            "total_unmarked_or_absent_marks, not as absences; total_absent_marks covers only weeks "
            "where attendance was demonstrably taken, so it can be too low. Do not report "
            "unmarked_or_absent or future marks as absences."
        )


def apply_attendance_calendar(result: dict[str, Any], *, term_start: date | None, today: date | None = None) -> dict[str, Any]:
    """Date the weeks from the term start and take future weeks out of the counts.

    Week ``N`` is taken to start ``7 * (N - 1)`` days after ``term_start``. A
    week that starts after ``today`` cannot hold an absence, whatever the grid
    prints, so its marks move to ``future_count``.
    """
    result["term_start"] = term_start.isoformat() if term_start else None
    if term_start is None:
        return result
    reference = today or datetime.now(tz=TURKIYE_TZ).date()
    for week in result["weeks"]:
        number = _WEEK_NUMBER_RE.search(str(week.get("week") or ""))
        if not number or int(number.group(1)) < 1:
            continue
        week_start = term_start + timedelta(days=7 * (int(number.group(1)) - 1))
        week["week_start"] = week_start.isoformat()
        if week_start > reference:
            week["status"] = "future"
            for session in week["sessions"]:
                session["future_count"] = (
                    session["present_count"] + session["absent_count"] + session["unmarked_or_absent_count"]
                )
                session["present_count"] = session["absent_count"] = session["unmarked_or_absent_count"] = 0
        else:
            for session in week["sessions"]:
                session.setdefault("future_count", 0)
    result["total_future_marks"] = 0
    _total_attendance(result)
    return result


def _table_after_heading(soup: BeautifulSoup, heading_text: str) -> Tag | None:
    matcher = normalize_lookup_text(heading_text)
    for tag in soup.find_all(True):
        if normalize_lookup_text(tag.get_text(" ", strip=True)) != matcher:
            continue
        return tag.find_next("table")
    return None


def _extract_remote_session_rows(table: Tag, page_url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    own_rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
    headers = [clean_text(cell.get_text(" ", strip=True)) for row in own_rows
               for cell in row.find_all("th", recursive=False)]
    def empty_state(text: str) -> bool:
        normalized = normalize_lookup_text(text)
        return not normalized or any(marker in normalized for marker in (
            "herhangi bir uzaktan egitim oturumu bulunmamaktadir",
            "no remote learning sessions", "no sessions found",
        ))
    if not headers:
        for row in own_rows:
            cells = row.find_all("td", recursive=False)
            if len(cells) != 1:
                continue
            text = clean_text(cells[0].get_text(" ", strip=True))
            if not empty_state(text):
                item = {"text": text, "title": text}
                anchor = cells[0].find("a", href=True)
                if anchor is not None:
                    item["meeting_url"] = urljoin(page_url, str(anchor["href"]))
                rows.append(item)
        return rows

    for row in own_rows:
        cells = row.find_all("td", recursive=False)
        if not cells or empty_state(row.get_text(" ", strip=True)):
            continue
        item: dict[str, Any] = {}
        for header, cell in zip(headers, cells, strict=False):
            item[header] = clean_text(cell.get_text(" ", strip=True)) or None
            anchor = cell.find("a", href=True)
            if anchor is not None:
                item[f"{header}_url"] = urljoin(page_url, anchor["href"])
                if any(word in normalize_lookup_text(header) for word in ("baglanti", "link", "katil")):
                    item["meeting_url"] = item[f"{header}_url"]
            key = normalize_lookup_text(header)
            if key in {"baslik", "oturum", "title", "session"}:
                item["title"] = item[header]
            elif key in {"baslangic", "baslangic tarihi", "start", "start time"}:
                item["start_at"] = ninova_datetime_iso(item[header])
            elif key in {"bitis", "bitis tarihi", "end", "end time"}:
                item["end_at"] = ninova_datetime_iso(item[header])
        rows.append(item)
    return rows


def extract_remote_learning(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    page = parse_html_page(page_url, html, base_url=base_url)
    active_table = _table_after_heading(soup, "Aktif Uzaktan Eğitim Oturumlarınız")
    past_table = _table_after_heading(soup, "Sınıfın Geçmiş Uzaktan Eğitim Oturumları")

    active_sessions = _extract_remote_session_rows(active_table, page_url) if active_table is not None else []
    past_sessions = _extract_remote_session_rows(past_table, page_url) if past_table is not None else []

    return {
        "url": page["url"],
        "active_count": len(active_sessions),
        "past_count": len(past_sessions),
        "active_sessions": active_sessions,
        "past_sessions": past_sessions,
    }


# ---------------------------------------------------------------------------
# OBS public page parsers (no auth needed)
# ---------------------------------------------------------------------------


def extract_course_select_options(html: str, page_url: str) -> list[dict[str, Any]]:
    """Parse the ``<select id="DersBransKoduId">`` dropdown on OBS prerequisite pages.

    Returns a list of ``{"value": "304", "text": "BBF - Bilgisayar Bilimleri ..."}``.
    """
    soup = make_soup(html)
    select = soup.find("select", id="DersBransKoduId")
    if select is None:
        return []
    options: list[dict[str, Any]] = []
    for option in select.find_all("option"):
        value = (option.get("value") or "").strip()
        text = clean_text(option.get_text(" ", strip=True))
        if value:
            options.append({"value": value, "text": text})
    return options


def extract_course_search_results(html: str, page_url: str) -> list[dict[str, Any]]:
    """Parse the public course information results, including nested tables.

    Tries table-based extraction first (headers like "Ders Kodu", "Ders Adı"),
    then falls back to link-list scanning.
    """
    soup = make_soup(html)
    results: list[dict[str, Any]] = []
    from .archive import COURSE_CODE_PATTERN, normalize_course_code

    # Strategy 1: look for a table with "Ders Kodu" header
    target_headers = {"ders kodu", "ders adı", "ders adi", "course code", "course name"}
    for table in soup.find_all("table"):
        own_headers = [th for th in table.find_all("th") if th.find_parent("table") is table]
        th_texts = {normalize_lookup_text(th.get_text(" ", strip=True)) for th in own_headers}
        if not th_texts & target_headers:
            continue
        headers = [clean_text(th.get_text(" ", strip=True)) for th in own_headers]
        for row in table.find_all("tr"):
            if row.find_parent("table") is not table:
                continue
            cells = row.find_all("td", recursive=False)
            if len(cells) < 2:
                continue
            item: dict[str, Any] = {}
            for idx, cell in enumerate(cells):
                key = headers[idx] if idx < len(headers) else f"col_{idx}"
                anchor = cell.find("a", href=True)
                item[key] = clean_text(cell.get_text(" ", strip=True))
                if anchor:
                    item[f"{key}_url"] = urljoin(page_url, anchor["href"])
            # Normalise common keys
            code = (
                item.get("Ders Kodu")
                or item.get("Ders Kodu_EN")
                or item.get("Course Code")
                or ""
            )
            name = (
                item.get("Ders Adı")
                or item.get("Ders Adi")
                or item.get("Course Name")
                or ""
            )
            codes = list(dict.fromkeys(normalize_course_code(match.group(0))
                                       for match in COURSE_CODE_PATTERN.finditer(code)))
            for canonical in codes:
                results.append({**item, "code": canonical, "name": name, "url": page_url})
        if results:
            return results

    # Strategy 2: look for links containing /public/DersBilgi/ or similar patterns
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        if "/DersBilgi/" not in href and "/DersOnsart" not in href:
            continue
        text = clean_text(anchor.get_text(" ", strip=True))
        if not text:
            continue
        context = clean_text(anchor.parent.get_text(" ", strip=True)) if anchor.parent else text
        code_match = COURSE_CODE_RE.search(context)
        results.append({
            "code": code_match.group(0) if code_match else None,
            "name": text if text != (code_match.group(0) if code_match else "") else context,
            "url": urljoin(page_url, href),
        })

    return results


def extract_prerequisite_list(
    html: str,
    page_url: str,
    base_url: str,
) -> dict[str, Any]:
    """Parse the OBS prerequisite detail/chain page.

    Returns structured prerequisite data with a ``raw_tables`` fallback.
    """
    page = parse_html_page(page_url, html, base_url=base_url)
    tables_data = page.get("tables") or []

    prerequisites: list[dict[str, Any]] = []
    parse_warnings: list[str] = []

    # Look for a table whose headers suggest prerequisite content.
    prereq_headers = {"on sart", "onsart", "ders kodu", "ders adi", "grup no", "grup", "tip", "tur"}
    for table_data in tables_data:
        headers_lower = {normalize_lookup_text(h) for h in (table_data.get("headers") or [])}
        if not headers_lower & prereq_headers:
            continue

        headers = table_data.get("headers") or []
        rows = table_data.get("rows") or []
        for row in rows:
            if isinstance(row, list):
                # Try to map to headers
                if len(headers) == len(row) and headers:
                    entry = dict(zip(headers, row, strict=False))
                else:
                    entry = {"_cells": row}
            elif isinstance(row, dict):
                entry = dict(row)
            else:
                continue

            # Normalise common field names
            code = (
                entry.get("Ders Kodu")
                or entry.get("On Sart Ders Kodu")
                or entry.get("DersKodu")
                or ""
            )
            name = (
                entry.get("Ders Adı")
                or entry.get("Ders Adi")
                or entry.get("On Sart Ders Adı")
                or ""
            )
            group = entry.get("Grup No") or entry.get("Grup") or None
            prereq_type = entry.get("Tip") or entry.get("Tür") or entry.get("Tur") or None
            prerequisites.append({
                "code": clean_text(code) if code else None,
                "name": clean_text(name) if name else None,
                "group": clean_text(group) if group else None,
                "type": clean_text(prereq_type) if prereq_type else None,
                "_raw": entry,
            })

    if not prerequisites:
        # Check for inline text mentioning prerequisites
        body_text = page.get("text") or ""
        if any(token in normalize_lookup_text(body_text) for token in ("on sart", "onsart", "on kosul")):
            parse_warnings.append(
                "Sayfada önşart metni bulundu ancak yapılandırılmış tablo çıkarılamadı. "
                "Ham tablo verisine bakın."
            )

    return {
        "url": page["url"],
        "title": page["title"],
        "prerequisites": prerequisites,
        "parse_warnings": parse_warnings or None,
        "raw_tables": tables_data,
        "text_excerpt": page.get("text_excerpt", "")[:3000],
    }


# Filler the schedule page prints where it has no value: dashes, the
# "-/-" of an empty time range, and the building code "undeclared"
# ("İlgili Bölümce Açıklanacak").
_SCHEDULE_PLACEHOLDER_RE = re.compile(r"[-\u2013\u2014/\s]*")
_SCHEDULE_RESERVATION_SEGMENT_RE = re.compile(r"(?P<program>[^/|]+?)\s*/\s*(?P<quota>\d+)\s*/\s*(?P<enrolled>\d+)")


def _schedule_value(value: str | None) -> str | None:
    """Return a schedule cell's text, or None when it is only a placeholder."""
    text = clean_text(value)
    if _SCHEDULE_PLACEHOLDER_RE.fullmatch(text) or text.casefold() == "undeclared":
        return None
    return text


def _parse_schedule_reservation(raw: str | None) -> tuple[list[dict[str, Any]], bool]:
    """Split ``"CEN_LS/8/10 | Diğer/41/37"`` into programme quota segments.

    Each segment is ``PROGRAM/quota/enrolled``; "Diğer" is the general pool.
    ``free`` is clamped at zero because a programme can be over its quota.
    Returns ``(segments, parsed)``; an unrecognized shape gives ``([], False)``
    so the caller can fall back instead of trusting a partial reading.
    """
    segments: list[dict[str, Any]] = []
    for part in (raw or "").split("|"):
        match = _SCHEDULE_RESERVATION_SEGMENT_RE.fullmatch(part.strip())
        if match is None:
            return [], False
        quota, enrolled = int(match.group("quota")), int(match.group("enrolled"))
        program = clean_text(match.group("program"))
        segments.append({
            "program": program,
            "general_pool": normalize_lookup_text(program) in {"diger", "other", "others"},
            "quota": quota,
            "enrolled": enrolled,
            "free": max(0, quota - enrolled),
        })
    return segments, bool(segments)


def extract_course_schedule_table(
    html: str,
    page_url: str,
    base_url: str,
) -> dict[str, Any]:
    """Parse the OBS ``DersProgramSearch`` result HTML table.

    Returns a dict with ``courses`` list, where each course has structured
    ``sessions`` (split on ``<br/>`` separators).

    Placeholder cells (``-``, ``--``, ``-/-``, ``undeclared``) and link texts
    ("Detay") are never passed on as values: they become None, and the section
    carries ``unscheduled`` (no day and time at all) or
    ``location_announced_later`` (a day and time, but no building or room).
    ``reservation`` keeps the page's raw text; ``reservations`` is the same
    line parsed into programme quotas. ``section_kind`` is ``"extra_exam"``
    for the "Ek Sınav" rows, which are exam rights and not sections one can
    register for.
    """
    soup = make_soup(html)
    table = soup.find("table", id="dersProgramContainer")
    if table is None:
        # Fallback: look for any table with CRN header
        for t in soup.find_all("table"):
            headers = [clean_text(td.get_text(" ", strip=True)) for td in (t.find("thead") or t).find_all("td")]
            if headers and normalize_lookup_text(headers[0]) == "crn":
                table = t
                break
    if table is None:
        # Check for empty result: the page injected a "no data" message
        body_text = clean_text(soup.get_text(" ", strip=True))
        if "bulunamad" in body_text or "kayit" in body_text:
            return {
                "url": page_url,
                "count": 0,
                "courses": [],
                "message": "Bu dönem için henüz program açıklanmamış veya bölüm kodu geçersiz.",
            }
        return {
            "url": page_url,
            "count": 0,
            "courses": [],
            "parse_warning": "Ders programı tablosu bulunamadı; OBS sayfa yapısı değişmiş olabilir.",
        }

    # Headers are in <td> inside <thead>, not <th> (unusual)
    thead = table.find("thead")
    header_cells = thead.find_all("td") if thead else []
    headers = [clean_text(cell.get_text(" ", strip=True)) for cell in header_cells]

    tbody = table.find("tbody")
    if tbody is None:
        return {"url": page_url, "count": 0, "courses": [], "headers": headers}

    courses: list[dict[str, Any]] = []
    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 10:
            continue

        cell_texts = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]

        crn = cell_texts[0] if len(cell_texts) > 0 else None

        # Ders Kodu: extract from link
        code_anchor = cells[1].find("a", href=True) if len(cells) > 1 else None
        code_text = clean_text(code_anchor.get_text(" ", strip=True)) if code_anchor else (cell_texts[1] if len(cell_texts) > 1 else None)
        code_href = code_anchor["href"] if code_anchor else None

        # Parse bransKodu and dersNo from href
        brans_kodu = None
        ders_no = None
        if code_href:
            from urllib.parse import parse_qs, urlparse as _urlparse
            qs = parse_qs(_urlparse(code_href).query)
            brans_kodu = (qs.get("bransKodu") or [None])[0]
            ders_no = (qs.get("dersNo") or [None])[0]

        name = cell_texts[2] if len(cell_texts) > 2 else None
        method = cell_texts[3] if len(cell_texts) > 3 else None
        instructor = _schedule_value(cell_texts[4]) if len(cell_texts) > 4 else None

        # Bina / Gün / Saat / Derslik: split on <br/>
        def _split_br(cell_idx: int) -> list[str]:
            if len(cells) <= cell_idx:
                return []
            inner = cells[cell_idx].decode_contents() if hasattr(cells[cell_idx], "decode_contents") else str(cells[cell_idx])
            parts_html = re.split(r"<br\s*/?\s*>", inner, flags=re.IGNORECASE)
            parts: list[str | None] = []
            for p in parts_html:
                # Strip any HTML tags (e.g. <a href="...">BBB</a> → BBB)
                text = re.sub(r"<[^>]+>", "", p)
                text = clean_text(text)
                if text:
                    # Placeholders keep their position so that the columns
                    # still line up session by session.
                    parts.append(_schedule_value(text))
            return parts

        bldgs = _split_br(5)  # Bina: may contain <a> links
        days = _split_br(6)   # Gün
        times = _split_br(7)  # Saat
        rooms = _split_br(8)  # Derslik

        # Build sessions list
        max_sessions = max(len(bldgs), len(days), len(times), len(rooms))
        sessions: list[dict[str, str | None]] = []
        for i in range(max_sessions):
            session = {
                "building": bldgs[i] if i < len(bldgs) else None,
                "day": days[i] if i < len(days) else None,
                "time": times[i] if i < len(times) else None,
                "room": rooms[i] if i < len(rooms) else None,
            }
            if any(session.values()):
                sessions.append(session)
        timed = [s for s in sessions if s["day"] and s["time"]]
        unscheduled = not timed
        location_announced_later = any(not s["building"] or not s["room"] for s in timed)

        capacity = None
        enrolled = None
        try:
            capacity = int(cell_texts[9]) if len(cell_texts) > 9 and cell_texts[9].lstrip("-").isdigit() else None
        except ValueError:
            pass
        try:
            enrolled = int(cell_texts[10]) if len(cell_texts) > 10 and cell_texts[10].lstrip("-").isdigit() else None
        except ValueError:
            pass

        reservation = _schedule_value(cell_texts[11]) if len(cell_texts) > 11 else None
        reservations, reservation_parsed = _parse_schedule_reservation(reservation)

        # Dersi Alabilen Programlar
        eligible_programs: list[str] = []
        eligible_text = cell_texts[12] if len(cell_texts) > 12 else None
        if len(cells) > 12:
            eligible_anchor = cells[12].find("a", href=True)
            if eligible_anchor:
                eligible_programs = [
                    p.strip() for p in clean_text(eligible_anchor.get_text(" ", strip=True)).split(",") if p.strip()
                ]
            elif eligible_text and eligible_text != "-":
                eligible_programs = [p.strip() for p in eligible_text.split(",") if p.strip()]
        # OBS sometimes repeats the same program code in this cell's source text.
        eligible_programs = list(dict.fromkeys(p for p in eligible_programs if _schedule_value(p)))

        # Ders Önşartları: "Detay" link or "-"
        detay_url = None
        has_prerequisites = False
        if len(cells) > 13:
            detay_anchor = cells[13].find("a", href=True)
            if detay_anchor:
                detay_url = detay_anchor["href"]
                has_prerequisites = True
            else:
                prereq_text = cell_texts[13] if len(cell_texts) > 13 else None
                has_prerequisites = bool(prereq_text and prereq_text not in ("-", ""))

        # Başarılan Kredi/Sınıf Önşartı: "-" or a "Detay" link to the course page.
        # The link text is not a rule, so only free text is reported as one.
        credit_prereq = None
        credit_prereq_url = None
        if len(cells) > 14:
            credit_anchor = cells[14].find("a", href=True)
            if credit_anchor:
                credit_prereq_url = credit_anchor["href"]
            else:
                credit_prereq = _schedule_value(cell_texts[14])

        section_kind = "extra_exam" if normalize_lookup_text(method) == "ek sinav" else "regular"

        courses.append({
            "crn": crn,
            "code": code_text,
            "brans_kodu": brans_kodu,
            "ders_no": ders_no,
            "name": name,
            "method": method,
            "instructor": instructor,
            "section_kind": section_kind,
            "sessions": sessions,
            "unscheduled": unscheduled,
            "location_announced_later": location_announced_later,
            "capacity": capacity,
            "enrolled": enrolled,
            "reservation": reservation,
            "reservations": reservations,
            **({"reservation_unparsed": True} if reservation and not reservation_parsed else {}),
            "eligible_programs": eligible_programs,
            "has_prerequisites": has_prerequisites,
            "prerequisite_detail_url": detay_url,
            "has_credit_prerequisite": bool(credit_prereq or credit_prereq_url),
            "credit_prerequisite": credit_prereq,
            "credit_prerequisite_detail_url": credit_prereq_url,
        })

    return {
        "url": page_url,
        "headers": headers,
        "count": len(courses),
        "courses": courses,
    }


def extract_academic_calendar(html: str, page_url: str) -> dict[str, Any]:
    """Parse official calendar events while retaining source coverage and times."""
    from .academic_calendar import parse_academic_calendar

    return parse_academic_calendar(html, page_url)


def extract_campus_card_info(
    html: str,
    page_url: str,
    base_url: str = "",
) -> dict[str, Any]:
    """Parse the İTÜ Portal campus card widget.

    Selectors (portal ``/apps/default/``):
    - Balance: ``div[data-placement="balance"]``
    - Transactions: ``ul[data-placement="transitions"] li.card-deposit__list-item``
    """
    soup = make_soup(html)

    balance: str | None = None
    transactions: list[dict[str, Any]] = []

    # Balance
    balance_el = soup.select_one('div[data-placement="balance"]')
    if balance_el:
        balance = clean_text(balance_el.get_text(" ", strip=True))

    # Transactions
    tx_container = soup.select_one('ul[data-placement="transitions"]')
    if tx_container:
        for li in tx_container.select("li.card-deposit__list-item"):
            spans = li.find_all("span", class_="amount")
            icon = li.find("span", class_=True)
            icon_class = ""
            if icon:
                classes = icon.get("class", [])
                icon_class = next((c for c in classes if c != "amount" and "icon" in c), "")

            tx_type: str | None = None
            if "spending" in icon_class:
                tx_type = "Harcama"
            elif "charge" in icon_class:
                tx_type = "Yükleme"

            amounts = [clean_text(s.get_text(" ", strip=True)) for s in spans]
            text = clean_text(li.get_text(" ", strip=True))

            # First row is header "Bakiye Tutar", skip it
            if normalize_lookup_text(text).startswith("bakiye"):
                continue

            transactions.append({
                "type": tx_type,
                "amounts": amounts,
                "description": text,
            })

    return {
        "url": page_url,
        "balance": balance,
        "transactions": transactions[:20],
        "transaction_count": len(transactions),
    }


def extract_notifications(
    html: str,
    page_url: str,
) -> dict[str, Any]:
    """Parse the İTÜ Portal notifications widget.

    Selectors (portal ``/apps/default/``):
    - List: ``ul[data-placement="notification-list"] li.notification__list-item``
    """
    soup = make_soup(html)
    items: list[dict[str, Any]] = []

    container = soup.select_one('ul[data-placement="notification-list"]')
    if container:
        for li in container.select("li.notification__list-item"):
            anchor = li.find("a", href=True)
            title_span = anchor.find("span", class_="pull-left") if anchor else None
            time_span = anchor.find("span", class_="pull-right") if anchor else None
            title = clean_text(title_span.get_text(" ", strip=True)) if title_span else None
            time_ago = clean_text(time_span.get_text(" ", strip=True)) if time_span else None
            is_unread = "notification__list-item--unread" in (li.get("class") or [])
            notif_id = anchor.get("data-notification-id") if anchor else None
            if title:
                items.append({
                    "title": title,
                    "time_ago": time_ago,
                    "unread": is_unread,
                    "notification_id": notif_id,
                })

    return {
        "url": page_url,
        "count": len(items),
        "notifications": items[:20],
    }


def extract_help_tickets(
    html: str,
    page_url: str,
) -> dict[str, Any]:
    """Parse all help widget rows before applying tool filters and limits."""
    from .help_tickets import parse_widget

    return parse_widget(html, page_url)


def extract_cafeteria_menu(
    html: str,
    page_url: str,
    base_url: str = "https://portal.itu.edu.tr",
) -> dict[str, Any]:
    """Parse the İTÜ Portal cafeteria menu widget from ``/apps/default/``.

    Selectors:
    - Title: ``span[data-placement="food-title"]``
    - Items: ``ul[data-placement="food-list"] li.lunch-menu__list-item``
    - Radio (öğle/akşam): ``#radio-ogle`` / ``#radio-aksam``
    - Date: ``#food-date``
    - Vegetarian checkbox: ``#checkbox-vejeteryan-vegan``
    """
    soup = make_soup(html)

    # Meal type (öğle/akşam)
    meal_type: str | None = None
    oglen_radio = soup.select_one("#radio-ogle")
    aksam_radio = soup.select_one("#radio-aksam")
    if oglen_radio and "checked" in (oglen_radio.get("checked") or ""):
        meal_type = "Öğle Yemeği"
    elif aksam_radio and "checked" in (aksam_radio.get("checked") or ""):
        meal_type = "Akşam Yemeği"
    # Fallback: read the title
    if not meal_type:
        title_el = soup.select_one('span[data-placement="food-title"]')
        if title_el:
            meal_type = clean_text(title_el.get_text(" ", strip=True))

    # Title
    title_el = soup.select_one('span[data-placement="food-title"]')
    title = clean_text(title_el.get_text(" ", strip=True)) if title_el else None

    # Date
    date: str | None = None
    date_input = soup.select_one("#food-date")
    if date_input:
        date = date_input.get("value") or None

    # Vegetarian option
    vegan_cb = soup.select_one("#checkbox-vejeteryan-vegan")
    vegetarian_available = vegan_cb is not None
    vegetarian_selected = bool(vegan_cb and "checked" in (vegan_cb.get("checked") or ""))

    # Menu items
    items: list[dict[str, Any]] = []
    food_list = soup.select_one('ul[data-placement="food-list"]')
    if food_list:
        for li in food_list.find_all("li", class_="lunch-menu__list-item", recursive=True):
            # Extract text without the warning icon
            warning_icon = li.find("i", class_="icon-warning")
            food_id = None
            has_allergen_info = False
            if warning_icon:
                has_allergen_info = True
                food_id = warning_icon.get("data-food-id")
                warning_icon.decompose()  # Remove icon so get_text gives clean name
            name = clean_text(li.get_text(" ", strip=True))
            if name:
                items.append({
                    "name": name,
                    "food_id": food_id,
                    "has_allergen_info": has_allergen_info,
                })

        # Check for "Seçmeli 4. Çeşit" (optional 4th dish) — a <b> tag with sub-list
        selectable_header = food_list.find("b")
        if selectable_header:
            selectable_name = clean_text(selectable_header.get_text(" ", strip=True))
            selectable_items: list[str] = []
            selectable_ul = selectable_header.find_next("ul", class_="secmeli-yemek")
            if selectable_ul:
                for sli in selectable_ul.find_all("li", class_="lunch-menu__list-item"):
                    sname = clean_text(sli.get_text(" ", strip=True))
                    if sname:
                        selectable_items.append(sname)
            if selectable_items:
                items.append({
                    "name": selectable_name,
                    "options": selectable_items,
                    "is_selectable_group": True,
                })

    return {
        "url": page_url,
        "title": title,
        "meal_type": title or meal_type,
        "date": date,
        "vegetarian_available": vegetarian_available,
        "vegetarian_selected": vegetarian_selected,
        "items": items,
        "item_count": len(items),
    }


def make_snapshot_payload(page_data: dict[str, Any], label: str | None = None) -> dict[str, Any]:
    return {
        "label": label,
        "url": page_data["url"],
        "title": page_data["title"],
        "headings": page_data["headings"],
        "links": page_data["links"],
        "attachments": page_data["attachments"],
        "tables": page_data["tables"],
        "text": page_data["text"],
        "text_hash": page_data["text_hash"],
    }


def compare_snapshot_payloads(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    def as_link_set(items: list[dict[str, Any]]) -> set[tuple[str, str]]:
        return {(item["url"], item["text"]) for item in items}

    previous_links = as_link_set(previous.get("links", []))
    current_links = as_link_set(current.get("links", []))
    previous_files = as_link_set(previous.get("attachments", []))
    current_files = as_link_set(current.get("attachments", []))

    diff_lines = list(
        difflib.unified_diff(
            previous.get("text", "").splitlines(),
            current.get("text", "").splitlines(),
            fromfile="previous",
            tofile="current",
            lineterm="",
        )
    )

    return {
        "url": current.get("url"),
        "title_changed": previous.get("title") != current.get("title"),
        "previous_title": previous.get("title"),
        "current_title": current.get("title"),
        "links_added": [
            {"url": url, "text": text}
            for url, text in sorted(current_links - previous_links)
        ][:100],
        "links_removed": [
            {"url": url, "text": text}
            for url, text in sorted(previous_links - current_links)
        ][:100],
        "attachments_added": [
            {"url": url, "text": text}
            for url, text in sorted(current_files - previous_files)
        ][:100],
        "attachments_removed": [
            {"url": url, "text": text}
            for url, text in sorted(previous_files - current_files)
        ][:100],
        "text_changed": previous.get("text_hash") != current.get("text_hash"),
        "text_diff_preview": diff_lines[:60],
    }


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


@dataclass(slots=True)
class SnapshotReference:
    path: Path
    payload: dict[str, Any]
