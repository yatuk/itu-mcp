"""Aggregation helpers over the İTÜ ders arşivi.

Pure functions on already-fetched archive JSON: no network, no I/O. The archive
stores history as compact positional rows; everything here turns those into
named fields and the summaries a student actually asks for.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# Branch codes run 2-4 letters; course numbers are 3 digits for normal courses
# and 4 for capstone/design courses (CEN 4901E). The trailing letters cover the
# English suffix and lab variants like FIZ 101EL.
COURSE_CODE_PATTERN = re.compile(
    r"\s*([A-Za-zÇĞİÖŞÜçğıöşü]{2,4})\s*(\d{3,4})\s*([A-Za-z]{0,2})\s*"
)

SEASON_LABELS = {"guz": "Güz", "bahar": "Bahar", "yaz": "Yaz"}

# Positional layout of history/courses/<BRANCH>.json rows.
_COURSE_ROW_FIELDS = ("term", "instructor", "capacity", "enrolled", "days")
# Positional layout of history/instructors/<letter>.json rows.
_INSTRUCTOR_ROW_FIELDS = ("term", "code", "name", "capacity", "enrolled")


def split_course_code(course_code: str) -> tuple[str, str]:
    """Split ``"CEN 4901E"`` into ``("CEN", "4901E")``.

    Accepts 3- and 4-digit course numbers and one- or two-letter suffixes, so
    capstone courses and lab variants resolve like any other course.
    """
    match = COURSE_CODE_PATTERN.fullmatch(course_code or "")
    if not match:
        raise ValueError(
            f"Ders kodu çözümlenemedi: {course_code!r}. 'BLG 223E' veya 'CEN 4901E' gibi olmalı."
        )
    branch, number, suffix = match.groups()
    return branch.upper(), f"{number}{suffix}".upper()


def normalize_course_code(course_code: str) -> str:
    """Return the canonical ``"BRANCH NUMBER"`` spelling of a course code."""
    branch, number = split_course_code(course_code)
    return f"{branch} {number}"


def season_of(term_slug: str) -> str:
    """Return ``"Güz"`` / ``"Bahar"`` / ``"Yaz"`` for ``"2025-2026-guz"``."""
    tail = (term_slug or "").rsplit("-", 1)[-1].lower()
    return SEASON_LABELS.get(tail, tail or "?")


def _term_sort_key(term_slug: str) -> tuple[int, int]:
    """Order terms chronologically: academic year, then season within it."""
    order = {"guz": 0, "bahar": 1, "yaz": 2}
    parts = (term_slug or "").split("-")
    try:
        year = int(parts[0])
    except (ValueError, IndexError):
        year = 0
    return year, order.get(parts[-1].lower() if parts else "", 9)


# -- placeholders -------------------------------------------------------------

# Cells OBS and the older dumps use for "nothing here": dashes in several
# spellings, the literal "undeclared", and "Detay", which is the text of a
# link to a detail page and not the detail itself.
_PLACEHOLDER_WORDS = frozenset({"undeclared", "detay"})
_PLACEHOLDER_MARKS = re.compile(r"[-‐-―/\s]*")


def clean_placeholder(value: Any) -> Any:
    """Return ``None`` for a placeholder cell, the stripped text otherwise.

    ``""``, ``"-"``, ``"--"``, ``"- -"``, ``"-/-"``, ``"undeclared"`` and
    ``"Detay"`` (any case, any spacing) are not data. Non-strings pass through.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if _PLACEHOLDER_MARKS.fullmatch(text) or text.casefold() in _PLACEHOLDER_WORDS:
        return None
    return text


def clean_list(values: Any) -> Any:
    """Drop placeholder items and repeats from a list, keeping the first order."""
    if not isinstance(values, list):
        return clean_placeholder(values)
    seen: set[str] = set()
    out: list[Any] = []
    for item in values:
        cleaned = clean_placeholder(item)
        if cleaned is None:
            continue
        key = repr(cleaned)
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def is_empty_record(value: Any) -> bool:
    """A dict whose every value is a placeholder, like an unfilled provenance."""
    return isinstance(value, dict) and all(
        clean_placeholder(item) is None or item == [] or item == {} for item in value.values()
    )


