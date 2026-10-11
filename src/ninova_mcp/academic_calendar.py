"""Parse dated events and the stated coverage of official ITU calendars."""

from __future__ import annotations

import calendar as month_calendar
import html as html_entities
import re
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from bs4 import NavigableString, Tag

from .parsing import clean_text, make_soup, normalize_lookup_text

TIMEZONE = "Europe/Istanbul"
_MONTHS = {
    "january": 1, "ocak": 1, "february": 2, "subat": 2,
    "march": 3, "mart": 3, "april": 4, "nisan": 4,
    "may": 5, "mayis": 5, "june": 6, "haziran": 6,
    "july": 7, "temmuz": 7, "august": 8, "agustos": 8,
    "september": 9, "eylul": 9, "october": 10, "ekim": 10,
    "november": 11, "kasim": 11, "december": 12, "aralik": 12,
}
_DATE = re.compile(
    r"(?<![\d:])(?P<day>\d{1,2})\s+(?P<month>" + "|".join(_MONTHS) + r")\b"
    r"(?:\s+(?P<year>\d{4}))?"
    r"(?:\s*(?:-\s*|saat\s*:?\s*)?(?P<time>\d{1,2}:\d{2}))?"
)
_TIME_END = re.compile(r"\s*[-–]\s*(\d{1,2}:\d{2})(?!\d)")


def parse_event_dates(value: str, *, default_year: int | None = None) -> dict[str, Any] | None:
    """Read explicit Turkish/English dates, sharing omitted range components.

    An omitted left year belongs to the end year, except across New Year.
    Times are returned only when present in the source. All-day ranges retain
    inclusive dates without inventing midnight opening or closing times.
    """
    text = normalize_lookup_text(value).replace("–", "-").replace("—", "-")
    matches = list(_DATE.finditer(text))
    if not matches or len(matches) > 2:
        return None
    first, last = matches[0], matches[-1]
    start_day, start_month = int(first["day"]), _MONTHS[first["month"]]
    end_day, end_month = int(last["day"]), _MONTHS[last["month"]]
    start_year = int(first["year"]) if first["year"] else None
    end_year = int(last["year"]) if last["year"] else None
    start_time, end_time = first["time"], last["time"] if len(matches) == 2 else None
    consumed = last.end()
    if len(matches) == 1:
        # "07 - 11 September 2026" and "17 Eylül 2026 Saat 10:00-13:00".
        short_start = re.search(r"(?<!\d)(\d{1,2})\s*-\s*$", text[: first.start()])
        if short_start:
            start_day = int(short_start[1])
            start_time, end_time = None, first["time"]
        time_end = _TIME_END.match(text, first.end())
        if time_end and start_time:
            end_time = time_end[1]
            consumed = time_end.end()
    # Do not silently accept a valid start followed by a malformed endpoint.
    if re.search(r"\d", text[consumed:]):
        return None
    if start_year is None and end_year is not None:
        start_year = end_year - int((start_month, start_day) > (end_month, end_day))
    if end_year is None and start_year is not None:
        end_year = start_year + int((end_month, end_day) < (start_month, start_day))
    if start_year is None:
        start_year = default_year
    if end_year is None and start_year is not None:
        end_year = start_year + int((end_month, end_day) < (start_month, start_day))
    if start_year is None or end_year is None:
        return None
    try:
        start, end = date(start_year, start_month, start_day), date(end_year, end_month, end_day)
        if end < start:
            return None
        def timestamp(day: date, clock: str | None) -> str | None:
            if clock is None:
                return None
            hour, minute = map(int, clock.split(":"))
            return datetime.combine(day, time(hour, minute), ZoneInfo(TIMEZONE)).isoformat()
        start_at, end_at = timestamp(start, start_time), timestamp(end, end_time)
        if start_at and end_at and end_at < start_at:
            return None
    except ValueError:
        return None
    return {
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "start_at": start_at, "end_at": end_at, "timezone": TIMEZONE,
    }


