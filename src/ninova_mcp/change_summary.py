"""Turn tracking change records into a short Turkish summary grouped by course.

The input is the list produced by ``tracking.diff_course_snapshots`` (and the
course added/removed records of ``sync_all_courses``). Nothing is fetched and
nothing is stored: this is a pure function over those records.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

MAX_COURSES = 10
MAX_LINES_PER_COURSE = 8

FILE_ENTITY_TYPES = frozenset({"class_files", "lesson_files"})
# Line types in the order they are shown inside a course: what has a deadline
# or a grade first, inventories last.
LINE_ORDER = ("deadline_changed", "assignment", "grade", "announcement", "course", "message_topic", "file")
_COUNT_LABELS = (
    ("announcement", "duyuru"),
    ("file", "dosya"),
    ("assignment", "ödev"),
    ("grade", "not"),
    ("course", "ders"),
    ("message_topic", "pano konusu"),
    ("other", "diğer"),
    ("removed", "silinen"),
)


def parse_since(value: Any) -> datetime | None:
    """Parse an ISO date or date-time; a value without a zone is read as UTC."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"since must be an ISO date or date-time such as 2026-10-01 or 2026-10-01T08:00:00+03:00, got {text!r}."
        ) from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def filter_since(updates: list[dict[str, Any]], since: Any) -> list[dict[str, Any]]:
    """Records detected at or after ``since``; a record without a usable time is dropped."""
    limit = parse_since(since)
    if limit is None:
        return list(updates)
    kept = []
    for item in updates:
        try:
            when = parse_since(item.get("detected_at"))
        except ValueError:
            when = None
        if when is not None and when >= limit:
            kept.append(item)
    return kept


def _label(payload: Any, fallback: str = "(adsız)") -> str:
    if not isinstance(payload, dict):
        return fallback
    for key in ("title", "name", "code", "path"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _deadline(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    return payload.get("submission_end") or payload.get("submission_end_iso") or None


def _deadline_key(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    return payload.get("submission_end_iso") or payload.get("submission_end") or None


def _line(update: dict[str, Any]) -> tuple[str, str] | None:
    """``(type, text)`` of the line for one record, or ``None`` when it is only counted."""
    entity_type = update.get("entity_type")
    action = update.get("action")
    before, after = update.get("before"), update.get("after")
    if entity_type == "course":
        course = after if action == "added" else before
        name = " ".join(
            str(part) for part in ((course or {}).get("code"), (course or {}).get("title")) if part
        ) or _label(course)
        if action == "added":
            return "course", f"Yeni ders: {name}"
        if action == "removed":
            return "course", f"Ders listeden çıktı: {name}"
        return None
    if action == "removed":
        return None
    if entity_type == "announcements":
        return "announcement", (
            f"Yeni duyuru: {_label(after)}" if action == "added" else f"Duyuru güncellendi: {_label(after)}"
        )
    if entity_type in FILE_ENTITY_TYPES:
        if isinstance(after, dict) and after.get("entry_type") == "folder":
            return None
        return "file", (
            f"Yeni dosya: {_label(after)}" if action == "added" else f"Dosya güncellendi: {_label(after)}"
        )
    if entity_type == "assignments":
        title = _label(after)
        if action == "added":
            due = _deadline(after)
            return "assignment", f"Yeni ödev: {title} (son teslim {due})" if due else f"Yeni ödev: {title}"
        if _deadline_key(before) != _deadline_key(after):
            return "deadline_changed", (
                f"Teslim tarihi değişti: {title} ({_deadline(before) or 'belirsiz'} → {_deadline(after) or 'belirsiz'})"
            )
        return "assignment", f"Ödev güncellendi: {title}"
    if entity_type == "grades":
        title = _label(after)
        new = after.get("score") if isinstance(after, dict) else None
        if action == "added":
            return "grade", f"Not girildi: {title} ({new})" if new else f"Not girildi: {title}"
        old = before.get("score") if isinstance(before, dict) else None
        if old != new:
            return "grade", f"Not değişti: {title} ({old or 'yok'} → {new or 'yok'})"
        return "grade", f"Not değişti: {title}"
    if entity_type == "message_topics" and action == "added":
        return "message_topic", f"Yeni pano konusu: {_label(after)}"
    return None


def _count_key(line_type: str) -> str:
    return "assignment" if line_type == "deadline_changed" else line_type


def _headline(total: int, course_count: int, counts: dict[str, int]) -> str:
    if total == 0:
        return "Değişiklik yok."
    parts = [f"{counts[key]} {label}" for key, label in _COUNT_LABELS if counts.get(key)]
    return f"{course_count} derste {total} değişiklik: " + ", ".join(parts) + "."


def summarize_updates(
    updates: list[dict[str, Any]],
    since: Any = None,
    *,
    max_courses: int = MAX_COURSES,
    max_lines_per_course: int = MAX_LINES_PER_COURSE,
) -> dict[str, Any]:
    """Group change records by course into short Turkish lines.

    Removals, attendance, course information and remote session changes are
    only counted (``removed`` and ``other``). ``omitted_count`` is the number
    of lines that the two bounds left out.
    """
    records = filter_since(updates, since)
    groups: dict[str, dict[str, Any]] = {}
    totals: dict[str, int] = {}
    for update in records:
        course = update.get("course") or {}
        key = str(course.get("url") or course.get("code") or course.get("title") or "")
        group = groups.setdefault(key, {
            "course": {"code": course.get("code"), "title": course.get("title")},
            "lines": [], "counts": {}, "record_count": 0,
        })
        group["record_count"] += 1
        line = _line(update)
        if line is None:
            bucket = "removed" if update.get("action") == "removed" else "other"
        else:
            bucket = _count_key(line[0])
            after = update.get("after") if isinstance(update.get("after"), dict) else {}
            group["lines"].append({
                "type": line[0], "text": line[1],
                "date": update.get("detected_at"), "url": after.get("url"),
            })
        group["counts"][bucket] = group["counts"].get(bucket, 0) + 1
        totals[bucket] = totals.get(bucket, 0) + 1

    ordered = sorted(groups.values(), key=lambda item: -item["record_count"])
    omitted = 0
    courses: list[dict[str, Any]] = []
    for index, group in enumerate(ordered):
        if index >= max_courses:
            omitted += len(group["lines"])
            continue
        lines = sorted(group["lines"], key=lambda item: LINE_ORDER.index(item["type"]))
        omitted += max(0, len(lines) - max_lines_per_course)
        courses.append({
            "course": group["course"],
            "lines": lines[:max_lines_per_course],
            "counts": group["counts"],
        })

    stamps = sorted(str(item["detected_at"]) for item in records if item.get("detected_at"))
    return {
        "since": str(since).strip() if since is not None and str(since).strip() else (stamps[0] if stamps else None),
        "until": stamps[-1] if stamps else None,
        "headline": _headline(len(records), len(groups), totals),
        "total": len(records),
        "courses": courses,
        "omitted_count": omitted,
    }
