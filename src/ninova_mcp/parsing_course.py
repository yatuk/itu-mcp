"""Ninova course pages: announcements, files, course info, sections, grades, message board, remote learning."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .parsing_common import (
    DATE_RE,
    _looks_like_date,
    clean_text,
    make_soup,
    ninova_datetime_iso,
    normalize_lookup_text,
    split_trailing_ninova_datetime,
)
from .parsing_page import _find_table_by_headers, _table_after_heading, parse_html_page


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


def _announcement_cards(soup: BeautifulSoup) -> list[Tag]:
    cards = soup.select("div.duyuruGoruntule")
    if cards:
        return cards
    # Fallback: any block that looks like an announcement card with h2>a to Duyuru.
    return [
        anchor.find_parent("div")
        for anchor in soup.select("h2 a[href*='Duyuru'], h2 a[href*='duyuru']")
        if anchor.find_parent("div") is not None
    ]


def _announcement_class(content: Tag | None, page_url: str) -> tuple[str | None, str | None, str | None]:
    """``(course_title, class_name, class_url)`` named at the top of a card's content."""
    if not content:
        return None, None, None
    strong = content.find("strong")
    course_title = clean_text(strong.get_text(" ", strip=True)) if strong else None
    class_anchor = content.find("a", href=True)
    if class_anchor and "/Sinif/" in class_anchor["href"] and "/Duyuru/" not in class_anchor["href"]:
        return course_title, clean_text(class_anchor.get_text(" ", strip=True)), urljoin(page_url, class_anchor["href"])
    return course_title, None, None


def _span_texts(card: Tag, selector: str) -> list[str]:
    texts = (clean_text(span.get_text(" ", strip=True)) for span in card.select(selector))
    return [text for text in texts if text]


def _announcement_author(card: Tag) -> str | None:
    """The last text of the card's footer, or the one before it when the last is the date."""
    trailing_spans = _span_texts(card, "div.tarih span.tarih")
    if not trailing_spans:
        return None
    if _looks_like_date(trailing_spans[-1]) and len(trailing_spans) > 1:
        return trailing_spans[-2]
    return trailing_spans[-1]


def _announcement_item(card: Tag, title_anchor: Tag, page_url: str) -> dict[str, Any]:
    content = card.select_one("div.icerik")
    content_text = clean_text(content.get_text(" ", strip=True) if content else "")
    course_title, class_name, class_url = _announcement_class(content, page_url)
    published_at = next((item for item in _span_texts(card, "span.tarih") if _looks_like_date(item)), None)

    summary = content_text
    summary = _remove_once(summary, course_title)
    summary = _remove_once(summary, class_name)
    summary = _remove_once(summary, published_at)
    summary = clean_text(summary.strip("/ "))

    return {
        "title": clean_text(title_anchor.get_text(" ", strip=True)),
        "url": urljoin(page_url, title_anchor["href"]),
        "course_title": course_title,
        "class_name": class_name,
        "class_url": class_url,
        "published_at": published_at,
        "author": _announcement_author(card),
        "summary": summary,
    }


def extract_announcements_list(html: str, page_url: str, base_url: str) -> list[dict[str, Any]]:
    soup = make_soup(html)
    items: list[dict[str, Any]] = []
    for card in _announcement_cards(soup):
        if card is None:
            continue
        title_anchor = card.select_one("h2 a[href]")
        if not title_anchor:
            continue
        items.append(_announcement_item(card, title_anchor, page_url))
    return items


_HELP_BOX_RE = re.compile(r"\bYardım\s+Diğer sınıflarınıza\b")


def _read_announcement_card(card: Tag, content: Tag) -> tuple[str | None, str | None, str]:
    """``(title, published_at, body_text)`` from the announcement's own card.

    The first date-like ``span.tarih`` is the publication date; when it sits
    inside the content it is taken out, so the date is not repeated in the body.
    """
    heading = card.find(["h1", "h2", "h3"])
    title = clean_text(heading.get_text(" ", strip=True)) if heading is not None else None
    published_at: str | None = None
    for span in card.select("span.tarih"):
        candidate = clean_text(span.get_text(" ", strip=True))
        if _looks_like_date(candidate):
            published_at = candidate
            if content in span.parents:
                span.decompose()
            break
    return title or None, published_at, clean_text(content.get_text(" ", strip=True))


def _split_flat_announcement(page_text: str, title: str) -> tuple[str | None, str]:
    """``(published_at, body_text)`` from the page text when there is no card.

    The body is what follows the title and the first date, up to the page's
    help box (or, failing that, the last "Yardım" of the footer).
    """
    body_text = page_text
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
    return published_at, body_text


def extract_announcement_detail(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    page = parse_html_page(page_url, html, base_url=base_url)
    title = page["headings"][0]["text"] if page["headings"] else page["title"]

    # The announcement has its own card; reading it keeps the page's help box
    # and footer, which also say "Yardım", out of the body.
    soup = make_soup(html)
    card = soup.select_one("div.duyuruGoruntule")
    content = card.select_one("div.icerik") if card else None
    if card is not None and content is not None:
        card_title, published_at, body_text = _read_announcement_card(card, content)
        title = card_title or title
    else:
        published_at, body_text = _split_flat_announcement(page["text"], title)
    return {
        "title": title,
        "url": page["url"],
        "published_at": published_at,
        "published_at_iso": ninova_datetime_iso(published_at),
        "body_text": clean_text(body_text),
    }


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


def _course_identity(rows: list[Any]) -> dict[str, Any]:
    """The identity table: label/value rows, with the course name given once per language."""
    identity: dict[str, Any] = {}
    for row in rows:
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
    return identity


def _weekly_schedule(rows: list[Any]) -> list[dict[str, str]]:
    return [{"time": row[0], "location": row[1]} for row in rows if isinstance(row, list) and len(row) >= 2]


def extract_course_info(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    page = parse_html_page(page_url, html, base_url=base_url)
    # The page is read by position: identity, class, weekly schedule, details, weekly plan.
    tables = [table["rows"] for table in page["tables"] if not _is_noise_table(table)]

    return {
        "url": page["url"],
        "title": page["title"],
        "headings": page["headings"],
        "identity": _course_identity(tables[0]) if len(tables) >= 1 else {},
        "class_meta": _pairs_from_rows(tables[1]) if len(tables) >= 2 else {},
        "weekly_schedule": _weekly_schedule(tables[2]) if len(tables) >= 3 else [],
        "course_details": _pairs_from_rows(tables[3]) if len(tables) >= 4 else {},
        "weekly_plan": tables[4] if len(tables) >= 5 else [],
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
