"""Parsers for public İTÜ pages outside the authenticated Ninova flow."""

from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import Tag

from .parsing import clean_text, make_soup, normalize_lookup_text


def _cell_texts(row: Tag) -> list[str]:
    return [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"], recursive=False)]


def _cell_text_br_joined(cell: Tag) -> str:
    """Join a cell's text, treating <br/> as an explicit " / " separator.

    Without this, multi-value cells (e.g. two exam locations separated by
    <br/>) collapse into an ambiguous space-joined string that reads as one
    run-on value.
    """
    inner = cell.decode_contents() if hasattr(cell, "decode_contents") else str(cell)
    parts_html = re.split(r"<br\s*/?\s*>", inner, flags=re.IGNORECASE)
    parts = [clean_text(re.sub(r"<[^>]+>", "", part)) for part in parts_html]
    return " / ".join(part for part in parts if part)


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
            values = [_cell_text_br_joined(cell) for cell in cells]
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
            # The directory's own "no result" answer, as opposed to a page
            # this parser could not read.
            **({"no_results": True} if no_results else {"parse_warning": "Rehber sonuç tablosu bulunamadı."}),
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
    detail: dict[str, Any] = {"url": page_url, "coverage": "public"}
    contacts = []
    aliases = {"birim": "unit", "bolum": "department", "is_telefonu": "phone",
               "telefon": "phone", "e_posta_adresi": "email", "e_posta": "email",
               "ofis": "office", "oda": "office"}
    profile = soup.select_one(".white-box") or soup
    for table in profile.find_all("table"):
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        if not rows:
            continue
        headers = _cell_texts(rows[0])
        keys = [aliases.get(_header_key(header)) for header in headers]
        if len(keys) >= 3 and sum(key is not None for key in keys) >= 3:
            for row in rows[1:]:
                values = _cell_texts(row)
                if not values:
                    continue
                contact = {key: value or None for key, value in zip(keys, values) if key}
                if not any(contact.values()):
                    continue
                contact["primary_fields"] = [key for key, header in zip(keys, headers) if key and "*" in header]
                contacts.append(contact)
            continue
        # Retain supported two-column/definition-list layouts without treating
        # an unrelated multi-column table as a key/value record.
        for row in rows:
            cells = _cell_texts(row)
            if len(cells) == 2 and _header_key(cells[0]) in aliases:
                detail[aliases[_header_key(cells[0])]] = cells[1] or None
    if contacts:
        detail["contacts"] = contacts
        for key in ("unit", "department", "phone", "email", "office"):
            primary = next((item.get(key) for item in contacts if key in item["primary_fields"] and item.get(key)), None)
            detail[key] = primary or next((item.get(key) for item in contacts if item.get(key)), None)
    for term in soup.find_all("dt"):
        value = term.find_next_sibling("dd")
        if value:
            detail[_header_key(clean_text(term.get_text(" ", strip=True)))] = clean_text(value.get_text(" ", strip=True))
    def person_heading(node: Tag) -> str | None:
        text = clean_text(node.get_text(" ", strip=True))
        normalized = normalize_lookup_text(text)
        generic = (
            "rehber", "istanbul teknik universitesi", "giris", "login", "sign in",
            "oturum", "iletisim bilgileri", "contact information", "kisi bilgileri",
            "person details", "profile details", "profil bilgileri", "hata", "error",
            "access denied", "bulunamadi", "not found", "no results", "duyurular",
        )
        if any(marker in normalized for marker in generic):
            return None
        if len(text) > 160 or any(char.isdigit() for char in text):
            return None
        if sum(any(char.isalpha() for char in word) for word in text.split()) < 2:
            return None
        return text

    # A page heading alone is not evidence of a person's name. Prefer the
    # profile card; the global fallback also requires recognizable contacts.
    candidates = [name for node in profile.find_all(["h2", "h1"])
                  if (name := person_heading(node)) is not None]
    has_contacts = bool(contacts) or any(detail.get(key) for key in aliases.values())
    if len(set(candidates)) == 1 and (profile is not soup or has_contacts):
        detail["full_name"] = candidates[0]
    if "giris yapmalisiniz" in normalize_lookup_text(soup.get_text(" ", strip=True)):
        detail["additional_details_require_login"] = True
    if not contacts and not any(key in detail for key in (*aliases.values(), "full_name")):
        detail["parse_warning"] = "Rehber detay alanları bulunamadı."
    return detail


def _pseudo_location_kind(code: str, name: str) -> str | None:
    """Rows of the building list that are not places: online teaching, "to be announced"."""
    blob = normalize_lookup_text(f"{code} {name}")
    if "online" in blob or "cevrimici" in blob:
        return "online"
    if "undeclared" in blob or "aciklanacak" in blob:
        return "announced_later"
    return None


def extract_building_codes(html: str, page_url: str, query: str | None = None) -> dict[str, Any]:
    """Parse the OBS building code list into buildings, sub-locations and pseudo entries.

    The page is one two-column table of code and name. A code that is listed
    once is a building. A code that is repeated (``DZB`` appears twelve times
    with "C", "B", "LAB", "AMFİ" ...) is one building whose rows name its
    blocks and rooms; the page gives no name for such a building. Rows such
    as ``undeclared`` and ``Online/Çevrimiçi`` are not places at all.

    ``locations`` holds the buildings, ``sub_locations`` the in-building
    codes, ``pseudo_entries`` the rest.
    """
    soup = make_soup(html)
    target = normalize_lookup_text(query) if query else ""
    rows: list[tuple[str, str]] = []
    for row in soup.select("table tr"):
        if row.find("th"):
            continue
        cells = _cell_texts(row)
        if len(cells) < 2 or not cells[0]:
            continue
        rows.append((cells[0], cells[1]))
    occurrences: dict[str, int] = {}
    for code, _ in rows:
        occurrences[code] = occurrences.get(code, 0) + 1

    buildings: dict[str, dict[str, Any]] = {}
    sub_locations: list[dict[str, str]] = []
    pseudo_entries: list[dict[str, str]] = []
    for code, name in rows:
        kind = _pseudo_location_kind(code, name)
        if kind:
            pseudo_entries.append({"code": code, "name": name, "kind": kind})
        elif occurrences[code] > 1:
            building = buildings.setdefault(code, {
                "code": code,
                "name": None,
                "name_missing_in_source": True,
                "sub_location_codes": [],
            })
            if name and name not in building["sub_location_codes"]:
                building["sub_location_codes"].append(name)
                sub_locations.append({"building_code": code, "code": name})
        else:
            buildings[code] = {"code": code, "name": name}

    parsed_any = bool(rows)
    locations = list(buildings.values())
    if target:
        def matches(*values: str) -> bool:
            return target in normalize_lookup_text(" ".join(values))

        matched_codes = {
            item["code"] for item in locations
            if matches(item["code"], item.get("name") or "")
        }
        sub_locations = [
            item for item in sub_locations
            if item["building_code"] in matched_codes or matches(item["code"])
        ]
        locations = [item for item in locations if item["code"] in matched_codes]
        pseudo_entries = [item for item in pseudo_entries if matches(item["code"], item["name"])]
    result: dict[str, Any] = {
        "url": page_url,
        "query": query,
        "count": len(locations),
        "locations": locations,
        "sub_location_count": len(sub_locations),
        "sub_locations": sub_locations,
        "pseudo_entries": pseudo_entries,
    }
    if not parsed_any:
        result["parse_warning"] = "Bina kodları tablosu bulunamadı."
    elif target and not (locations or sub_locations or pseudo_entries):
        result["no_match_note"] = (
            "Eşleşme yok. Bu kaynak yalnızca ders programında geçen bina kodlarını listeler "
            "(derslik binaları ve spor alanları); kütüphane, yemekhane, yurt gibi yerler ve "
            "adresler burada yer almaz. Sorguyu bina kodu ya da bina adının bir parçasıyla deneyin."
        )
    return result


_SPORTS_TIME_RE = re.compile(r"(\d{1,2})\s*[:.]\s*(\d{2})")
_WEEKDAY_NAMES = {
    "pazartesi": "Pazartesi", "sali": "Salı", "carsamba": "Çarşamba", "persembe": "Perşembe",
    "cuma": "Cuma", "cumartesi": "Cumartesi", "pazar": "Pazar",
}
_CLOSURE_NOTE_RE = re.compile(r"\s*\(([^()]*kapal[^()]*)\)\s*", re.IGNORECASE)


def _sports_clock(value: str) -> str | None:
    """``09.00`` and ``9:00`` both become ``09:00``; anything else is None."""
    match = _SPORTS_TIME_RE.fullmatch(value.strip())
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _sports_period(opens: str, closes: str) -> dict[str, Any]:
    """One opening period; "Kapalı" in a cell becomes ``closed`` instead of a time."""
    closed = any("kapal" in normalize_lookup_text(value) for value in (opens, closes))
    if closed:
        return {"opens": None, "closes": None, "closed": True}
    period: dict[str, Any] = {"opens": _sports_clock(opens), "closes": _sports_clock(closes), "closed": False}
    for key, raw in (("opens", opens), ("closes", closes)):
        if period[key] is None and raw.strip():
            # An unexpected cell is passed on as text, never as a time.
            period[f"{key}_text"] = raw.strip()
    return period


def _sports_facility_name(raw: str) -> tuple[str, str | None, list[str]]:
    """Split "Havuz (Pazartesi Kapalı)" into the name, the note and the closed days."""
    match = _CLOSURE_NOTE_RE.search(raw)
    if match is None:
        return raw, None, []
    note = clean_text(match.group(1))
    name = clean_text(raw[: match.start()] + " " + raw[match.end():])
    days = [
        display for folded, display in _WEEKDAY_NAMES.items()
        if re.search(rf"\b{folded}\b", normalize_lookup_text(note))
    ]
    return name, note, days


def extract_sports_facility_hours(html: str, page_url: str, facility: str | None = None) -> dict[str, Any]:
    soup = make_soup(html)
    target = normalize_lookup_text(facility) if facility else ""
    facilities: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        rows = _table_matrix(table)
        for values in rows:
            if len(values) < 5 or not re.search(r"\d{1,2}[:.]\d{2}|kapal", normalize_lookup_text(" ".join(values[1:]))):
                continue
            if target and target not in normalize_lookup_text(values[0]):
                continue
            name, note, closed_days = _sports_facility_name(values[0])
            item: dict[str, Any] = {
                "facility": name,
                "weekday": _sports_period(values[1], values[2]),
                "weekend": _sports_period(values[3], values[4]),
                "closed_days": closed_days,
            }
            if note:
                item["note"] = note
            facilities.append(item)
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
    # The page opens with a sentence saying from which date the tables apply.
    for node in soup.find_all(["strong", "p"]):
        text = clean_text(node.get_text(" ", strip=True))
        if "tarihinden itibaren" in text and len(text) < 400:
            result["validity_note"] = text
            break
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


def _ects_fields(raw: str) -> dict[str, Any]:
    """Parse the ECTS cell: ``"5"`` and ``"4,5"`` are numbers, ``"4 / 5"`` is a range.

    A range keeps its text in ``ects_raw`` with ``ects_min``/``ects_max`` and
    leaves ``ects`` empty, because no single number is right for it.
    """
    value = raw.strip()
    number = _number(value) if value else None
    if number is not None or not value:
        return {"ects": number}
    parts = [_number(part) for part in re.split(r"\s*[/\u2013-]\s*", value) if part.strip()]
    if len(parts) >= 2 and all(part is not None for part in parts):
        return {"ects": None, "ects_min": min(parts), "ects_max": max(parts), "ects_raw": value}
    return {"ects": None, "ects_raw": value}


def extract_degree_plan_detail(
    html: str,
    page_url: str,
    include_flat_courses: bool = False,
) -> dict[str, Any]:
    """Parse a semester-by-semester OBS degree plan.

    Courses are listed once, under ``semesters``. ``include_flat_courses``
    adds the same rows again as one flat ``courses`` list, each with its
    ``semester``; it is off by default because it doubles the payload.
    """
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
                **_ects_fields(values[5]),
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
    }
    if include_flat_courses:
        result["courses"] = all_courses
    if not semesters:
        result["parse_warning"] = "Ders planı detay tabloları bulunamadı."
    return result


