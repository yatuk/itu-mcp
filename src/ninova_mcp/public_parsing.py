"""Parsers for public İTÜ pages outside the authenticated Ninova flow."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from bs4 import Tag

from .parsing import clean_text, make_soup, normalize_lookup_text


def _cell_texts(row: Tag) -> list[str]:
    return [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"], recursive=False)]


def _table_matrix(table: Tag) -> list[list[str]]:
    return [cells for row in table.find_all("tr") if (cells := _cell_texts(row))]


def _header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", normalize_lookup_text(value)).strip("_")


def extract_final_exam_schedule(html: str, page_url: str) -> dict[str, Any]:
    """Parse the OBS public final-exam partial returned by its AJAX route."""
    soup = make_soup(html)
    page_text = normalize_lookup_text(soup.get_text(" ", strip=True))
    unpublished = any(
        marker in page_text
        for marker in ("yayinlanmamistir", "not published", "program bulunamadi")
    )
    exams: list[dict[str, Any]] = []
    raw_headers: list[str] = []

    aliases = {
        "crn": "crn",
        "ders_kodu": "course_code",
        "course_code": "course_code",
        "ders_adi": "course_name",
        "course_name": "course_name",
        "sinav_tarihi": "date",
        "final_tarihi": "date",
        "tarih": "date",
        "date": "date",
        "sinav_saati": "time",
        "saat": "time",
        "time": "time",
        "bina": "building",
        "building": "building",
        "derslik": "room",
        "salon": "room",
        "room": "room",
        "ogretim_uyesi": "instructor",
        "instructor": "instructor",
        "gozetmen": "proctor",
    }

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_row = next((row for row in rows if row.find("th")), rows[0])
        headers = _cell_texts(header_row)
        if len(headers) < 2:
            continue
        raw_headers = raw_headers or headers
        normalized_headers = [aliases.get(_header_key(header), _header_key(header)) for header in headers]
        for row in rows[rows.index(header_row) + 1 :]:
            cells = row.find_all("td", recursive=False)
            if not cells:
                continue
            values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
            if not any(values):
                continue
            item: dict[str, Any] = {}
            for index, value in enumerate(values):
                key = normalized_headers[index] if index < len(normalized_headers) else f"column_{index + 1}"
                item[key] = value or None
            for index, cell in enumerate(cells):
                anchor = cell.find("a", href=True)
                if anchor and index < len(normalized_headers):
                    item[f"{normalized_headers[index]}_url"] = urljoin(page_url, anchor["href"])
            exams.append(item)

    result: dict[str, Any] = {
        "url": page_url,
        "published": bool(exams) and not unpublished,
        "count": len(exams),
        "headers": raw_headers,
        "exams": exams,
    }
    if not exams:
        result["message"] = (
            "Bu bölüm için final sınav programı henüz yayımlanmamış."
            if unpublished
            else "Final takvimi tablosu bulunamadı; sayfa yapısı değişmiş olabilir."
        )
        if not unpublished:
            result["parse_warning"] = result["message"]
    return result


def extract_directory_results(html: str, page_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    table = soup.select_one("table.search-result-table")
    if table is None:
        text = normalize_lookup_text(soup.get_text(" ", strip=True))
        no_results = "sonuc bulunamadi" in text or "kayit bulunamadi" in text
        return {
            "url": page_url,
            "count": 0,
            "people": [],
            **({} if no_results else {"parse_warning": "Rehber sonuç tablosu bulunamadı."}),
        }

    header_row = (table.find("thead") or table).find("tr")
    headers = _cell_texts(header_row) if header_row else []
    keys = [
        {
            "unvan": "title",
            "ad_soyad": "full_name",
            "birim": "unit",
            "bolum": "department",
        }.get(_header_key(header), _header_key(header))
        for header in headers
    ]
    people: list[dict[str, Any]] = []
    for row in table.select("tbody tr"):
        values = _cell_texts(row)
        if not values:
            continue
        item = {keys[index] if index < len(keys) else f"column_{index + 1}": value or None for index, value in enumerate(values)}
        detail_path = row.get("data-link")
        if detail_path:
            item["detail_url"] = urljoin(page_url, str(detail_path))
        people.append(item)
    return {"url": page_url, "count": len(people), "people": people}


def extract_directory_detail(html: str, page_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    detail: dict[str, Any] = {"url": page_url}
    for row in soup.select("table tr"):
        cells = _cell_texts(row)
        if len(cells) >= 2:
            detail[_header_key(cells[0])] = cells[1]
    for term in soup.find_all("dt"):
        value = term.find_next_sibling("dd")
        if value:
            detail[_header_key(clean_text(term.get_text(" ", strip=True)))] = clean_text(value.get_text(" ", strip=True))
    heading = soup.find(["h1", "h2"])
    if heading:
        detail.setdefault("full_name", clean_text(heading.get_text(" ", strip=True)))
    if len(detail) == 1:
        detail["parse_warning"] = "Rehber detay alanları bulunamadı."
    return detail


def extract_building_codes(html: str, page_url: str, query: str | None = None) -> dict[str, Any]:
    soup = make_soup(html)
    target = normalize_lookup_text(query) if query else ""
    buildings: list[dict[str, str]] = []
    for row in soup.select("table tr"):
        cells = _cell_texts(row)
        if len(cells) < 2:
            continue
        code, name = cells[0], cells[1]
        if target and target not in normalize_lookup_text(f"{code} {name}"):
            continue
        buildings.append({"code": code, "name": name})
    result: dict[str, Any] = {"url": page_url, "query": query, "count": len(buildings), "locations": buildings}
    if not buildings and not query:
        result["parse_warning"] = "Bina kodları tablosu bulunamadı."
    return result


def extract_sports_facility_hours(html: str, page_url: str, facility: str | None = None) -> dict[str, Any]:
    soup = make_soup(html)
    target = normalize_lookup_text(facility) if facility else ""
    facilities: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        rows = _table_matrix(table)
        for values in rows:
            if len(values) < 5 or not re.search(r"\d{1,2}:\d{2}|kapal", normalize_lookup_text(" ".join(values[1:]))):
                continue
            if target and target not in normalize_lookup_text(values[0]):
                continue
            facilities.append({
                "facility": values[0],
                "weekday": {"opens": values[1], "closes": values[2]},
                "weekend": {"opens": values[3], "closes": values[4]},
            })
    result: dict[str, Any] = {"url": page_url, "query": facility, "count": len(facilities), "facilities": facilities}
    if not facilities:
        result["parse_warning"] = "Spor tesisi saatleri tablosu bulunamadı veya filtre eşleşmedi."
    return result


def extract_shuttle_schedule(
    html: str,
    page_url: str,
    *,
    route: str | None = None,
    day_type: str | None = None,
) -> dict[str, Any]:
    """Return every official shuttle table as a structured matrix.

    The SKS page is editor-managed and contains several differently shaped
    schedules. Preserving table rows avoids inventing route semantics when the
    page changes, while still making filtering and LLM consumption practical.
    """
    soup = make_soup(html)
    route_key = normalize_lookup_text(route) if route else ""
    day_key = normalize_lookup_text(day_type) if day_type else ""
    schedules: list[dict[str, Any]] = []
    stop_lists: list[dict[str, Any]] = []
    for index, table in enumerate(soup.find_all("table"), start=1):
        rows = _table_matrix(table)
        if not rows:
            continue
        title = " / ".join(rows[0])
        blob = normalize_lookup_text(" ".join(" ".join(row) for row in rows[:4]))
        if route_key and route_key not in blob:
            continue
        if day_key and day_key not in blob:
            continue
        item = {"table_index": index, "title": title, "rows": rows[1:]}
        if "durak" in blob:
            stop_lists.append(item)
        elif "servis" in blob or "ring" in blob or "saat" in blob:
            schedules.append(item)
    result: dict[str, Any] = {
        "url": page_url,
        "route_filter": route,
        "day_type_filter": day_type,
        "schedule_count": len(schedules),
        "schedules": schedules,
        "stop_list_count": len(stop_lists),
        "stop_lists": stop_lists,
    }
    if not schedules and not stop_lists:
        result["parse_warning"] = "Mekik çizelgesi tablosu bulunamadı veya filtre eşleşmedi."
    return result


def extract_degree_plan_list(html: str, page_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    plans: list[dict[str, Any]] = []
    for row in soup.select("table.datalist tbody tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        anchor = cells[0].find("a", href=True)
        if not anchor:
            continue
        href = urljoin(page_url, anchor["href"])
        match = re.search(r"/DersPlanDetay/(\d+)", href)
        plans.append({
            "plan_id": int(match.group(1)) if match else None,
            "title": clean_text(cells[1].get_text(" ", strip=True)),
            "url": href,
        })
    result: dict[str, Any] = {"url": page_url, "count": len(plans), "plans": plans}
    if not plans:
        result["parse_warning"] = "Ders planı listesi bulunamadı."
    return result


def _number(value: str) -> float | None:
    try:
        return float(value.replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def extract_degree_plan_detail(html: str, page_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    semesters: list[dict[str, Any]] = []
    all_courses: list[dict[str, Any]] = []
    for table in soup.select("table.datalist"):
        heading = table.find("h2")
        semester_name = clean_text(heading.get_text(" ", strip=True)) if heading else f"{len(semesters) + 1}. Yarıyıl"
        courses: list[dict[str, Any]] = []
        for row in table.select("tbody tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 10:
                continue
            values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
            anchor = cells[0].find("a", href=True)
            code = clean_text(anchor.get_text(" ", strip=True)) if anchor and "DersBilgi" in anchor.get("href", "") else None
            group_anchor = cells[0].find("a", href=re.compile(r"_DersGrupSearch"))
            group_match = re.search(r"grupId=(\d+)", group_anchor.get("href", "")) if group_anchor else None
            item = {
                "code": code,
                "name": values[1],
                "language": values[2] or None,
                "requirement": values[3] or None,
                "credit": _number(values[4]),
                "ects": values[5] or None,
                "theory_hours": _number(values[6]),
                "practice_hours": _number(values[7]),
                "lab_hours": _number(values[8]),
                "category": values[9] or None,
                "elective_group_id": int(group_match.group(1)) if group_match else None,
            }
            courses.append(item)
            all_courses.append({**item, "semester": semester_name})
        if courses:
            semesters.append({"semester": semester_name, "course_count": len(courses), "courses": courses})
    result: dict[str, Any] = {
        "url": page_url,
        "semester_count": len(semesters),
        "course_count": len(all_courses),
        "semesters": semesters,
        "courses": all_courses,
    }
    if not semesters:
        result["parse_warning"] = "Ders planı detay tabloları bulunamadı."
    return result


def extract_announcement_list(html: str, page_url: str, source: str) -> list[dict[str, Any]]:
    """Best-effort parser for Sitefinity/legacy İTÜ announcement lists."""
    soup = make_soup(html)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates = soup.select(
        ".sfnewsListItem, .news-list-item, .news-item, article, .content-area.news .row > div"
    )
    date_re = re.compile(r"\b\d{1,2}\s+[A-Za-zÇĞİÖŞÜçğıöşü]+\s+\d{4}\b")
    for node in candidates:
        anchor = node.find("a", href=True)
        if not anchor:
            continue
        title_node = node.find(["h1", "h2", "h3", "h4", "h5", "h6"]) or anchor
        title = clean_text(title_node.get_text(" ", strip=True))
        if len(title) < 6:
            continue
        url = urljoin(page_url, anchor["href"])
        if url in seen:
            continue
        text = clean_text(node.get_text(" ", strip=True))
        date_match = date_re.search(text)
        items.append({
            "source": source,
            "title": title,
            "published_at": date_match.group(0) if date_match else None,
            "summary": text[:500] if text != title else None,
            "url": url,
        })
        seen.add(url)
    return items


def extract_library_search_results(html: str, page_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=re.compile(r"(?:/record=|/record/|record=b)")):
        url = urljoin(page_url, anchor["href"])
        if url in seen:
            continue
        container = anchor.find_parent(["tr", "div", "li"]) or anchor
        title = clean_text(anchor.get_text(" ", strip=True))
        if not title:
            title_node = container.select_one(".briefcitTitle, .browseEntryData") if isinstance(container, Tag) else None
            title = clean_text(title_node.get_text(" ", strip=True)) if title_node else ""
        if not title:
            continue
        text = clean_text(container.get_text(" ", strip=True)) if isinstance(container, Tag) else title
        match = re.search(r"record[=/](b\d+)", url)
        records.append({"record_id": match.group(1) if match else None, "title": title, "summary": text[:500], "url": url})
        seen.add(url)
    result: dict[str, Any] = {"url": page_url, "count": len(records), "records": records}
    if not records:
        text = normalize_lookup_text(soup.get_text(" ", strip=True))
        if "no entries found" not in text and "kayit bulunamadi" not in text:
            result["parse_warning"] = "Katalog sonuç kayıtları bulunamadı; WebPAC yapısı değişmiş olabilir."
    return result


def extract_library_record(html: str, page_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    fields: dict[str, Any] = {}
    for row in soup.select(".bibInfoEntry, table.bibDetail tr, .bibDisplayContent tr"):
        cells = _cell_texts(row)
        if len(cells) >= 2:
            key = _header_key(cells[0])
            value = " ".join(cells[1:])
            if key in fields:
                existing = fields[key]
                fields[key] = existing + [value] if isinstance(existing, list) else [existing, value]
            else:
                fields[key] = value
    copies: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        headers = [_header_key(value) for value in (_cell_texts(table.find("tr")) if table.find("tr") else [])]
        if not any(key in headers for key in ("location", "bulun_yer", "status", "statusu")):
            continue
        for row in table.find_all("tr")[1:]:
            values = _cell_texts(row)
            if values:
                copies.append({headers[i] if i < len(headers) else f"column_{i + 1}": value for i, value in enumerate(values)})
    title = fields.get("title") or fields.get("baslik")
    result: dict[str, Any] = {"url": page_url, "title": title, "fields": fields, "copy_count": len(copies), "copies": copies}
    if not fields and not copies:
        result["parse_warning"] = "Katalog kayıt detayları bulunamadı."
    return result


def extract_library_account(html: str, page_url: str) -> dict[str, Any]:
    """Parse a Millennium WebPAC patron page without exposing PIN fields."""
    soup = make_soup(html)
    loans: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [_header_key(value) for value in _cell_texts(rows[0])]
        table_blob = normalize_lookup_text(" ".join(headers) + " " + clean_text(table.get_text(" ", strip=True))[:500])
        if not any(marker in table_blob for marker in ("due", "iade", "checked out", "odunc")):
            continue
        for row in rows[1:]:
            cells = row.find_all("td", recursive=False)
            if not cells:
                continue
            values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
            item = {headers[i] if i < len(headers) and headers[i] else f"column_{i + 1}": value for i, value in enumerate(values)}
            checkbox = row.find("input", attrs={"type": "checkbox"})
            if checkbox:
                item["loan_id"] = checkbox.get("value") or checkbox.get("name")
                item["renewable"] = not checkbox.has_attr("disabled")
            else:
                item["renewable"] = False
            anchor = row.find("a", href=True)
            if anchor:
                item["item_url"] = urljoin(page_url, anchor["href"])
                item.setdefault("title", clean_text(anchor.get_text(" ", strip=True)))
            loans.append(item)
    name_node = soup.select_one(".patronName, .patName, h1, h2")
    result: dict[str, Any] = {
        "url": page_url,
        "account_name": clean_text(name_node.get_text(" ", strip=True)) if name_node else None,
        "loan_count": len(loans),
        "loans": loans,
    }
    if not loans:
        text = normalize_lookup_text(soup.get_text(" ", strip=True))
        if "no items" not in text and "odunc aldiginiz yayin bulunmamaktadir" not in text:
            result["parse_warning"] = "Ödünç kayıt tablosu bulunamadı veya hesapta açık ödünç yok."
    return result
