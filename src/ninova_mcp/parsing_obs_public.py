"""Public OBS pages: course search, prerequisites, the course schedule table and the academic calendar."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from .parsing_common import COURSE_CODE_RE, clean_text, make_soup, normalize_lookup_text
from .parsing_page import parse_html_page


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
