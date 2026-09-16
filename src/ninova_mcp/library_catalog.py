"""Public Sirsi Portfolio HTML, without executing its account or holdings scripts."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode, urlparse

from .parsing import clean_text, make_soup, normalize_lookup_text

CATALOG_URL = "https://katalog.kutuphane.itu.edu.tr/client/tr_TR/default/"
RECORD_PATTERN = r"SD_ILS:[0-9]{1,12}"
SEARCH_FIELDS = {
    "keyword": None,
    "title": ("TITLE", "Başlık"),
    "author": ("AUTHOR", "Yazar"),
    "subject": ("SUBJECT", "Konu"),
    "call_number": ("CALLNUMBER", "Yer Numarası"),
    "isbn": ("ISBN", "ISBN"),
}


def record_url(record_id: str) -> str:
    if not re.fullmatch(RECORD_PATTERN, record_id):
        raise ValueError("Invalid Sirsi record ID.")
    return CATALOG_URL + "search/detailnonmodal?" + urlencode({"d": f"ent://SD_ILS/0/{record_id}~ILS~0"})


def _values(node: Any, field: str) -> list[str]:
    return list(dict.fromkeys(
        text for value in node.select(f".displayElementText.{field}")
        if (text := clean_text(value.get_text(" ", strip=True)))
    ))


def _first(node: Any, field: str) -> str | None:
    return next(iter(_values(node, field)), None)


def _identity(node: Any) -> str | None:
    ids = set()
    for value in node.select(".DOC_ID_value, input.results_chkbox"):
        raw = value.get("value", "") if value.name == "input" else clean_text(value.get_text())
        match = re.fullmatch(r"(?:ent://SD_ILS/0/)?(" + RECORD_PATTERN + r")", raw)
        if match:
            ids.add(match.group(1))
    return ids.pop() if len(ids) == 1 else None


def parse_record(html: str, page_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    title = _first(soup, "INITIAL_TITLE_SRCH")
    record_id = _identity(soup)
    if not title or not record_id:
        raise ValueError("Sirsi catalog record identity/title was not found; the response is not a verified item.")
    fields = {
        "title": title,
        "author": _values(soup, "PERSONAL_AUTHOR"),
        "isbn": _values(soup, "ISBN"),
        "edition": _first(soup, "EDITION"),
        "publication": _first(soup, "PUBLICATION_INFO"),
        "physical_description": _first(soup, "PHYSICAL_DESC"),
        "subjects": _values(soup, "SUBJECT_TERM"),
    }
    copies = []
    tables = soup.select("table.detailItemTable")
    for table in tables:
        for row in table.select("tr.detailItemsTableRow"):
            copy: dict[str, Any] = {}
            for field, key in (("LOCATION", "location"), ("ITYPE", "material_type"),
                               ("BARCODE", "barcode"), ("CALLNUMBER", "call_number")):
                cell = row.select_one(f"td.detailItemsTable_{field}")
                copy[key] = clean_text(cell.get_text(" ", strip=True)) if cell else None
            status = row.select_one("td.detailItemsTable_SD_ITEM_STATUS")
            pending = bool(status and status.select_one(".asyncInProgressSD_ITEM_STATUS"))
            if status:
                for hidden in status.select(".hidden"):
                    hidden.decompose()
            copy["status"] = clean_text(status.get_text(" ", strip=True)) if status and not pending else None
            copy["status_pending"] = pending
            if any(copy.get(key) for key in ("location", "barcode", "call_number")):
                copies.append(copy)
    links = list(dict.fromkeys(
        anchor["href"] for anchor in soup.select(".ELECTRONIC_ACCESS_value a[href], .displayElementText.ELECTRONIC_ACCESS a[href]")
        if urlparse(anchor["href"]).scheme == "https"
    ))
    result = {
        "record_id": record_id, "title": title, "fields": fields,
        "copies": copies, "copy_count": len(copies), "copy_list_observed": bool(tables),
        "electronic_access_urls": links, "url": record_url(record_id),
        "availability_source": "initial_public_html",
    }
    if any(copy["status_pending"] for copy in copies):
        result["availability_warning"] = (
            "The catalog loads copy status asynchronously. This adapter reads the public HTML only; "
            "pending statuses are unknown. Check the official catalog for current shelf availability."
        )
    if not tables:
        result["parse_warning"] = "No public copy table was present; this does not prove that the item has no copies."
    return result


def parse_search(html: str, page_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    # Sirsi redirects a one-result query directly to the item page.
    if "/search/detailnonmodal" in urlparse(page_url).path:
        item = parse_record(html, page_url)
        record = {key: item[key] for key in ("record_id", "title", "url")}
        record.update({"author": item["fields"]["author"], "isbn": item["fields"]["isbn"]})
        return {"url": page_url, "records": [record], "count": 1, "total_count": 1, "first_result": 1}
    records = []
    seen = set()
    cells = soup.select(".results_cell")
    for cell in cells:
        record_id = _identity(cell)
        title_node = cell.select_one(".INITIAL_TITLE_SRCH .displayDetailLink")
        title = clean_text(title_node.get_text(" ", strip=True)) if title_node else None
        if not record_id or not title:
            raise ValueError("Sirsi catalog search contains an unrecognized result; a complete result list cannot be reported.")
        if record_id in seen:
            continue
        seen.add(record_id)
        records.append({
            "record_id": record_id, "title": title, "url": record_url(record_id),
            "author": _values(cell, "INITIAL_AUTHOR_SRCH"), "isbn": _values(cell, "ISBN"),
            "call_number": _first(cell, "PREFERRED_CALLNUMBER"),
        })
    total_node = soup.select_one(".resultsToolbar_num_results")
    total_match = re.match(r"\s*([\d.,\s]+)", total_node.get_text()) if total_node else None
    total = int(re.sub(r"\D", "", total_match.group(1))) if total_match else None
    first = soup.select_one(".results_cell .hitNumber")
    first_match = re.search(r"\d+", first.get_text()) if first else None
    if not records:
        empty_node = soup.select_one("#searchResultText")
        empty = normalize_lookup_text(empty_node.get_text(" ", strip=True)) if empty_node else ""
        if empty not in {"no results found", "sonuc bulunamadi", "kayit bulunamadi"}:
            raise ValueError("Sirsi catalog search results were not recognized; this is not an empty search result.")
        total = 0
    return {"url": page_url, "records": records, "count": len(records), "total_count": total,
            "first_result": int(first_match.group()) if first_match else None}
