"""Generic page parsing: headings, links, tables, table lookup and page snapshots."""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .parsing_common import (
    clean_text,
    link_kind,
    make_soup,
    normalize_lookup_text,
    split_date_range,
)


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


def _table_after_heading(soup: BeautifulSoup, heading_text: str) -> Tag | None:
    matcher = normalize_lookup_text(heading_text)
    for tag in soup.find_all(True):
        if normalize_lookup_text(tag.get_text(" ", strip=True)) != matcher:
            continue
        return tag.find_next("table")
    return None


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


@dataclass(slots=True)
class SnapshotReference:
    path: Path
    payload: dict[str, Any]