def _rows_as_dicts(rows: Any, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Zip positional archive rows onto field names, skipping malformed rows.

    An instructor cell that holds only a placeholder (``-``, ``--``) becomes
    ``None``: the section had no announced instructor.
    """
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, list) or len(row) < len(fields):
            continue
        entry = dict(zip(fields, row, strict=False))
        entry["season"] = season_of(str(entry.get("term") or ""))
        if "instructor" in entry:
            entry["instructor"] = clean_placeholder(entry["instructor"])
        out.append(entry)
    return out


def _fill_ratio(capacity: Any, enrolled: Any) -> float | None:
    try:
        cap = float(capacity)
        enr = float(enrolled)
    except (TypeError, ValueError):
        return None
    if cap <= 0:
        return None
    return round(enr / cap, 3)


# -- was enrolment captured at all? -------------------------------------------

ENROLMENT_NOT_CAPTURED_NOTE = (
    "enrolment_not_captured işaretli dönemlerde kayıtlı öğrenci sayısı arşive alınmamış: "
    "o dönemin dökümündeki bütün şubelerde kayıtlı sayısı 0 (büyük olasılıkla kayıtlar "
    "açılmadan önce alınmış). Bu sıfırlar ölçüm değildir; enrolled ve fill_ratio boş "
    "bırakıldı, hiçbir ortalamaya ya da orana katılmadı."
)


def enrolment_captured(pairs: Any) -> bool | None:
    """Whether a set of ``(capacity, enrolled)`` rows holds real enrolment.

    ``None`` when there is no row. ``False`` when no row has a positive
    enrolment: that is the rule "every section with a positive capacity has 0
    enrolled", and it also covers the dumps where capacity is 0 everywhere.
    One enrolled student anywhere in the rows means the numbers were captured,
    so a section with 0 next to it is a genuinely empty section.
    """
    seen = False
    for _capacity, enrolled in pairs:
        seen = True
        if _is_count(enrolled) and enrolled > 0:
            return True
    return False if seen else None


def stats_enrolment_captured(stats: Any) -> bool | None:
    """Read the term-wide ``stats`` block of ``meta.json`` or ``index.json``.

    The block sums every branch, so it settles the question for the whole
    term where one small branch file alone could not.
    """
    if not isinstance(stats, dict) or not _is_count(stats.get("enrolled")):
        return None
    return bool(stats["enrolled"] > 0)


def enrolment_uncaptured_terms(history: Any, *, captured_terms: Any = ()) -> list[str]:
    """Terms of one ``history/courses/<BRANCH>.json`` file without enrolment.

    The decision is taken per term over every course of the branch file, not
    per course. ``captured_terms`` names terms known from elsewhere to hold
    enrolment (the live term, by the index totals); they are never returned.
    """
    by_term: dict[str, bool] = {}
    for entry in history.values() if isinstance(history, dict) else []:
        if not isinstance(entry, dict):
            continue
        for row in entry.get("rows") or []:
            if not isinstance(row, list) or len(row) < 4:
                continue
            term = str(row[0] or "")
            positive = _is_count(row[3]) and row[3] > 0
            by_term[term] = by_term.get(term, False) or positive
    known = {str(term) for term in captured_terms or ()}
    return sorted(
        (term for term, positive in by_term.items() if not positive and term not in known),
        key=_term_sort_key,
        reverse=True,
    )


def _uncaptured_set(entry: dict[str, Any], uncaptured_terms: Any) -> set[str]:
    source = uncaptured_terms if uncaptured_terms is not None else entry.get("enrolment_uncaptured_terms")
    return {str(term) for term in source or ()}


def course_history(
    entry: dict[str, Any],
    *,
    limit_terms: int | None = None,
    uncaptured_terms: Any = None,
) -> dict[str, Any]:
    """Turn one ``history/courses`` entry into a term-by-term offering record.

    Groups the flat section rows by term so "this course ran in 13 terms, always
    in Bahar, with these instructors" is readable in one pass.

    ``uncaptured_terms`` (or the entry's own ``enrolment_uncaptured_terms``)
    names the terms whose enrolment the archive never captured. Their sections
    carry no ``enrolled`` and no ``fill_ratio``.
    """
    rows = _rows_as_dicts(entry.get("rows"), _COURSE_ROW_FIELDS)
    uncaptured = _uncaptured_set(entry, uncaptured_terms)
    by_term: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_term.setdefault(str(row.get("term") or ""), []).append(row)

    ordered = sorted(by_term, key=_term_sort_key, reverse=True)
    if limit_terms is not None:
        ordered = ordered[:limit_terms]

    offerings: list[dict[str, Any]] = []
    for term in ordered:
        sections = by_term[term]
        missing = term in uncaptured
        offerings.append({
            "term": term,
            "season": season_of(term),
            "section_count": len(sections),
            "instructors": sorted({str(s["instructor"]) for s in sections if s.get("instructor")}),
            "unannounced_section_count": sum(1 for s in sections if not s.get("instructor")),
            "enrolment_not_captured": missing,
            "sections": [
                {
                    "instructor": s.get("instructor"),
                    "capacity": s.get("capacity"),
                    "enrolled": None if missing else s.get("enrolled"),
                    "fill_ratio": None if missing else _fill_ratio(s.get("capacity"), s.get("enrolled")),
                    "days": s.get("days"),
                }
                for s in sections
            ],
        })

    return {
        "course_code": entry.get("code"),
        "course_name": entry.get("name"),
        "terms_offered": len(by_term),
        "seasonality": seasonality(list(by_term)),
        "enrolment_not_captured_terms": sorted(
            (term for term in by_term if term in uncaptured), key=_term_sort_key, reverse=True
        ),
        "offerings": offerings,
        "truncated": limit_terms is not None and len(by_term) > len(ordered),
    }


# Fewer term records than this say nothing about when a course opens: the
# archive misses whole branches in older terms, so one record is often just
# the current term.
MIN_SEASONALITY_TERMS = 3
_REGULAR_SEASONS = ("Güz", "Bahar")
_SUMMER = "Yaz"


def seasonality(term_slugs: list[str]) -> dict[str, Any]:
    """Summarise which seasons a course actually opens in.

    Güz and Bahar are the regular terms; Yaz (summer school) is counted apart
    and never decides the pattern of the other two. ``pattern`` is one of:

    - ``no_data``: no term record.
    - ``insufficient_data``: fewer than ``MIN_SEASONALITY_TERMS`` records.
    - ``every_regular_term``: both regular terms, neither with two thirds of
      the regular-term records.
    - ``mostly_one_season``: both regular terms, one with at least two thirds
      of the regular-term records (``dominant_season``).
    - ``single_season``: only one regular term (``only_regular_season``).
    - ``summer_only``: only summer records.

    ``only_season`` is set only when every record is in one season and there
    are enough records to say so.
    """
    counts: dict[str, int] = {}
    for slug in term_slugs:
        counts[season_of(slug)] = counts.get(season_of(slug), 0) + 1
    total = sum(counts.values())
    regular = {season: counts.get(season, 0) for season in _REGULAR_SEASONS}
    regular_total = sum(regular.values())
    summer = counts.get(_SUMMER, 0)
    larger = max(regular, key=lambda season: regular[season])

    pattern = "no_data"
    dominant: str | None = None
    only_regular: str | None = None
    if 0 < total < MIN_SEASONALITY_TERMS:
        pattern = "insufficient_data"
    elif total and regular_total == 0:
        pattern, dominant = ("summer_only", _SUMMER) if summer == total else ("insufficient_data", None)
    elif total and min(regular.values()) == 0:
        pattern, dominant, only_regular = "single_season", larger, larger
    elif total and 3 * regular[larger] >= 2 * regular_total:
        pattern, dominant = "mostly_one_season", larger
    elif total:
        pattern = "every_regular_term"
    return {
        "counts": counts,
        "total_terms": total,
        "pattern": pattern,
        "dominant_season": dominant,
        "only_season": dominant if dominant and counts.get(dominant) == total else None,
        "only_regular_season": only_regular,
        "regular_terms": regular_total,
        "summer_terms": summer,
        "minimum_terms_for_a_pattern": MIN_SEASONALITY_TERMS,
    }


def seasonality_sentence(course_code: str, summary: dict[str, Any]) -> str:
    """One Turkish sentence for a ``seasonality`` summary, no stronger than the data."""
    counts = summary.get("counts") or {}
    total = summary.get("total_terms") or 0
    summer = summary.get("summer_terms") or 0
    pattern = summary.get("pattern")
    dominant = summary.get("dominant_season")
    by_season = ", ".join(
        f"{season} {counts[season]}" for season in (*_REGULAR_SEASONS, _SUMMER) if counts.get(season)
    )
    if pattern == "insufficient_data":
        return (
            f"{course_code} için arşivde yalnızca {total} dönem kaydı var ({by_season}); hangi dönemlerde "
            "açıldığını söylemek için yetersiz veri. Bu, dersin yalnızca o dönemde açıldığı anlamına gelmez."
        )
    if pattern == "every_regular_term":
        return f"{course_code} her iki normal dönemde de açılıyor (dönem kaydı: {by_season})."
    if pattern == "mostly_one_season":
        return f"{course_code} çoğunlukla {dominant} döneminde açılıyor (dönem kaydı: {by_season})."
    if pattern == "single_season" and summer:
        return (
            f"{course_code} normal dönemlerden yalnızca {dominant} döneminde açılıyor "
            f"({counts.get(dominant, 0)} dönem kaydı); ayrıca {summer} yaz döneminde açılmış."
        )
    if pattern == "single_season":
        return f"{course_code} yalnızca {dominant} döneminde açılıyor ({total} dönem kaydı)."
    if pattern == "summer_only":
        return f"{course_code} arşivde yalnızca Yaz döneminde açılmış ({total} dönem kaydı)."
    return f"{course_code} için arşivde dönem kaydı yok."


def who_taught(entry: dict[str, Any], *, limit_terms: int | None = None) -> dict[str, Any]:
    """Rank instructors for one course by how often and how recently they taught it.

    Sections of a term whose enrolment was never captured (see
    ``course_history``) count as taught but add nothing to the fill average.
    """
    rows = _rows_as_dicts(entry.get("rows"), _COURSE_ROW_FIELDS)
    uncaptured = _uncaptured_set(entry, None)
    if limit_terms is not None:
        keep = sorted({str(r.get("term") or "") for r in rows}, key=_term_sort_key, reverse=True)[:limit_terms]
        rows = [r for r in rows if str(r.get("term") or "") in set(keep)]

    grouped: dict[str, dict[str, Any]] = {}
    unannounced = 0
    for row in rows:
        # A placeholder was already turned into None: nobody was announced
        # for that section, so it ranks no one.
        name = str(row.get("instructor") or "").strip()
        if not name:
            unannounced += 1
            continue
        bucket = grouped.setdefault(name, {"instructor": name, "terms": set(), "sections": 0, "ratios": []})
        bucket["terms"].add(str(row.get("term") or ""))
        bucket["sections"] += 1
        if str(row.get("term") or "") in uncaptured:
            continue
        ratio = _fill_ratio(row.get("capacity"), row.get("enrolled"))
        if ratio is not None:
            bucket["ratios"].append(ratio)

    instructors = []
    for bucket in grouped.values():
        terms = sorted(bucket["terms"], key=_term_sort_key, reverse=True)
        ratios = bucket["ratios"]
        instructors.append({
            "instructor": bucket["instructor"],
            "term_count": len(terms),
            "section_count": bucket["sections"],
            "latest_term": terms[0] if terms else None,
            "terms": terms,
            "average_fill_ratio": round(sum(ratios) / len(ratios), 3) if ratios else None,
            "fill_ratio_section_count": len(ratios),
        })
    instructors.sort(
        key=lambda i: (i["term_count"], _term_sort_key(i["latest_term"] or "")),
        reverse=True,
    )
    return {
        "course_code": entry.get("code"),
        "course_name": entry.get("name"),
        "enrolment_not_captured_terms": sorted(
            ({str(r.get("term") or "") for r in rows} & uncaptured), key=_term_sort_key, reverse=True
        ),
        "unannounced_section_count": unannounced,
        "instructors": instructors,
    }


def instructor_courses(entry: dict[str, Any], *, limit: int | None = None) -> dict[str, Any]:
    """Summarise every course one instructor has taught, newest first."""
    rows = _rows_as_dicts(entry.get("rows"), _INSTRUCTOR_ROW_FIELDS)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        bucket = grouped.setdefault(code, {
            "course_code": code,
            "course_name": row.get("name"),
            "terms": set(),
            "sections": 0,
        })
        bucket["terms"].add(str(row.get("term") or ""))
        bucket["sections"] += 1

    courses = []
    for bucket in grouped.values():
        terms = sorted(bucket["terms"], key=_term_sort_key, reverse=True)
        courses.append({
            "course_code": bucket["course_code"],
            "course_name": bucket["course_name"],
            "term_count": len(terms),
            "section_count": bucket["sections"],
            "latest_term": terms[0] if terms else None,
            "terms": terms,
        })
    courses.sort(key=lambda c: (_term_sort_key(c["latest_term"] or ""), c["term_count"]), reverse=True)
    total = len(courses)
    if limit is not None:
        courses = courses[:limit]
    return {
        "instructor": entry.get("name"),
        "distinct_courses": total,
        "term_count": entry.get("terms"),
        "courses": courses,
        "truncated": total > len(courses),
    }


def summarize_section(section: dict[str, Any], *, enrolment_captured: bool = True) -> dict[str, Any]:
    """Flatten one archived section record into a compact, readable row.

    The archive stores day/time/building as parallel arrays because OBS packs
    multiple weekly sessions into one cell; recombine them into sessions.
    ``enrolment_captured=False`` says the term's enrolment was never captured:
    ``enrolled`` and ``fill_ratio`` are then left empty instead of 0.
    """
    enrolled = section.get("enrolled") if enrolment_captured else None
    days = section.get("days") or []
    times = section.get("times") or []
    buildings = section.get("buildings") or []
    rooms = section.get("rooms") or []
    sessions = []
    for index in range(max(len(days), len(times))):
        sessions.append({
            "day": clean_placeholder(days[index]) if index < len(days) else None,
            "time": clean_placeholder(times[index]) if index < len(times) else None,
            "building": clean_placeholder(buildings[index]) if index < len(buildings) else None,
            "room": clean_placeholder(rooms[index]) if index < len(rooms) else None,
        })
    return {
        "crn": section.get("crn"),
        "course_code": section.get("code"),
        "course_name": section.get("name"),
        # None: no instructor announced. "-" and "--" are not people.
        "instructor": clean_placeholder(section.get("instructor")),
        "level": clean_placeholder(section.get("level")),
        "capacity": section.get("capacity"),
        "enrolled": enrolled,
        "fill_ratio": _fill_ratio(section.get("capacity"), enrolled),
        "sessions": sessions,
        "programs": clean_list(section.get("programs")),
        # "Detay" is the text of a link to the rule, not the rule; "-" is none.
        "prerequisite_note": clean_placeholder(section.get("prereq") or None),
        "class_requirement": clean_placeholder(section.get("classReq") or None),
        "method": clean_placeholder(section.get("method") or None),
        "reserved": clean_placeholder(section.get("reserved") or None),
    }


def fill_summary(quota: dict[str, Any], crn: str) -> dict[str, Any] | None:
    """Look one CRN up in a term's derived quota summary."""
    target = str(crn).strip()
    for course in quota.get("courses") or []:
        if str(course.get("crn") or "").strip() == target:
            entry = dict(course)
            entry["fill_ratio"] = _fill_ratio(course.get("capacity"), course.get("enrolled"))
            entry["is_full"] = bool(course.get("filledAt")) or (
                entry["fill_ratio"] is not None and entry["fill_ratio"] >= 1.0
            )
            return entry
    return None


def _name_key(name: Any) -> str:
    """Course name without case, accents, punctuation or spacing differences."""
    from .parsing import normalize_lookup_text

    folded = normalize_lookup_text(str(name or "")).replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def search_courses(codes: list[list[Any]], query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Search the archive's flat course index by code or name fragment.

    ``codes`` is ``history/codes.json``: ``[code, name, branch, term_count]``
    rows. Exact and prefix code matches rank above substring code matches,
    which rank above name matches, so a code lookup like "BLG 102" still wins
    over an unrelated course whose name happens to contain "102". Codes are
    compared without spacing or case (``cen411e``), names without case,
    accents or punctuation (``yazilim`` finds ``Yazılım``); a name also
    matches when it holds every word of the query in another order.
    """
    target_name = _name_key(query)
    target_code = target_name.replace(" ", "")
    if not target_code:
        return []
    words = target_name.split()

    ranked: list[tuple[int, dict[str, Any]]] = []
    for row in codes:
        if not isinstance(row, list) or len(row) < 4:
            continue
        code, name, branch, term_count = row[0], row[1], row[2], row[3]
        norm_code = _name_key(code).replace(" ", "")
        norm_name = _name_key(name)
        if target_code == norm_code:
            rank = 0
        elif norm_code.startswith(target_code):
            rank = 1
        elif target_code in norm_code:
            rank = 2
        elif target_name in norm_name:
            rank = 3
        elif len(words) > 1 and set(words) <= set(norm_name.split()):
            rank = 4
        else:
            continue
        ranked.append((rank, {
            "course_code": code,
            "course_name": name,
            "branch": branch,
            "term_count": term_count,
        }))

    ranked.sort(key=lambda item: (item[0], -(item[1]["term_count"] or 0)))
    return [item[1] for item in ranked[:limit]]


_TURKISH_LETTERS = frozenset("çğıöşüÇĞİÖŞÜ")


def course_search_hint(codes: Any, query: str, matches: list[Any]) -> dict[str, Any] | None:
    """Why a name search found nothing, for a caller to attach to an empty result.

    The index holds one name per course, in the language the course is taught
    in: a code ending in ``E`` carries an English name only. Nothing is
    translated here. Whether the query looks Turkish is read from the index
    itself: a word that occurs in the names of Turkish-taught courses and in
    no English-taught one.
    """
    words = _name_key(query).split()
    if matches or not words:
        return None
    turkish_words: set[str] = set()
    english_words: set[str] = set()
    for row in codes if isinstance(codes, list) else []:
        if not isinstance(row, list) or len(row) < 2:
            continue
        match = COURSE_CODE_PATTERN.fullmatch(str(row[0]))
        english = bool(match and match.group(3).upper().startswith("E"))
        (english_words if english else turkish_words).update(_name_key(row[1]).split())
    looks_turkish = bool(_TURKISH_LETTERS & set(str(query))) or any(
        word in turkish_words and word not in english_words for word in words
    )
    hint = (
        "Eşleşme yok. Arşiv dizini her ders için tek bir ad tutuyor: dersin öğretim dilindeki adı. "
        "Kodu E ile biten (İngilizce verilen) dersler yalnızca İngilizce adıyla kayıtlı"
        + ("; sorgu Türkçe görünüyor. Dersin İngilizce adıyla" if looks_turkish
           else ", diğerleri Türkçe adıyla. Adın diğer dildeki karşılığıyla")
        + " (örn. 'Software Engineering') ya da ders koduyla (örn. 'BLG 411E') yeniden arayın."
    )
    return {"hint": hint, "query_looks_turkish": looks_turkish}


MAX_OTHER_BRANCH_CODES = 5


def same_course_other_branches(
    course_code: str, course_name: Any, codes: Any, *, limit: int = MAX_OTHER_BRANCH_CODES,
) -> list[dict[str, Any]]:
    """Index rows that may be the same course under another department code.

    A candidate has the same course number and suffix, another branch code and
    the same normalized name (``CEN 411E`` and ``BLG 411E``, both "Software
    Engineering"). ``codes`` is ``history/codes.json``. The index holds a term
    count per code but no term list, so a candidate carries no term range.
    Candidates are ranked by term count and capped; nothing is merged.
    """
    own = COURSE_CODE_PATTERN.fullmatch(course_code or "")
    name = _name_key(course_name)
    if not own or not name:
        return []
    branch = own.group(1).upper()
    number = (own.group(2) + own.group(3)).upper()
    found: dict[str, dict[str, Any]] = {}
    for row in codes if isinstance(codes, list) else []:
        if not isinstance(row, list) or len(row) < 4:
            continue
        other = COURSE_CODE_PATTERN.fullmatch(str(row[0]))
        if not other or other.group(1).upper() == branch:
            continue
        if (other.group(2) + other.group(3)).upper() != number or _name_key(row[1]) != name:
            continue
        found.setdefault(str(row[0]), {
            "course_code": str(row[0]),
            "course_name": row[1],
            "branch": row[2],
            "term_count": row[3],
            "evidence": {"same_number": True, "same_name": True},
        })
    ranked = sorted(
        found.values(),
        key=lambda item: (-(item["term_count"] if _is_count(item["term_count"]) else 0), item["course_code"]),
    )
    return ranked[:max(0, limit)]


def diff_term_offerings(
    sections_a: list[dict[str, Any]],
    sections_b: list[dict[str, Any]],
    *,
    enrolment_captured_a: bool = True,
    enrolment_captured_b: bool = True,
) -> dict[str, Any]:
    """Compare a course's sections between two terms.

    Takes already-summarized sections (``summarize_section`` output) for the
    same course in two different terms and reports what changed: instructor
    turnover, section-count delta, and capacity/fill movement. This is the
    piece a manual side-by-side ``archive_course_history`` read cannot give
    directly — it names *what changed*, not just two snapshots to compare by eye.

    A side whose enrolment was never captured keeps its sections, capacity and
    instructors but has no enrolment total and no fill average, and the
    enrolment comparison is reported as not comparable.
    """

    def _summarize_side(sections: list[dict[str, Any]], captured: bool) -> dict[str, Any]:
        named = [clean_placeholder(s.get("instructor")) for s in sections]
        instructors = sorted({str(name) for name in named if name})
        capacities = [s["capacity"] for s in sections if _is_count(s.get("capacity"))]
        enrolled = [s["enrolled"] for s in sections if _is_count(s.get("enrolled"))] if captured else []
        ratios = [s["fill_ratio"] for s in sections if s.get("fill_ratio") is not None] if captured else []
        return {
            "section_count": len(sections),
            "instructors": instructors,
            "unannounced_section_count": sum(1 for name in named if not name),
            "total_capacity": sum(capacities) if capacities else None,
            "total_enrolled": sum(enrolled) if enrolled else None,
            "average_fill_ratio": round(sum(ratios) / len(ratios), 3) if ratios else None,
            "enrolment_not_captured": not captured,
        }

    side_a = _summarize_side(sections_a, enrolment_captured_a)
    side_b = _summarize_side(sections_b, enrolment_captured_b)

    def _delta(key: str) -> Any:
        if side_a[key] is None or side_b[key] is None:
            return None
        return round(side_b[key] - side_a[key], 3)

    enrolment_comparable = (
        enrolment_captured_a
        and enrolment_captured_b
        and side_a["total_enrolled"] is not None
        and side_b["total_enrolled"] is not None
    )
    return {
        "first": side_a,
        "second": side_b,
        "instructors_added": sorted(set(side_b["instructors"]) - set(side_a["instructors"])),
        "instructors_removed": sorted(set(side_a["instructors"]) - set(side_b["instructors"])),
        "section_count_delta": side_b["section_count"] - side_a["section_count"],
        "capacity_delta": _delta("total_capacity"),
        "enrolment_comparable": enrolment_comparable,
        "enrolled_delta": _delta("total_enrolled") if enrolment_comparable else None,
        "average_fill_ratio_delta": _delta("average_fill_ratio") if enrolment_comparable else None,
    }


def recommend_course_timing(
    course_code: str,
    seasonality_summary: dict[str, Any],
    top_instructors: list[dict[str, Any]],
) -> str:
    """Render a one-line scheduling recommendation from archive history.

    Combines what ``seasonality`` and ``who_taught`` already compute
    separately into the sentence a student actually wants: which term to plan
    around, and who is likely to teach it.
    """
    parts: list[str] = [seasonality_sentence(course_code, seasonality_summary)]

    if top_instructors:
        top = top_instructors[0]
        fill = top.get("average_fill_ratio")
        fill_text = f", ort. doluluk {fill}" if fill is not None else ""
        parts.append(
            f"En sık {top['instructor']} veriyor ({top['term_count']} dönem, "
            f"son: {top['latest_term']}{fill_text})."
        )

    return " ".join(parts)


# -- extra datasets: grades, catalog, term-wide search, exams, status --------

# OBS term ids end in 10/20/30 for Güz/Bahar/Yaz and carry the *ending* year of
# the academic year: 202410 is 2023-2024 Güz.
_DONEM_SEASONS = {"10": "guz", "20": "bahar", "30": "yaz"}

# Positional layout of terms/<slug>/search.json rows, taken from the archive
# site's own reader (assets/views/courses.js) and checked field by field against
# terms/<slug>/branches/<BRANCH>.json. ``when`` is "Day HH:MM/HH:MM" sessions
# joined by " | "; ``where`` is the matching "BUILDING ROOM" list. Dumps older
# than the live scraper stop after ``programs`` (11 columns), so everything past
# ``enrolled`` is optional.
SEARCH_ROW_FIELDS = (
    "crn", "code", "name", "branch", "instructor", "when",
    "capacity", "enrolled", "level", "method", "programs", "where",
)
_SEARCH_ROW_REQUIRED = 8

# Folded day names as they appear in ``when``, plus the English names people type.
_DAY_ALIASES = {
    "pazartesi": "pazartesi", "monday": "pazartesi",
    "sali": "sali", "tuesday": "sali",
    "carsamba": "carsamba", "wednesday": "carsamba",
    "persembe": "persembe", "thursday": "persembe",
    "cuma": "cuma", "friday": "cuma",
    "cumartesi": "cumartesi", "saturday": "cumartesi",
    "pazar": "pazar", "sunday": "pazar",
}


def _code_key(course_code: Any) -> str:
    """Compare course codes ignoring case and spacing only, never the suffix."""
    return re.sub(r"\s+", "", str(course_code or "")).upper()


def _is_count(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def term_slug_from_donem(donem: Any) -> str | None:
    """Turn an OBS term id like ``"202410"`` into ``"2023-2024-guz"``."""
    text = str(donem or "").strip()
    if not re.fullmatch(r"\d{6}", text):
        return None
    season = _DONEM_SEASONS.get(text[4:])
    if season is None:
        return None
    year = int(text[:4])
    return f"{year - 1}-{year}-{season}"


# -- term arguments -----------------------------------------------------------

TERM_FORMS_HELP = (
    "Kabul edilen biçimler: '2025-2026-guz' (arşiv kodu), 202610 (OBS dönem kodu; son iki "
    "hane 10 Güz, 20 Bahar, 30 Yaz ve yıl akademik yılın bitiş yılı), '2025-2026 Güz Dönemi' "
    "ya da '2025-2026 Fall' (dönem adı; Güz/Bahar/Yaz, Fall/Spring/Summer)."
)

_SEASON_WORDS = {
    "guz": "guz", "fall": "guz", "autumn": "guz",
    "bahar": "bahar", "spring": "bahar",
    "yaz": "yaz", "summer": "yaz",
}
_TERM_NOISE = re.compile(r"\b(?:donemi|donem|semester|term|yariyili|yariyil)\b")
_TERM_YEARS_FIRST = re.compile(r"(\d{4})\s*[-/ ]\s*(\d{4}|\d{2})\s*[-_/ ]?\s*([a-z]+)")
_TERM_SEASON_FIRST = re.compile(r"([a-z]+)\s*[-_/ ]?\s*(\d{4})\s*[-/ ]\s*(\d{4}|\d{2})")
NEAREST_TERM_COUNT = 4


def parse_term(value: Any) -> str:
    """Turn any accepted spelling of a term into the archive slug.

    Accepts the slug (``2025-2026-guz``), the OBS term id as text or integer
    (``202610``), and the label in Turkish or English, with or without
    diacritics, case, or the word "Dönemi"/"Semester" (``2025-2026 Güz``,
    ``2025-2026 guz donemi``, ``2025-2026 Fall``). Raises ``ValueError`` with
    the accepted forms for anything else. Whether the archive holds the term
    is a separate question: see ``resolve_term``.
    """
    from .parsing import normalize_lookup_text

    if value is None or isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"Dönem çözümlenemedi: {value!r}. {TERM_FORMS_HELP}")
    text = str(value).strip()
    if re.fullmatch(r"\d{6}", text):
        slug = term_slug_from_donem(text)
        if slug is None:
            raise ValueError(
                f"Dönem çözümlenemedi: {value!r}. OBS dönem kodunun son iki hanesi 10, 20 ya da "
                f"30 olmalı. {TERM_FORMS_HELP}"
            )
        return slug
    folded = " ".join(_TERM_NOISE.sub(" ", normalize_lookup_text(text)).split())
    match = _TERM_YEARS_FIRST.fullmatch(folded)
    if match:
        first, second, word = match.groups()
    else:
        match = _TERM_SEASON_FIRST.fullmatch(folded)
        if not match:
            raise ValueError(f"Dönem çözümlenemedi: {value!r}. {TERM_FORMS_HELP}")
        word, first, second = match.groups()
    season = _SEASON_WORDS.get(word)
    start = int(first)
    end = int(second) if len(second) == 4 else (start // 100) * 100 + int(second)
    if len(second) == 2 and end < start:
        end += 100
    if season is None or end != start + 1:
        raise ValueError(f"Dönem çözümlenemedi: {value!r}. {TERM_FORMS_HELP}")
    return f"{start}-{end}-{season}"


def _term_ordinal(term_slug: str) -> int:
    year, season = _term_sort_key(term_slug)
    return year * 3 + min(season, 2)


def nearest_terms(slugs: Any, target: str | None, *, limit: int = NEAREST_TERM_COUNT) -> list[str]:
    """The archive terms closest in time to ``target``, or the newest ones."""
    known = sorted({str(slug) for slug in slugs or () if slug}, key=_term_sort_key, reverse=True)
    if target is None:
        return known[:limit]
    wanted = _term_ordinal(target)
    closest = sorted(known, key=lambda slug: (abs(_term_ordinal(slug) - wanted), -_term_ordinal(slug)))
    return sorted(closest[:limit], key=_term_sort_key, reverse=True)


def resolve_term(value: Any, index_terms: Any) -> tuple[str, dict[str, Any]]:
    """Parse a term argument and find it in the archive index.

    ``index_terms`` is the ``terms`` list of ``index.json``. Raises
    ``ValueError`` naming the accepted forms and the nearest terms the
    archive does hold, both for text that is not a term and for a term the
    archive does not list.
    """
    by_slug = {
        str(entry.get("slug")): entry
        for entry in index_terms or []
        if isinstance(entry, dict) and entry.get("slug")
    }
    try:
        slug = parse_term(value)
    except ValueError as exc:
        year = re.search(r"(?<!\d)(\d{4})(?!\d)", str(value))
        target = f"{year.group(1)}-{int(year.group(1)) + 1}-guz" if year else None
        listed = ", ".join(nearest_terms(by_slug, target)) or "yok"
        raise ValueError(
            f"{exc} Arşivdeki {'en yakın' if target else 'en yeni'} dönemler: {listed}."
        ) from exc
    entry = by_slug.get(slug)
    if entry is None:
        listed = ", ".join(nearest_terms(by_slug, slug)) or "yok"
        raise ValueError(
            f"Dönem arşivde yok: {value!r} ({slug} olarak okundu). Arşivdeki en yakın dönemler: "
            f"{listed}. Tüm liste için archive_list_terms. {TERM_FORMS_HELP}"
        )
    return slug, entry


def related_course_codes(course_code: str, known_codes: Any) -> list[str]:
    """Other codes with the same branch and number but a different suffix.

    ``BLG 212`` and ``BLG 212E`` are recorded separately by the archive. They
    are surfaced side by side, never folded into one another.
    """
    match = COURSE_CODE_PATTERN.fullmatch(course_code or "")
    if not match:
        return []
    base = (match.group(1).upper(), match.group(2))
    own = _code_key(course_code)
    related = []
    for code in known_codes or []:
        other = COURSE_CODE_PATTERN.fullmatch(str(code))
        if not other or _code_key(code) == own:
            continue
        if (other.group(1).upper(), other.group(2)) == base:
            related.append(str(code))
    return sorted(set(related))


def _grade_term_entry(record: dict[str, Any]) -> dict[str, Any]:
    raw = record.get("grades")
    grades = dict(raw) if isinstance(raw, dict) else {}
    counted = sum(value for value in grades.values() if _is_count(value))
    total = record.get("total")
    entry: dict[str, Any] = {
        "term": term_slug_from_donem(record.get("donem")),
        "term_label": record.get("term"),
        "donem": record.get("donem"),
        "total": total,
        "counted_total": counted,
        "total_mismatch": not _is_count(total) or total != counted,
        "grades": grades,
        # Shares of the counted grades, so they add up to 100 even when the
        # published total disagrees with the counts.
        "percentages": {
            label: round(value * 100 / counted, 1)
            for label, value in grades.items()
            if _is_count(value)
        } if counted > 0 else {},
        "source_url": record.get("sourceUrl"),
    }
    if entry["total_mismatch"] and _is_count(total):
        entry["total_difference"] = total - counted
    return entry


def grade_distribution(
    records: Any,
    course_code: str,
    *,
    term: str | None = None,
) -> dict[str, Any]:
    """Shape one course's letter grade records out of a branch grades file.

    Only records whose code matches exactly are used. Grade labels are passed
    through as published (including the ``+`` grades and anything unexpected
    like ``BL``); a label missing from a term is left missing, not zero-filled.
    """
    target = _code_key(course_code)
    by_code: dict[str, list[dict[str, Any]]] = {}
    for record in records or []:
        if isinstance(record, dict) and record.get("code"):
            by_code.setdefault(str(record["code"]).strip(), []).append(record)

    own = [r for code, rows in by_code.items() if _code_key(code) == target for r in rows]
    related = related_course_codes(course_code, by_code)

    entries = []
    for record in own:
        entry = _grade_term_entry(record)
        # The archive has been seen to store the very same OBS distribution
        # under both language variants. Say so, so nobody adds them together.
        twins = sorted({
            code
            for code in related
            for other in by_code[code]
            if other.get("donem") == record.get("donem")
            and other.get("grades") == record.get("grades")
            and other.get("total") == record.get("total")
        })
        if twins:
            entry["same_counts_as"] = twins
        entries.append(entry)
    entries.sort(key=lambda e: str(e.get("donem") or ""), reverse=True)

    available = [e["term"] for e in entries if e["term"]]
    if term is not None:
        entries = [e for e in entries if e["term"] == term]

    if not own:
        coverage = "course_absent_from_grades"
    elif not entries:
        coverage = "term_absent_for_course"
    else:
        coverage = "covered"

    return {
        "course_code": course_code,
        "term_filter": term,
        "coverage": coverage,
        "term_count": len(entries),
        "available_terms": available,
        "mismatch_term_count": sum(1 for e in entries if e["total_mismatch"]),
        "terms": entries,
        "related_codes": [
            {"course_code": code, "term_count": len(by_code[code])} for code in related
        ],
        "branch_terms": sorted(
            {
                slug
                for rows in by_code.values()
                for slug in (term_slug_from_donem(r.get("donem")) for r in rows)
                if slug
            },
            key=_term_sort_key,
        ),
    }


def catalog_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Rename one catalog record's fields; values are passed through untouched.

    Some records have no outcomes or weekly topics at all, and some hold only
    a placeholder. Those come back as ``None`` or an empty list and are named
    in ``missing_fields``.
    """
    fields = {
        "course_name": "name",
        "course_name_en": "nameEn",
        "language": "language",
        "credits": "credits",
        "description": "description",
        "outcomes": "outcomes",
        "weekly_topics": "weeklyTopics",
        "textbooks": "textbooks",
        "source_url": "sourceUrl",
    }
    result: dict[str, Any] = {"course_code": entry.get("code")}
    missing = []
    for out_key, source_key in fields.items():
        # A placeholder ("- -" as a description, ["-"] as the textbook list)
        # is a field the catalog does not hold, and is reported as missing.
        value = clean_list(entry.get(source_key))
        result[out_key] = value
        if value is None or value == "" or value == []:
            missing.append(out_key)
    result["missing_fields"] = missing
    return result


def normalize_day(day: str) -> str:
    """Resolve a Turkish or English day name to its folded Turkish form."""
    from .parsing import normalize_lookup_text

    key = _DAY_ALIASES.get(normalize_lookup_text(day))
    if key is None:
        raise ValueError(
            f"Gün çözümlenemedi: {day!r}. Pazartesi, Salı, Çarşamba, Perşembe, Cuma, "
            "Cumartesi veya Pazar olmalı."
        )
    return key


def _split_sessions(when: Any, where: Any) -> list[dict[str, Any]]:
    slots = [part.strip() for part in str(when or "").split("|") if part.strip()]
    places = [part.strip() for part in str(where or "").split("|")]
    sessions = []
    for index, slot in enumerate(slots):
        day, _, time_range = slot.partition(" ")
        place = places[index] if index < len(places) else ""
        sessions.append({
            "day": day or None,
            "time": time_range.strip() or None,
            # Building and room arrive as one string; not split, because the
            # boundary between them is not marked in the data.
            "location": clean_placeholder(place),
        })
    return sessions


def summarize_search_row(row: list[Any], *, enrolment_captured: bool = True) -> dict[str, Any]:
    """Name the columns of one term-wide ``search.json`` row."""
    cell = dict(zip(SEARCH_ROW_FIELDS, row, strict=False))
    if not enrolment_captured:
        cell["enrolled"] = None
    return {
        "crn": cell.get("crn"),
        "course_code": cell.get("code"),
        "course_name": cell.get("name"),
        "branch": cell.get("branch"),
        "instructor": clean_placeholder(cell.get("instructor")),
        "level": clean_placeholder(cell.get("level") or None),
        "capacity": cell.get("capacity"),
        "enrolled": cell.get("enrolled"),
        "fill_ratio": _fill_ratio(cell.get("capacity"), cell.get("enrolled")),
        "sessions": _split_sessions(cell.get("when"), cell.get("where")),
        "programs": clean_list(cell.get("programs")),
        "method": clean_placeholder(cell.get("method") or None),
    }


def search_sections(
    rows: Any,
    *,
    course_code: str | None = None,
    course_name: str | None = None,
    instructor: str | None = None,
    day: str | None = None,
    limit: int = 40,
) -> dict[str, Any]:
    """Filter one term's ``search.json`` rows; every given filter must match.

    Text filters are case-insensitive substring matches after Turkish-aware
    folding, so "sahin" finds "Şahin" and "isletme" finds "İşletme". The code
    filter also ignores spaces ("blg22" matches "BLG 223E"). ``day`` matches a
    section that meets on that day in any of its sessions.
    """
    from .parsing import normalize_lookup_text

    def fold(value: Any) -> str:
        return normalize_lookup_text(str(value)) if value is not None else ""

    code_target = fold(course_code).replace(" ", "")
    name_target = fold(course_name)
    instructor_target = fold(instructor)
    day_target = normalize_day(day) if day else ""

    usable = 0
    matched = []
    any_enrolled = False
    for row in rows or []:
        if not isinstance(row, list) or len(row) < _SEARCH_ROW_REQUIRED:
            continue
        usable += 1
        any_enrolled = any_enrolled or (_is_count(row[7]) and row[7] > 0)
        if code_target and code_target not in fold(row[1]).replace(" ", ""):
            continue
        if name_target and name_target not in fold(row[2]):
            continue
        if instructor_target and instructor_target not in fold(row[4]):
            continue
        if day_target:
            days = {
                fold(part.strip().partition(" ")[0])
                for part in str(row[5] or "").split("|")
            }
            if day_target not in days:
                continue
        matched.append(row)

    # The index holds every branch of the term, so "no section anywhere has an
    # enrolled student" means the term's enrolment was never captured.
    captured = any_enrolled or not usable
    return {
        "term_section_count": usable,
        "match_count": len(matched),
        "enrolment_not_captured": not captured,
        "sections": [summarize_search_row(row, enrolment_captured=captured) for row in matched[:limit]],
        "truncated": len(matched) > limit,
    }


def summarize_exam(exam: dict[str, Any]) -> dict[str, Any]:
    """Rename one exam record's fields to match the other archive tools."""
    return {
        "crn": exam.get("crn"),
        "course_code": exam.get("code"),
        "course_name": exam.get("name"),
        "branch": exam.get("branch"),
        "instructor": clean_placeholder(exam.get("instructor")),
        "exam_type": clean_placeholder(exam.get("type")),
        "date": clean_placeholder(exam.get("date")),
        "day": clean_placeholder(exam.get("day")),
        "time": clean_placeholder(exam.get("time")),
        "place": clean_placeholder(exam.get("place")),
    }


def filter_exams(
    exams: Any,
    *,
    branch: str | None = None,
    course_code: str | None = None,
) -> list[dict[str, Any]]:
    """Keep exam records for one branch and/or one exact course code."""
    branch_target = (branch or "").strip().upper()
    code_target = _code_key(course_code) if course_code else ""
    out = []
    for exam in exams or []:
        if not isinstance(exam, dict):
            continue
        if branch_target and str(exam.get("branch") or "").strip().upper() != branch_target:
            continue
        if code_target and _code_key(exam.get("code")) != code_target:
            continue
        out.append(summarize_exam(exam))
    return out


def _parse_utc_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# -- which study levels a term dump covers ------------------------------------

# A regular term whose section count is this many times the median of the
# other terms of the same season (or that fraction of it) probably does not
# cover the same study levels. Summer school is left out: its size really
# does change from year to year.
SECTION_COUNT_OUTLIER_RATIO = 1.4


def term_levels(meta: Any, sections: Any = ()) -> list[str]:
    """Study levels (``LS``, ``LU``, ...) one term's dump holds.

    Read from the per-branch ``levels`` of ``meta.json``, which covers the
    whole term; the levels of the loaded sections are the fallback. An empty
    list means the level is not recorded, not that the term has none.
    """
    levels: set[str] = set()
    for branch in (meta.get("branches") if isinstance(meta, dict) else None) or []:
        if isinstance(branch, dict):
            levels.update(str(level) for level in clean_list(branch.get("levels")) or [])
    if not levels:
        for section in sections or ():
            level = clean_placeholder(section.get("level")) if isinstance(section, dict) else None
            if level:
                levels.add(str(level))
    return sorted(levels)


def section_count_outliers(index_terms: Any) -> list[dict[str, Any]]:
    """Regular terms whose section count is far from their season's median.

    ``index.json`` records no level per term, so this is only a hint that a
    term may cover other study levels than its neighbours, never a finding.
    """
    by_season: dict[str, list[tuple[str, float]]] = {}
    for entry in index_terms or []:
        if not isinstance(entry, dict) or entry.get("missing"):
            continue
        slug, count = str(entry.get("slug") or ""), entry.get("sections")
        season = slug.rsplit("-", 1)[-1]
        if season in ("guz", "bahar") and _is_count(count) and count > 0:
            by_season.setdefault(season, []).append((slug, count))
    outliers = []
    for terms in by_season.values():
        for slug, count in terms:
            others = sorted(other for other_slug, other in terms if other_slug != slug)
            if len(others) < 2:
                continue
            middle = len(others) // 2
            median = others[middle] if len(others) % 2 else (others[middle - 1] + others[middle]) / 2
            ratio = count / median
            if ratio >= SECTION_COUNT_OUTLIER_RATIO or ratio <= 1 / SECTION_COUNT_OUTLIER_RATIO:
                outliers.append({
                    "term": slug,
                    "sections": count,
                    "same_season_median": median,
                    "ratio": round(ratio, 2),
                })
    return sorted(outliers, key=lambda item: _term_sort_key(item["term"]), reverse=True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# A scan newer than this is read as "the scraper is running". The archive
# scans at least daily while it scans at all, so two days leaves room for one
# missed run without calling a paused scraper active.
SCAN_ACTIVE_WINDOW_HOURS = 48


def _age_text(hours: float) -> str:
    if hours < SCAN_ACTIVE_WINDOW_HOURS:
        return f"yaklaşık {max(0, round(hours))} saat"
    return f"yaklaşık {round(hours / 24)} gün"


def snapshot_freshness(
    last_snapshot: Any,
    *,
    scraped_at: Any = None,
    is_current_term: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """How old a quota series is and whether the archive is still scanning.

    Nothing here assumes a refresh cadence. ``last_snapshot`` is the last
    entry of the term's quota series and ``scraped_at`` the archive's own last
    scrape time (``index.json``); the newer of the two decides ``scan_active``,
    which is ``None`` when neither is known. The archive pauses outside
    registration and add/drop weeks, so an old snapshot is expected, and the
    note says how old it is instead of promising a daily refresh.
    """
    moment = now or _utc_now()
    last = _parse_utc_timestamp(last_snapshot)
    scraped = _parse_utc_timestamp(scraped_at)
    known = [stamp for stamp in (last, scraped) if stamp is not None]

    def hours_since(stamp: datetime) -> float:
        return (moment - stamp).total_seconds() / 3600

    age_hours = hours_since(last) if last is not None else None
    newest_hours = hours_since(max(known)) if known else None
    scan_active: bool | None
    if not is_current_term:
        scan_active = False
    elif newest_hours is None:
        scan_active = None
    else:
        scan_active = newest_hours <= SCAN_ACTIVE_WINDOW_HOURS

    if age_hours is None:
        parts = ["Son kontenjan görüntüsünün zamanı kayıtlı değil."]
    else:
        parts = [f"Son kontenjan görüntüsü {_age_text(age_hours)} önce alındı ({last_snapshot})."]
    if not is_current_term:
        parts.append("Bu dönem arşivin şu an taradığı dönem değil; seri bu noktada bitiyor.")
    elif scan_active is None:
        parts.append("Taramanın şu an etkin olup olmadığı bilinmiyor.")
    elif scan_active:
        parts.append(
            f"Arşivin son taraması {SCAN_ACTIVE_WINDOW_HOURS} saatten yeni; tarama etkin görünüyor."
        )
    else:
        parts.append(
            f"Arşivin bilinen son taraması {_age_text(newest_hours or 0.0)} önce; tarama şu an etkin "
            "görünmüyor (arşiv kayıt ve ekle-bırak haftaları dışında taramayı durdurur)."
        )
    parts.append("Sayılar o görüntüdeki durumdur, anlık değildir; güncel kontenjan için OBS'ye bakın.")
    return {
        "last_snapshot_age_days": round(age_hours / 24, 1) if age_hours is not None else None,
        "scan_active": scan_active,
        "note": " ".join(parts),
    }


def status_summary(status: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Shape ``status.json``. Purely descriptive: an old date is not a fault.

    The archive stops scraping outside registration and add/drop weeks, so
    ``data_age_days`` is reported as a plain number with no threshold attached.
    """
    last_success = _parse_utc_timestamp(status.get("lastSuccessAt"))
    age_days = None
    if last_success is not None:
        moment = now or datetime.now(timezone.utc)
        age_days = round((moment - last_success).total_seconds() / 86400, 1)
    failed = status.get("failedBranches")
    return {
        "last_run_at": status.get("lastRunAt"),
        "last_success_at": status.get("lastSuccessAt"),
        "data_age_days": age_days,
        "sections": status.get("sections"),
        "previous_sections": status.get("prevSections"),
        "partial": status.get("partial"),
        "failed_branches": failed if isinstance(failed, list) else [],
        "mode": status.get("mode"),
        "duration_sec": status.get("durationSec"),
        "schema_version": status.get("schemaVersion"),
        "sources": status.get("sources"),
    }