_MONTH_NUMBERS = {
    "oca": 1, "ocak": 1, "sub": 2, "subat": 2, "mar": 3, "mart": 3, "nis": 4, "nisan": 4,
    "may": 5, "mayis": 5, "haz": 6, "haziran": 6, "tem": 7, "temmuz": 7, "agu": 8, "agustos": 8,
    "eyl": 9, "eylul": 9, "eki": 10, "ekim": 10, "kas": 11, "kasim": 11, "ara": 12, "aralik": 12,
    "jan": 1, "january": 1, "feb": 2, "february": 2, "march": 3, "apr": 4, "april": 4,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_TEXT_DATE_RE = re.compile(r"(\d{1,2})\s+([^\W\d_]+)\.?\s+(\d{4})")
_NUMERIC_DATE_RE = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})")


def parse_announcement_date(value: Any) -> str | None:
    """Turn a publication date into ISO form so that sources can be sorted together.

    Reads ISO timestamps (kept with their time), ``06 Eki 2026``,
    ``6 Ekim 2026`` and ``06.10.2026``. Returns None for anything else.
    """
    raw = clean_text(str(value)) if value is not None else ""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    year = month = day = None
    match = _TEXT_DATE_RE.search(raw)
    if match:
        month = _MONTH_NUMBERS.get(normalize_lookup_text(match.group(2)))
        day, year = int(match.group(1)), int(match.group(3))
    else:
        match = _NUMERIC_DATE_RE.search(raw)
        if match:
            day, month, year = (int(part) for part in match.groups())
    if not (year and month and day):
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def truncate_text(value: str, limit: int) -> tuple[str, bool]:
    """Cut ``value`` to ``limit`` characters at a word boundary, marking the cut with "…"."""
    if len(value) <= limit:
        return value, False
    cut = value[:limit]
    if not value[limit].isspace() and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-") + "…", True


