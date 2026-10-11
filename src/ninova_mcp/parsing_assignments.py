"""Ninova assignments: the list, the detail page, the upload form and its file slots."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import Tag

from .parsing_common import clean_text, make_soup, normalize_lookup_text
from .parsing_page import _find_table_by_headers, parse_html_page


def _extract_labeled_value(text: str, label: str, next_labels: list[str]) -> str | None:
    normalized = clean_text(text)
    pattern = re.escape(label) + r"\s*(.*?)\s*(?=" + "|".join(re.escape(item) for item in next_labels) + r"|$)"
    match = re.search(pattern, normalized, flags=re.DOTALL)
    if not match:
        return None
    value = clean_text(match.group(1))
    return value or None


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


def _slot_is_uploaded(normalized_status: str, has_file_link: bool) -> bool:
    """Whether a slot already holds a file: the status sentence decides, then the file link."""
    if "henuz gondermediniz" in normalized_status:
        return False
    if any(token in normalized_status for token in ("gonderdiniz", "yuklediniz", "teslim ettiniz")):
        return True
    return has_file_link


def _slot_file_input(cells: list[Tag]) -> Tag | None:
    """The slot's ``<input type="file">``, from the first cell that has one."""
    for cell in cells:
        file_input = cell.find("input", attrs={"type": "file"})
        if file_input is not None:
            return file_input
    return None


def _allowed_extensions(extensions: str | None) -> list[str]:
    """``"*.pdf; .zip"`` as ``[".pdf", ".zip"]``; pieces without a leading dot are dropped."""
    allowed_extensions: list[str] = []
    if extensions:
        for piece in re.split(r"[;,\s]+", extensions):
            piece = piece.strip().lower()
            if piece.startswith("*."):
                allowed_extensions.append(piece[1:])  # ".pdf"
            elif piece.startswith(".") and len(piece) > 1:
                allowed_extensions.append(piece)
    return allowed_extensions


def _parse_upload_row(cells: list[Tag], page_url: str, *, index: int) -> dict[str, Any]:
    status_text = clean_text(cells[0].get_text(" ", strip=True))
    anchors = [
        anchor
        for anchor in cells[0].find_all("a", href=True)
        if not clean_text(anchor.get("href", "")).startswith("javascript:")
    ]
    file_anchor = next((anchor for anchor in anchors if clean_text(anchor.get_text(" ", strip=True))), None)

    strong = cells[0].find("strong")
    description = clean_text(strong.get_text(" ", strip=True)) if strong else None

    file_input = _slot_file_input(cells)
    # The raw text of the extensions cell is kept, whatever else the cell holds.
    extensions = clean_text(cells[1].get_text(" ", strip=True)) if len(cells) > 1 else None

    return {
        "index": index,
        "description": description or status_text,
        "extensions": extensions,
        "allowed_extensions": _allowed_extensions(extensions),
        "status_text": status_text,
        "uploaded": _slot_is_uploaded(normalize_lookup_text(status_text), file_anchor is not None),
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


_SUBMIT_LABELS = {"odevi gonder", "gonder", "submit", "upload"}


def _postback_submit_control(form: Tag) -> tuple[str, str] | None:
    """``(event target, label)`` of the LinkButton that submits the form.

    The control is usually ``javascript:__doPostBack('...$lbEkle','')``. Failing
    that and a link labelled as a submit, the first content postback that is
    not the header's language switch is taken.
    """
    fallback: tuple[str, str] | None = None
    for anchor in form.find_all("a", href=True):
        href = anchor.get("href") or ""
        text = clean_text(anchor.get_text(" ", strip=True))
        match = re.search(r"__doPostBack\(\s*'([^']+)'\s*,\s*'([^']*)'\s*\)", href)
        if not match:
            continue
        target = match.group(1)
        if "lbEkle" in target or normalize_lookup_text(text) in _SUBMIT_LABELS:
            return target, text or target
        if fallback is None and "ContentPlaceHolder1" in target and "Header" not in target:
            fallback = (target, text or target)
    return fallback


def _button_submit_control(form: Tag) -> tuple[str | None, str | None]:
    """``(name, label)`` of the first real submit button; some themes render one."""
    for input_tag in form.select("input[type='submit'][name], button[name]"):
        return input_tag.get("name"), input_tag.get("value") or clean_text(input_tag.get_text(" ", strip=True))
    return None, None


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
    hidden_fields: dict[str, str] = {}
    for input_tag in form.select("input[type='hidden'][name]"):
        name = input_tag.get("name")
        if name:
            hidden_fields[name] = input_tag.get("value") or ""

    submit_event_target, submit_label = _postback_submit_control(form) or _button_submit_control(form)

    status = extract_assignment_upload_status(html, page_url, base_url=base_url)
    slots = status.get("upload_items") or []

    page = parse_html_page(page_url, html, base_url=base_url)
    title = page["headings"][0]["text"] if page["headings"] else page["title"]

    return {
        "ok": True,
        "url": page_url,
        "form_url": urljoin(page_url, action),
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


def _match_slot_by_description(slots: list[dict[str, Any]], slot_description: str) -> dict[str, Any]:
    """The one slot whose description equals the text, or else the one that contains it."""
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
        return _match_slot_by_description(slots, slot_description)

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
