from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag


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
COURSE_CODE_RE = re.compile(r"\b[A-Z]{2,}\s*\d{3}[A-Z]?\b")
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
    if not url_or_path:
        raise ValueError("url_or_path is required")
    parsed = urlparse(url_or_path)
    if parsed.scheme and parsed.netloc:
        return url_or_path
    if url_or_path.startswith("/"):
        return f"{base_url.rstrip('/')}{url_or_path}"
    return urljoin(f"{base_url.rstrip('/')}/", url_or_path)


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
        href = clean_text(anchor.get("href"))
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
    headers = [clean_text(cell.get_text(" ", strip=True)) for cell in table.select("th")]
    rows: list[list[str]] = []
    for row in table.find_all("tr"):
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["td", "th"])]
        if cells:
            rows.append(cells)

    structured_rows: list[dict[str, Any] | list[str]] = []
    if headers and len(headers) == len(rows[0]):
        body_rows = rows[1:]
        for row in body_rows:
            structured_rows.append(dict(zip(headers, row, strict=False)))
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


def extract_courses(html: str, page_url: str, base_url: str) -> list[dict[str, Any]]:
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
        code_match = COURSE_CODE_RE.search(context)
        courses.append(
            {
                "code": code_match.group(0) if code_match else None,
                "title": _extract_course_title(anchor),
                "url": absolute,
                "context": context,
            }
        )
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


def summarize_dashboard(page_data: dict[str, Any], html: str, base_url: str) -> dict[str, Any]:
    return {
        "page": {
            "url": page_data["url"],
            "title": page_data["title"],
            "headings": page_data["headings"],
        },
        "courses": extract_courses(html, page_data["url"], base_url=base_url),
        "recent_announcements": extract_named_table(html, "Son Duyurular"),
        "recent_assignments": extract_named_table(html, "Son Odevler"),
        "recent_messages": extract_named_table(html, "Son Mesajlar"),
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


def extract_announcement_detail(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    page = parse_html_page(page_url, html, base_url=base_url)
    title = page["headings"][0]["text"] if page["headings"] else page["title"]
    body_text = page["text"]
    if title in body_text:
        body_text = body_text.split(title, 1)[1]
    published_at_match = DATE_RE.search(body_text)
    published_at = published_at_match.group(0) if published_at_match else None
    if published_at:
        body_text = body_text.split(published_at, 1)[1]
    if "Yardım" in body_text:
        body_text = body_text.rsplit("Yardım", 1)[0]
    return {
        "title": title,
        "url": page["url"],
        "published_at": published_at,
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
    description = _extract_labeled_value(text, "Ödev Açıklaması", ["Kaynak Dosyalar", "İstenen Dosyalar", "Yardım"])

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

    headers: list[str] = []
    weeks: list[dict[str, Any]] = []
    total_present = 0
    total_absent = 0
    if attendance_table is not None:
        headers = [clean_text(cell.get_text(" ", strip=True)) for cell in attendance_table.find_all("th")]
        for row in attendance_table.find_all("tr")[1:]:
            cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            if not cells:
                continue
            sessions = []
            for column_name, value in zip(headers[1:], cells[1:], strict=False):
                marks = re.findall(r"[01]", value)
                total_present += marks.count("1")
                total_absent += marks.count("0")
                sessions.append(
                    {
                        "name": column_name,
                        "value": value,
                        "present_count": marks.count("1"),
                        "absent_count": marks.count("0"),
                    }
                )
            weeks.append(
                {
                    "week": cells[0],
                    "sessions": sessions,
                }
            )

    return {
        "url": page["url"],
        "student_name": student_name,
        "headers": headers,
        "count": len(weeks),
        "weeks": weeks,
        "total_present_marks": total_present,
        "total_absent_marks": total_absent,
    }


def _table_after_heading(soup: BeautifulSoup, heading_text: str) -> Tag | None:
    matcher = normalize_lookup_text(heading_text)
    for tag in soup.find_all(True):
        if normalize_lookup_text(tag.get_text(" ", strip=True)) != matcher:
            continue
        return tag.find_next("table")
    return None


def _extract_remote_session_rows(table: Tag, page_url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    headers = [clean_text(cell.get_text(" ", strip=True)) for cell in table.find_all("th")]
    if not headers:
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) != 1:
                continue
            text = clean_text(cells[0].get_text(" ", strip=True))
            if text and "herhangi bir uzaktan eğitim oturumu bulunmamaktadır" not in normalize_lookup_text(text):
                rows.append({"text": text})
        return rows

    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        item: dict[str, Any] = {}
        for header, cell in zip(headers, cells, strict=False):
            item[header] = clean_text(cell.get_text(" ", strip=True)) or None
            anchor = cell.find("a", href=True)
            if anchor is not None:
                item[f"{header}_url"] = urljoin(page_url, anchor["href"])
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
    """Parse OBS ``/public/DersBilgi/Search`` results into course dicts.

    Tries table-based extraction first (headers like "Ders Kodu", "Ders Adı"),
    then falls back to link-list scanning.
    """
    soup = make_soup(html)
    results: list[dict[str, Any]] = []

    # Strategy 1: look for a table with "Ders Kodu" header
    target_headers = {"ders kodu", "ders adı", "ders adi", "course code", "course name"}
    for table in soup.find_all("table"):
        th_texts = {normalize_lookup_text(th.get_text(" ", strip=True)) for th in table.find_all("th")}
        if not th_texts & target_headers:
            continue
        headers = [clean_text(th.get_text(" ", strip=True)) for th in table.find_all("th")]
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
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
            if code or name:
                item["code"] = code
                item["name"] = name
            results.append(item)
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
    soup = make_soup(html)
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