def extract_announcement_list(html: str, page_url: str, source: str) -> list[dict[str, Any]]:
    """Best-effort parser for Sitefinity/legacy İTÜ announcement lists.

    ``.thumbnail`` is the card of the main site (www.itu.edu.tr/duyurular):
    an ``h6`` title, a ``p`` summary, a ``.date`` and a "Devamı" link.
    """
    soup = make_soup(html)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates = soup.select(
        ".thumbnail, .sfnewsListItem, .news-list-item, .news-item, article, .content-area.news .row > div"
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
        date_node = node.select_one(".date, time")
        date_text = clean_text(date_node.get_text(" ", strip=True)) if date_node else ""
        date_match = date_re.search(date_text) or date_re.search(text)
        published = date_match.group(0) if date_match else (date_text or None)
        paragraphs = [
            clean_text(p.get_text(" ", strip=True)) for p in node.find_all("p")
            if not p.find_parent(class_="hidden")
        ]
        body = " ".join(part for part in paragraphs if part)
        if not body:
            # No paragraph: use the card text without its title, date and link label.
            body = text
            for piece in (title, published or "", clean_text(anchor.get_text(" ", strip=True))):
                if piece:
                    body = body.replace(piece, " ", 1)
            body = clean_text(body)
        summary, truncated = truncate_text(body, 500)
        item: dict[str, Any] = {
            "source": source,
            "title": title,
            "published_at": published,
            "published_at_iso": parse_announcement_date(published),
            "summary": summary if summary and summary != title else None,
            "url": url,
        }
        if truncated:
            item["summary_truncated"] = True
        items.append(item)
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
