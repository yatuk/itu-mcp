"""Parse dated events and the stated coverage of official ITU calendars."""

from __future__ import annotations

import calendar as month_calendar
import re
from datetime import date, datetime, time
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
            start_time = None
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


def _metadata(description: str, context: str) -> dict[str, Any]:
    text = normalize_lookup_text(description)
    whole = normalize_lookup_text(f"{context} {description}")
    classes = sorted({int(n) for n in re.findall(r"\b([1-4])(?:st|nd|rd|th|\.)?\s*(?:class|sinif)\b", text)})
    # Year-of-study synonyms are used only alongside registration wording.
    registration = any(word in text for word in ("registration", "kayit", "kaydi", "kaydinin", "course selection", "add/drop", "add drop", "add-drop", "bir baska derse yazilma"))
    if registration and not classes:
        classes = [n for label, n in (("freshman", 1), ("sophomore", 2), ("junior", 3), ("senior", 4)) if re.search(rf"\b{label}\b", text)]
    if registration:
        category = "registration"
    elif any(word in text for word in ("exam", "sinav", "final", "midterm", "butunleme")):
        category = "exam"
    elif any(word in text for word in ("holiday", "tatil", "bayram")):
        category = "holiday"
    elif any(word in text for word in ("term", "semester", "donem", "classes", "yariyil")):
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
        elif any(word in text for word in ("add/drop", "add drop", "add-drop", "bir baska derse yazilma")):
            kind = "add_drop"
        elif any(word in text for word in ("exam registration", "sinav kay")):
            kind = "exam_registration"
        elif "cezali" in text or "late registration" in text:
            kind = "late_registration"
        else:
            kind = "other"
        result["registration_kind"] = kind
    term = None
    for label, pattern in (("fall", r"\b(fall|guz)\b"), ("spring", r"\b(spring|bahar)\b"), ("summer", r"\b(summer|yaz)\b")):
        if re.search(pattern, whole):
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
        raw_date = clean_text(raw_date).rstrip(" :")
        description = clean_text(description)
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
        key = (*[item[k] for k in ("start_date", "end_date", "start_at", "end_at")], normalize_lookup_text(description), normalize_lookup_text(source_calendar))
        if key in events:
            if item["academic_term"] and not events[key].get("academic_term"):
                events[key]["academic_term"] = item["academic_term"]
        else:
            events[key] = item

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
    current_semester = None
    for event in ordered:
        description = normalize_lookup_text(event["description"])
        for term, label in (("fall term", "Fall (Güz)"), ("spring term", "Spring (Bahar)"), ("summer term", "Summer (Yaz)"), ("summer school", "Summer School (Yaz Okulu)")):
            if term in description and "beginning of" in description:
                current_semester = label
                semesters.append({"semester": label, "start": event["date"], "type": "start"})
            elif term in description and "end of" in description:
                semesters.append({"semester": label, "end": event["date"], "type": "end"})
    result: dict[str, Any] = {"url": page_url, "event_count": len(ordered), "events": ordered,
        "semesters": semesters, "current_semester": current_semester, "source": urlparse(page_url).hostname,
        "timezone": TIMEZONE, "coverage": coverage, "parse_complete": complete,
        "unparsed_event_count": len(unparsed), "truncated": False,
        "note": "Coverage describes the fetched page only. Class registration windows are public schedules, not confirmation of personal eligibility."}
    if unparsed or not ordered:
        result["parse_warning"] = (f"Could not parse {len(unparsed)} dated event entries. Results may be incomplete." if unparsed else "No recognizable dated events were found. This does not establish an empty calendar.")
        result["unparsed_events"] = [{"date": d, "source_calendar": c} for d, c in sorted(unparsed)]
    return result


def calendar_query_matches(event: dict[str, Any], query: str) -> bool:
    """Match words and a small explicit Turkish/English academic vocabulary."""
    def tokens(value: str) -> list[str]:
        text = normalize_lookup_text(value)
        replacements = (
            (r"\b(?:kayit\w*|kaydi\w*|kaydin\w*|registrations?)\b", "registration"),
            (r"\b(?:ders(?:ler\w*|in|i|e)?|courses?)\b", "course"),
            (r"\b(?:sinif\w*|classes|class)\b", "class"),
            (r"\b(?:sinav\w*|exams?|examinations?)\b", "exam"),
            (r"\b(?:guz|fall)\b", "fall"), (r"\b(?:bahar|spring)\b", "spring"),
            (r"\b(?:yaz|summer)\b", "summer"), (r"\b(?:tatil\w*|holidays?)\b", "holiday"),
        )
        for pattern, replacement in replacements:
            text = re.sub(pattern, replacement, text)
        return re.findall(r"\w+", text)
    official_text = f"{event.get('description') or ''} {event.get('date') or ''}"
    if normalize_lookup_text(query) in normalize_lookup_text(official_text):
        return True
    aliases = []
    if event.get("category") == "registration":
        aliases.append("registration")
        if event.get("scope") in {"undergraduate", "graduate"}:
            aliases.append("course")
    if event.get("class_levels"):
        aliases.extend(f"{number} class" for number in event["class_levels"])
    haystack = set(tokens(" ".join(str(event.get(k) or "") for k in ("description", "date", "academic_term", "category")) + " " + " ".join(aliases)))
    return set(tokens(query)).issubset(haystack)
