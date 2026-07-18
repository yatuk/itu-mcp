from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


TRACKING_STATE_VERSION = 1
MAX_UPDATE_HISTORY = 2000


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def load_tracking_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": TRACKING_STATE_VERSION,
            "last_sync_at": None,
            "courses": {},
            "updates": [],
        }

    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        "version": document.get("version", TRACKING_STATE_VERSION),
        "last_sync_at": document.get("last_sync_at"),
        "courses": document.get("courses", {}),
        "updates": document.get("updates", []),
    }


def save_tracking_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _make_update_id(
    *,
    course_url: str,
    entity_type: str,
    action: str,
    entity_id: str,
    before: Any,
    after: Any,
) -> str:
    digest = hashlib.sha1(
        "|".join(
            [
                course_url,
                entity_type,
                action,
                entity_id,
                _stable_json(before),
                _stable_json(after),
            ]
        ).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()
    return digest[:16]


def _entity_summary(entity_type: str, action: str, payload: dict[str, Any]) -> str:
    label = (
        payload.get("title")
        or payload.get("name")
        or payload.get("week")
        or payload.get("path")
        or payload.get("description")
        or payload.get("url")
        or entity_type
    )
    return f"{entity_type}:{action}:{label}"


def _normalize_collection(items: list[dict[str, Any]], *, key_fields: list[str]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items, start=1):
        entity_id = None
        for key in key_fields:
            candidate = item.get(key)
            if isinstance(candidate, str) and candidate.strip():
                entity_id = candidate.strip()
                break
        if entity_id is None:
            entity_id = f"item-{index}"
        normalized[entity_id] = item
    return normalized


def snapshot_entities(snapshot: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    overview = snapshot["overview"]
    return {
        "announcements": _normalize_collection(overview["announcements"], key_fields=["url", "title"]),
        "assignments": _normalize_collection(overview["assignments"], key_fields=["url", "title"]),
        "class_files": _normalize_collection(overview["class_files"], key_fields=["url", "path", "name"]),
        "lesson_files": _normalize_collection(overview["lesson_files"], key_fields=["url", "path", "name"]),
        "grades": _normalize_collection(overview["grades"]["grades"], key_fields=["title"]),
        "message_topics": _normalize_collection(overview["message_board"]["topics"], key_fields=["url", "title"]),
        "attendance_weeks": _normalize_collection(overview["attendance"]["weeks"], key_fields=["week"]),
        "active_remote_sessions": _normalize_collection(
            overview["remote_learning"]["active_sessions"],
            key_fields=["url", "Ad", "Başlık", "text"],
        ),
        "past_remote_sessions": _normalize_collection(
            overview["remote_learning"]["past_sessions"],
            key_fields=["url", "Ad", "Başlık", "text"],
        ),
        "course_info": {
            "course_info": {
                "identity": overview["info"].get("identity"),
                "class_meta": overview["info"].get("class_meta"),
                "course_details": overview["info"].get("course_details"),
                "weekly_schedule": overview["info"].get("weekly_schedule"),
            }
        },
    }


def diff_course_snapshots(
    *,
    course: dict[str, Any],
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
    detected_at: str | None = None,
) -> list[dict[str, Any]]:
    if previous_snapshot is None:
        return []

    previous_entities = snapshot_entities(previous_snapshot)
    current_entities = snapshot_entities(current_snapshot)
    when = detected_at or utc_now_iso()
    updates: list[dict[str, Any]] = []

    for entity_type, current_items in current_entities.items():
        previous_items = previous_entities.get(entity_type, {})

        for entity_id, current_payload in current_items.items():
            if entity_id not in previous_items:
                action = "added"
                before = None
            else:
                before = previous_items[entity_id]
                if _stable_json(before) == _stable_json(current_payload):
                    continue
                action = "changed"

            updates.append(
                {
                    "id": _make_update_id(
                        course_url=course["url"],
                        entity_type=entity_type,
                        action=action,
                        entity_id=entity_id,
                        before=before,
                        after=current_payload,
                    ),
                    "detected_at": when,
                    "course": course,
                    "entity_type": entity_type,
                    "action": action,
                    "entity_id": entity_id,
                    "summary": _entity_summary(entity_type, action, current_payload),
                    "before": before,
                    "after": current_payload,
                }
            )

        for entity_id, previous_payload in previous_items.items():
            if entity_id in current_items:
                continue
            updates.append(
                {
                    "id": _make_update_id(
                        course_url=course["url"],
                        entity_type=entity_type,
                        action="removed",
                        entity_id=entity_id,
                        before=previous_payload,
                        after=None,
                    ),
                    "detected_at": when,
                    "course": course,
                    "entity_type": entity_type,
                    "action": "removed",
                    "entity_id": entity_id,
                    "summary": _entity_summary(entity_type, "removed", previous_payload),
                    "before": previous_payload,
                    "after": None,
                }
            )

    return updates


def merge_updates(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {item["id"] for item in existing if "id" in item}
    merged = list(existing)
    for update in incoming:
        if update["id"] in seen:
            continue
        seen.add(update["id"])
        merged.append(update)
    merged.sort(key=lambda item: item.get("detected_at") or "", reverse=True)
    return merged[:MAX_UPDATE_HISTORY]