def _scope(value: str) -> str:
    text = normalize_lookup_text(value)
    if any(term in text for term in ("internal transfer", "yatay gecis", "pre - registration", "on kayit")):
        return "transfer" if "transfer" in text or "gecis" in text else "pre_registration"
    if "preparatory" in text or "hazirlik" in text:
        return "preparatory"
    if "undergraduate" in text or ("lisans" in text and "lisansustu" not in text):
        return "undergraduate"
    if "graduate" in text or "lisansustu" in text:
        return "graduate"
    return "general"


_REGISTRATION_SOURCE_CATEGORIES = {"course registrations", "ders kayitlari"}
_EXAM_SOURCE_CATEGORIES = {"exams", "sinavlar", "last grade entry date", "son not girisi"}
_TERM_BOUNDARY = re.compile(
    r"(?:beginning|start|end) of\b.*\b(?:term|semester|school|classes|courses)\b"
    r"|.*\b(?:yariyili|donemi|ogretimi|derslerinin) (?:baslangici|sonu|baslamasi|tamamlanmasi)$"
)
_SECTION_TERMS = {
    "fall term": "fall", "guz donemi": "fall", "spring term": "spring", "bahar donemi": "spring",
    "summer school": "summer", "yaz okulu": "summer",
}


def _metadata(description: str, context: str, *, source_section: str = "", source_category: str = "") -> dict[str, Any]:
    text = normalize_lookup_text(description)
    whole = normalize_lookup_text(f"{context} {description}")
    source_section, source_category = normalize_lookup_text(source_section), normalize_lookup_text(source_category)
    classes = sorted({int(n) for n in re.findall(r"\b([1-4])(?:st|nd|rd|th|\.)?\s*(?:class|sinif)\b", text)})
    # Year-of-study synonyms are used only alongside registration wording.
    registration = source_category in _REGISTRATION_SOURCE_CATEGORIES or any(word in text for word in (
        "registration", "kayit", "kaydi", "kaydinin", "course selection", "add/drop", "add drop", "add-drop",
        "bir baska derse yazilma", "ekle-birak", "ekle birak", "withdrawal", "cekilme"))
    if registration and not classes:
        classes = [n for label, n in (("freshman", 1), ("sophomore", 2), ("junior", 3), ("senior", 4)) if re.search(rf"\b{label}\b", text)]
    if registration:
        category = "registration"
    elif source_category in _EXAM_SOURCE_CATEGORIES or any(word in text for word in ("exam", "sinav", "final", "midterm", "butunleme", "make-up", "mazeret")):
        category = "exam"
    elif any(word in text for word in ("holiday", "tatil", "bayram")) or re.search(r"\bbreak\b", text):
        category = "holiday"
    elif _TERM_BOUNDARY.match(text) or (not source_category and any(word in text for word in ("term", "semester", "donem", "classes", "yariyil"))):
        # On the year list almost every row names its term; only the rows that
        # open or close a term are semester events there.
        category = "semester"
    else:
        category = "other"
    scope = _scope(context) if context else _scope(description)
    result: dict[str, Any] = {"category": category, "scope": scope, "class_levels": classes}
    if registration:
        if any(word in text for word in ("draft", "taslak")):
            kind = "draft_preparation"
        elif classes:
            kind = "class_window"
        elif any(word in text for word in ("reopening", "yeniden acil")):
            kind = "all_students_reopening"
        elif any(word in text for word in ("first time", "ilk defa", "new conservatory")):
            kind = "first_registration"
        elif any(word in text for word in ("add/drop", "add drop", "add-drop", "bir baska derse yazilma", "ekle-birak", "ekle birak")):
            kind = "add_drop"
        elif any(word in text for word in ("withdrawal", "cekilme")):
            kind = "withdrawal"
        elif any(word in text for word in ("exam registration", "sinav kay")):
            kind = "exam_registration"
        elif "cezali" in text or "late registration" in text:
            kind = "late_registration"
        else:
            kind = "other"
        result["registration_kind"] = kind
    term = _SECTION_TERMS.get(source_section)
    for label, pattern in (("fall", r"\b(fall|guz)\b"), ("spring", r"\b(spring|bahar)\b"), ("summer", r"\b(summer|yaz)\b")):
        if term is None and re.search(pattern, whole):
            term = label
            break
    result["academic_term"] = term
    groups = []
    if any(word in text for word in ("double degree", "double major")) or re.search(r"\bcap\b", text):
        groups.append("double_major")
    if re.search(r"\b(ddp|uolp)\b", text):
        groups.append("dual_diploma")
    if groups:
        result["additional_student_groups"] = groups
    return result


def _selected(soup: Tag, name: str) -> str | None:
    node = soup.select_one(f'select[name="{name}"] option[selected]')
    return str(node.get("value") or "") if node else None


def _split_inline(text: str) -> tuple[str, str] | None:
    # A separator followed by words cannot be the colon within HH:MM.
    parts = re.split(r"\s*:\s+(?=[^\W\d_])", text, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    normalized = normalize_lookup_text(text)
    matches = list(_DATE.finditer(normalized))
    if matches:
        # Normalization changes Turkish code points, not string length for these dates.
        stop = matches[-1].end()
        end_clock = _TIME_END.match(normalized, stop)
        if end_clock:
            stop = end_clock.end()
        if normalized[stop:].strip(" :"):
            # Work with the normalized date prefix but preserve the official description.
            return normalized[:stop], text[stop:].strip(" :")
    return None


_NOTE = "Coverage describes the fetched pages only. Class registration windows are public schedules, not confirmation of personal eligibility."
_STATUS_UNKNOWN = "not_explicitly_identified_by_source"


def _available_academic_years(soup: Tag) -> list[dict[str, str]]:
    years = []
    for option in soup.select('select[name="akademikyil"] option'):
        value = str(option.get("value") or "")
        match = re.search(r"\b(\d{4})\s*[-–]\s*(\d{4})\b", option.get_text(" ", strip=True))
        if value.isdigit() and match:
            years.append({"id": value, "academic_year": f"{match[1]}-{match[2]}"})
    return years


def _parse_year_list(soup: Tag, page_url: str, academic_year: str | None) -> tuple[list[dict[str, Any]], list[tuple[str, str]]] | None:
    """Read the full academic-year list (``div#results``), one row per event.

    Each row is: date text, a bold description, then a line
    "calendar name / term or section / site category" and a link carrying the
    site's own event id (UID). Returns ``None`` when the page has no such
    list, so the caller can fall back to the older layouts.
    """
    container = soup.select_one("div#results")
    if container is None:
        return None
    events: dict[str, dict[str, Any]] = {}
    unparsed: list[tuple[str, str]] = []
    for row in container.find_all("div", recursive=False):
        try:
            bold = row.find("b")
            date_parts = []
            for node in row.children:
                if isinstance(node, Tag) and node.name in {"br", "b"}:
                    break
                date_parts.append(str(node) if isinstance(node, NavigableString) else node.get_text(" ", strip=True))
            # "(12 days left)" changes daily and is not part of the date.
            raw_date = clean_text(html_entities.unescape(" ".join(date_parts)).split("(", 1)[0]).rstrip(" :")
            description = clean_text(html_entities.unescape(bold.get_text(" ", strip=True))) if bold else ""
            label_node = next((f for f in row.find_all("font") if f.get("size") == "2" and not f.find("a")), None)
            label = clean_text(html_entities.unescape(label_node.get_text(" ", strip=True))) if label_node else ""
            link = row.find("a", href=re.compile(r"UID=\d+"))
            uid_match = re.search(r"UID=(\d+)", str(link.get("href"))) if link else None
            parts = [part.strip() for part in label.split(" / ")]
            calendar_name = " / ".join(parts[:-2]) if len(parts) >= 3 else label
            section, site_category = (parts[-2], parts[-1]) if len(parts) >= 3 else ("", "")
            parsed = parse_event_dates(raw_date) if raw_date and description else None
        except (AttributeError, TypeError, ValueError):
            unparsed.append(("", ""))
            continue
        if parsed is None:
            unparsed.append((raw_date, calendar_name))
            continue
        uid = uid_match[1] if uid_match else None
        item = {"date": raw_date, "description": description, **parsed,
                **_metadata(description, calendar_name, source_section=section, source_category=site_category),
                "source_calendar": calendar_name or None, "source_section": section or None,
                "source_category": site_category or None, "source_url": page_url,
                "academic_year": academic_year, "uid": uid}
        key = uid or "|".join(str(item[k]) for k in ("start_date", "end_date", "start_at", "end_at")) + normalize_lookup_text(description + calendar_name)
        events.setdefault(key, item)
    return list(events.values()), unparsed


def _sorted_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(events, key=lambda e: (e["start_date"], e["start_at"] or "", e["description"], e.get("source_calendar") or ""))


def _year_list_result(parsed: tuple[list[dict[str, Any]], list[tuple[str, str]]], page_url: str,
                      academic_year: str | None, available: list[dict[str, str]]) -> dict[str, Any]:
    events, unparsed = parsed
    ordered = _sorted_events(events)
    complete = bool(ordered) and not unparsed
    result: dict[str, Any] = {
        "url": page_url, "event_count": len(ordered), "events": ordered, "semesters": [], "current_semester": None,
        "current_semester_status": _STATUS_UNKNOWN, "source": urlparse(page_url).hostname, "timezone": TIMEZONE,
        "coverage": {"scope": "academic_year",
                     "start_date": min((e["start_date"] for e in ordered), default=None),
                     "end_date": max((e["end_date"] for e in ordered), default=None),
                     "complete": complete, "academic_years": [academic_year] if academic_year else []},
        "parse_complete": complete, "unparsed_event_count": len(unparsed), "truncated": False,
        "academic_year": academic_year, "available_academic_years": available, "languages": ["en"],
        "note": _NOTE,
    }
    if unparsed or not ordered:
        result["parse_warning"] = (f"Could not parse {len(unparsed)} dated event entries. Results may be incomplete." if unparsed
                                   else "No recognizable dated events were found. This does not establish an empty calendar.")
        result["unparsed_events"] = [{"date": d, "source_calendar": c} for d, c in sorted(set(unparsed))]
    return finalize_calendar(result)


_TERM_START = re.compile(r"beginning of (?:the )?\w+ (?:term|semester|school)|(?:guz|bahar) yariyili baslangici|yaz ogretimi baslangici")
_TERM_END = re.compile(r"end of (?:the )?\w+ (?:term|semester|school)|(?:guz|bahar) yariyili sonu|yaz ogretimi sonu")
_TERM_LABELS = {"fall": "Fall (Güz)", "spring": "Spring (Bahar)", "summer": "Summer School (Yaz Okulu)"}


def build_terms(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """First and last day of classes per academic year, term and level.

    Only a row whose whole description is "Beginning of Fall Term" / "End of
    Fall Term" (or the Turkish "Güz Yarıyılı Başlangıcı" / "Sonu") counts, and
    the term comes from the site's own term column, not from the wording (the
    site has misspelled "Spring"). Two rows that disagree leave that date
    empty. The summer school calendar is shared by both levels.
    """
    found: dict[tuple[str, str, str], dict[str, set[str]]] = {}
    calendars: dict[tuple[str, str, str], str] = {}
    for event in events:
        term = _SECTION_TERMS.get(normalize_lookup_text(event.get("source_section")))
        year = event.get("academic_year")
        calendar_name = normalize_lookup_text(event.get("source_calendar"))
        if not term or not year or "preparatory" in calendar_name or "hazirlik" in calendar_name:
            continue
        texts = [normalize_lookup_text(event.get(k)).strip(" .") for k in ("description", "description_tr") if event.get(k)]
        bound = "classes_start" if any(_TERM_START.fullmatch(t) for t in texts) else "classes_end" if any(_TERM_END.fullmatch(t) for t in texts) else None
        if bound is None:
            continue
        scopes = ["undergraduate", "graduate"] if term == "summer" else [event.get("scope")]
        for scope in scopes:
            if scope not in {"undergraduate", "graduate"}:
                continue
            key = (str(year), term, scope)
            found.setdefault(key, {"classes_start": set(), "classes_end": set()})[bound].add(
                event["start_date"] if bound == "classes_start" else event["end_date"])
            calendars.setdefault(key, event.get("source_calendar") or "")
    terms = []
    for (year, term, scope), bounds in sorted(found.items(), key=lambda item: (item[0][0], ("fall", "spring", "summer").index(item[0][1]), item[0][2])):
        start = next(iter(bounds["classes_start"])) if len(bounds["classes_start"]) == 1 else None
        end = next(iter(bounds["classes_end"])) if len(bounds["classes_end"]) == 1 else None
        if start and end and end < start:
            start = end = None
        terms.append({"academic_year": year, "term": term, "scope": scope, "classes_start": start,
                      "classes_end": end, "source_calendar": calendars[(year, term, scope)] or None})
    return terms


def finalize_calendar(calendar: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Derive ``terms``, ``semesters`` and ``current_semester`` from the events."""
    today = today or datetime.now(ZoneInfo(TIMEZONE)).date()
    terms = build_terms(calendar.get("events") or [])
    calendar["terms"] = terms
    calendar["semesters"] = [
        {"semester": _TERM_LABELS[t["term"]], "academic_year": t["academic_year"], "start": t["classes_start"], "end": t["classes_end"]}
        for t in terms if t["scope"] == "undergraduate"]
    current = next((t for t in terms if t["scope"] == "undergraduate" and t["classes_start"] and t["classes_end"]
                    and t["classes_start"] <= today.isoformat() <= t["classes_end"]), None)
    calendar["current_semester"] = dict(current) if current else None
    calendar["current_semester_status"] = "derived_from_term_start_and_end_dates" if current else _STATUS_UNKNOWN
    return calendar


def merge_calendar_languages(english: dict[str, Any], turkish: dict[str, Any]) -> dict[str, Any]:
    """Attach the Turkish description to each English row with the same site id.

    Rows are paired by UID and only when both pages give the same dates, so a
    Turkish text is never attached to a different event.
    """
    by_uid = {e["uid"]: e for e in turkish.get("events") or [] if e.get("uid")}
    events, unmatched = [], 0
    for event in english.get("events") or []:
        other = by_uid.get(event.get("uid"))
        if other and all(other.get(k) == event.get(k) for k in ("start_date", "end_date", "start_at", "end_at")):
            event = {**event, "description_tr": other["description"]}
        else:
            unmatched += 1
        events.append(event)
    merged = {**english, "events": events, "languages": ["en", "tr"]}
    if unmatched:
        merged["bilingual_unmatched_count"] = unmatched
    return finalize_calendar(merged)


def academic_years_for_range(start: date, end: date) -> list[str]:
    """Academic years whose list can hold events between ``start`` and ``end``.

    The list of year Y/Y+1 starts with applications in spring of Y and ends
    with summer school results in September of Y+1.
    """
    return [f"{year}-{year + 1}" for year in range(start.year - 2, end.year + 1)
            if date(year, 4, 1) <= end and start <= date(year + 1, 9, 30)]


def combine_calendars(calendars: list[dict[str, Any]], *, warnings: list[str] | None = None,
                      requested_years: list[str] | None = None, today: date | None = None) -> dict[str, Any]:
    """Join the lists of several academic years into one result."""
    warnings = list(warnings or [])
    events: dict[tuple[Any, ...], dict[str, Any]] = {}
    for calendar in calendars:
        for event in calendar.get("events") or []:
            key = (event.get("academic_year"), event.get("uid")) if event.get("uid") else (
                normalize_lookup_text(event["description"]), event["start_date"], event["end_date"], event.get("scope"), event.get("source_calendar"))
            events.setdefault(key, event)
    ordered = _sorted_events(list(events.values()))
    years = [c["academic_year"] for c in calendars if c.get("academic_year")]
    unparsed = sum(int(c.get("unparsed_event_count") or 0) for c in calendars)
    missing = [y for y in requested_years or [] if y not in years]
    complete = bool(calendars) and all(c.get("parse_complete") for c in calendars) and not missing
    languages = ["en", "tr"] if calendars and all(c.get("languages") == ["en", "tr"] for c in calendars) else ["en"]
    first = calendars[0] if calendars else {}
    result: dict[str, Any] = {
        "url": first.get("url"), "urls": [c.get("url") for c in calendars],
        "event_count": len(ordered), "events": ordered, "source": first.get("source"), "timezone": TIMEZONE,
        "coverage": {"scope": "academic_year",
                     "start_date": min((c["coverage"]["start_date"] for c in calendars if c["coverage"].get("start_date")), default=None),
                     "end_date": max((c["coverage"]["end_date"] for c in calendars if c["coverage"].get("end_date")), default=None),
                     "complete": complete, "academic_years": years},
        "parse_complete": bool(calendars) and all(c.get("parse_complete") for c in calendars),
        "unparsed_event_count": unparsed, "truncated": False, "languages": languages,
        "available_academic_years": first.get("available_academic_years") or [],
        "note": _NOTE,
    }
    unmatched = sum(int(c.get("bilingual_unmatched_count") or 0) for c in calendars)
    if unmatched:
        result["bilingual_unmatched_count"] = unmatched
    if languages == ["en"]:
        result["language_note"] = "Turkish descriptions are unavailable for at least one fetched year; Turkish queries match through a small built-in vocabulary only."
    if missing:
        result["missing_academic_years"] = missing
    if warnings:
        result["coverage_warning"] = " ".join(warnings)
    parse_warnings = [c["parse_warning"] for c in calendars if c.get("parse_warning")]
    if parse_warnings or not calendars:
        result["parse_warning"] = " ".join(parse_warnings) or "No calendar page could be read for the requested dates. This does not establish an empty calendar."
        result["unparsed_events"] = [u for c in calendars for u in c.get("unparsed_events") or []]
    return finalize_calendar(result, today=today)


def default_event_window(today: date) -> tuple[date, date]:
    """Window shown when the caller gives no filter at all: recent and upcoming."""
    return today - timedelta(days=14), today + timedelta(days=90)


def parse_academic_calendar(html: str, page_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    events: dict[tuple[Any, ...], dict[str, Any]] = {}
    unparsed: set[tuple[str, str]] = set()
    year_raw, month_raw = _selected(soup, "yil"), _selected(soup, "ay")
    default_year = int(year_raw) if year_raw and year_raw.isdigit() else None
    academic_year_node = soup.select_one('select[name="akademikyil"] option[selected]')
    academic_year_match = re.search(r"\b(\d{4})\s*[-–]\s*(\d{4})\b", academic_year_node.get_text(" ", strip=True)) if academic_year_node else None
    academic_year = f"{academic_year_match[1]}-{academic_year_match[2]}" if academic_year_match else None
    headings = {clean_text(b.get_text(" ", strip=True)) for p in soup.select("p.auto") for b in p.select("b") if b.find("span")}

    def add(raw_date: str, description: str, context: str = "") -> None:
        # lxml leaves HTML5-only entities such as &lpar; in the text.
        raw_date = clean_text(html_entities.unescape(raw_date)).rstrip(" :")
        description = clean_text(html_entities.unescape(description))
        context = clean_text(html_entities.unescape(context))
        if not description:
            unparsed.add((raw_date, context))
            return
        parsed = parse_event_dates(raw_date, default_year=default_year)
        if parsed is None:
            unparsed.add((raw_date, context))
            return
        source_calendar = next((h for h in sorted(headings, key=len, reverse=True) if context.startswith(h)), context)
        context_year = re.search(r"\b(\d{4})\s*[-–]\s*(\d{4})\b", context)
        event_year = f"{context_year[1]}-{context_year[2]}" if context_year else academic_year
        item = {"date": raw_date, "description": description, **parsed, **_metadata(description, context),
                "source_calendar": source_calendar or None, "source_url": page_url, "academic_year": event_year}
        # The grid writes apostrophes as an acute accent; compare letters and digits only.
        key = (*[item[k] for k in ("start_date", "end_date", "start_at", "end_at")],
               re.sub(r"[\W_]+", "", normalize_lookup_text(description)), normalize_lookup_text(source_calendar))
        if key in events:
            if item["academic_term"] and not events[key].get("academic_term"):
                # The upcoming list is the cleaner copy of a grid row: it names the term.
                events[key]["academic_term"] = item["academic_term"]
                events[key]["description"] = description
        else:
            events[key] = item

    year_list = _parse_year_list(soup, page_url, academic_year)
    if year_list is not None:
        # The same page also shows a month grid and an "upcoming" list. Both
        # repeat rows of this list with less detail, so they are not read.
        return _year_list_result(year_list, page_url, academic_year, _available_academic_years(soup))

    # The monthly grid repeats each event on every active day. Each bold date
    # is followed by its description, while colored bold spans name a calendar.
    for block in soup.select("p.auto"):
        context = ""
        for bold in block.find_all("b", recursive=False):
            text = clean_text(bold.get_text(" ", strip=True))
            if bold.find("span") and not re.match(r"\d", text):
                context = text
                continue
            if not text:
                continue
            inline = _split_inline(text)
            if inline and parse_event_dates(inline[0], default_year=default_year):
                add(*inline, context)
                continue
            description_parts = []
            for sibling in bold.next_siblings:
                if isinstance(sibling, Tag) and sibling.name in {"br", "b"}:
                    break
                description_parts.append(str(sibling) if isinstance(sibling, NavigableString) else sibling.get_text(" ", strip=True))
            add(text, " ".join(description_parts), context)

    # Upcoming-event lists provide explicit term labels that enrich grid rows.
    for li in soup.find_all("li"):
        first_text = clean_text(str(next((x for x in li.children if isinstance(x, NavigableString) and clean_text(str(x))), "")))
        if not re.match(r"\d", first_text):
            continue
        bold = li.find("b", recursive=False)
        if bold and parse_event_dates(first_text, default_year=default_year):
            context = clean_text(" ".join(str(x) if isinstance(x, NavigableString) else x.get_text(" ", strip=True) for x in bold.next_siblings))
            add(first_text, bold.get_text(" ", strip=True), context)
        else:
            inline = _split_inline(clean_text(li.get_text(" ", strip=True)))
            if inline:
                add(*inline)
            else:
                unparsed.add((first_text, ""))

    # The undergraduate course-registration page uses description/date columns.
    # Also retain support for compact legacy table cells with bold inline events.
    for table in soup.find_all("table"):
        title_node = table.select_one(".table-baslik")
        context = clean_text(title_node.get_text(" ", strip=True)) if title_node else ""
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) != 2 or any(c.find(["table", "p"]) for c in cells):
                continue
            values = [clean_text(c.get_text(" ", strip=True)) for c in cells]
            if context:
                add(values[1], values[0], context)
                continue
            for date_index in (1, 0):
                if len(values[date_index]) <= 300 and (_DATE.search(normalize_lookup_text(values[date_index])) or re.search(r"\d{1,2}\s+\w+\s+\d{4}", values[date_index])):
                    add(values[date_index], values[1 - date_index], context)
                    break
    for bold in soup.select("td b"):
        if bold.find_parent("p", class_="auto"):
            continue
        inline = _split_inline(clean_text(bold.get_text(" ", strip=True)))
        if inline and re.match(r"\d", inline[0]):
            add(*inline)

    ordered = sorted(events.values(), key=lambda e: (e["start_date"], e["start_at"] or "", e["description"], e["source_calendar"] or ""))
    complete = bool(ordered) and not unparsed
    coverage: dict[str, Any] = {"scope": "source_page", "start_date": None, "end_date": None, "complete": False}
    if default_year and month_raw and month_raw.isdigit() and 1 <= int(month_raw) <= 12:
        month = int(month_raw)
        coverage = {"scope": "displayed_month", "start_date": date(default_year, month, 1).isoformat(),
                    "end_date": date(default_year, month, month_calendar.monthrange(default_year, month)[1]).isoformat(), "complete": complete}
    semesters = []
    for event in ordered:
        description = normalize_lookup_text(event["description"])
        for term, label in (("fall term", "Fall (Güz)"), ("spring term", "Spring (Bahar)"), ("summer term", "Summer (Yaz)"), ("summer school", "Summer School (Yaz Okulu)")):
            if term in description and "beginning of" in description:
                semesters.append({"semester": label, "start": event["date"], "type": "start"})
            elif term in description and "end of" in description:
                semesters.append({"semester": label, "end": event["date"], "type": "end"})
    result: dict[str, Any] = {"url": page_url, "event_count": len(ordered), "events": ordered,
        "semesters": semesters, "current_semester": None,
        "current_semester_status": "not_explicitly_identified_by_source", "source": urlparse(page_url).hostname,
        "timezone": TIMEZONE, "coverage": coverage, "parse_complete": complete,
        "unparsed_event_count": len(unparsed), "truncated": False,
        "note": "Coverage describes the fetched page only. Class registration windows are public schedules, not confirmation of personal eligibility."}
    if unparsed or not ordered:
        result["parse_warning"] = (f"Could not parse {len(unparsed)} dated event entries. Results may be incomplete." if unparsed else "No recognizable dated events were found. This does not establish an empty calendar.")
        result["unparsed_events"] = [{"date": d, "source_calendar": c} for d, c in sorted(unparsed)]
    return result


_QUERY_FILLER = {"ne", "zaman", "when", "is", "are", "the", "tarih", "tarihi", "tarihleri", "date", "dates"}


def calendar_query_matches(event: dict[str, Any], query: str) -> bool:
    """Match words and a small explicit Turkish/English academic vocabulary."""
    def tokens(value: str) -> list[str]:
        text = normalize_lookup_text(value)
        replacements = (
            (r"\b(?:kayit\w*|kaydi\w*|kaydin\w*|registrations?)\b", "registration"),
            (r"\b(?:ders(?:ten|ler\w*|in|i|e)?|courses?)\b", "course"),
            (r"\b(?:sinif\w*|classes|class)\b", "class"),
            (r"\b(?:sinav\w*|exams?|examinations?)\b", "exam"),
            (r"\b(?:guz|fall)\b", "fall"), (r"\b(?:bahar|spring)\b", "spring"),
            (r"\b(?:yaz|summer)\b", "summer"), (r"\b(?:tatil\w*|holidays?|breaks?)\b", "holiday"),
            (r"\b(?:yariyil\w*|donem\w*|terms?|semesters?)\b", "term"),
            (r"\b(?:cekil\w*|withdraw\w*)\b", "withdrawal"), (r"\bfinal\w*\b", "final"),
            (r"\b(?:butunleme\w*|mazeret\w*|make\s*-?\s*up)\b", "makeup"),
            (r"\b(?:birak\w*|sil\w*|drop)\b", "drop"), (r"\b(?:ekle\w*|add)\b", "add"),
        )
        for pattern, replacement in replacements:
            text = re.sub(pattern, replacement, text)
        return re.findall(r"\w+", text)
    official_text = " ".join(str(event.get(k) or "") for k in ("description", "description_tr", "date"))
    if normalize_lookup_text(query) in normalize_lookup_text(official_text):
        return True
    aliases = []
    if event.get("registration_kind") == "add_drop":
        aliases.append("add drop")
    if event.get("registration_kind") == "withdrawal":
        aliases.append("withdrawal")
    if event.get("category") == "exam" and re.search(r"(?:yariyili|ogretimi) sonu sinav", normalize_lookup_text(event.get("description_tr"))):
        aliases.append("final")
    if event.get("category") == "registration":
        aliases.append("registration")
        if event.get("scope") in {"undergraduate", "graduate"}:
            aliases.append("course")
    if event.get("class_levels"):
        aliases.extend(f"{number} class" for number in event["class_levels"])
    haystack = set(tokens(" ".join(str(event.get(k) or "") for k in ("description", "description_tr", "date", "academic_term", "category")) + " " + " ".join(aliases)))
    wanted = set(tokens(query)) - _QUERY_FILLER
    return bool(wanted) and wanted.issubset(haystack)
