from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


TRACKING_STATE_VERSION = 2
MAX_UPDATE_HISTORY = 2000

# Metadata follows the fields in overview. Different entity types may share
# one read, for example the active/past session lists.
ENTITY_SCOPES = {
    "announcements": "announcements", "assignments": "assignments",
    "class_files": "class_files", "lesson_files": "lesson_files",
    "grades": "grades", "message_topics": "message_board",
    "attendance_weeks": "attendance", "active_remote_sessions": "remote_learning",
    "past_remote_sessions": "remote_learning", "course_info": "info",
}
SNAPSHOT_SCOPES = tuple(dict.fromkeys((*ENTITY_SCOPES.values(), "sections")))


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

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A write killed mid-flight (crash, kill -9) can leave a truncated
        # file. Treating that the same as "no tracking state yet" matches
        # session_store.load_session's handling of the same failure mode,
        # and is safer than hard-failing every subsequent sync/get_updates
        # call until someone manually deletes the file.
        return {
            "version": TRACKING_STATE_VERSION,
            "last_sync_at": None,
            "courses": {},
            "updates": [],
        }
    return {
        "version": document.get("version", TRACKING_STATE_VERSION),
        "last_sync_at": document.get("last_sync_at"),
        "courses": document.get("courses", {}),
        "updates": document.get("updates", []),
        "enrollment_coverage": document.get("enrollment_coverage", {"status": "unknown"}),
    }


def save_tracking_state(path: Path, state: dict[str, Any]) -> None:
    # Write-then-rename so a crash mid-write leaves the old file intact
    # instead of a truncated one load_tracking_state would otherwise choke
    # on — same pattern as session_store.save_session.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


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


def scope_observation(snapshot: dict[str, Any], scope: str) -> dict[str, Any]:
    """Missing metadata is unknown, including snapshots written by version 1."""
    return (snapshot.get("coverage") or {}).get(scope) or {"status": "unknown", "reason": "legacy_or_missing_coverage"}


def has_complete_baseline(observation: dict[str, Any]) -> bool:
    return observation.get("status") == "complete" or observation.get("baseline_complete") is True or bool(observation.get("last_complete_at"))


def merge_course_snapshot(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Keep last-known data whenever the corresponding current read is incomplete.

    The latest attempt's status remains visible alongside last_complete_at. A
    first complete observation after an unknown/legacy baseline is accepted
    silently by diff_course_snapshots; it does not claim historical changes.
    """
    merged = copy.deepcopy(current)
    merged["coverage"] = {}
    for scope in SNAPSHOT_SCOPES:
        observation = copy.deepcopy(scope_observation(current, scope))
        if observation["status"] == "complete":
            observation["baseline_complete"] = True
            observation["last_complete_at"] = current.get("captured_at") or utc_now_iso()
            if scope == "assignments" and previous is not None and observation.get("details_included") is False:
                old_items = {item.get("url"): item for item in previous.get("overview", {}).get("assignments", []) if item.get("url")}
                for item in merged["overview"].get("assignments", []):
                    old_item = old_items.get(item.get("url"), {})
                    for key in ("description", "source_files", "required_files", "upload_items", "upload_url"):
                        if item.get(key) is None and old_item.get(key) is not None:
                            item[key] = copy.deepcopy(old_item[key])
                            observation["details_retained"] = True
                if observation.get("details_retained"):
                    old = scope_observation(previous, scope)
                    observation["details_last_observed_at"] = old.get("details_last_observed_at") or previous.get("captured_at")
            elif scope == "assignments" and observation.get("details_included"):
                observation["details_last_observed_at"] = current.get("captured_at")
        elif previous is not None:
            old = scope_observation(previous, scope)
            if scope in previous.get("overview", {}):
                merged["overview"][scope] = copy.deepcopy(previous["overview"][scope])
                observation["data_retained"] = True
            if has_complete_baseline(old):
                observation["baseline_complete"] = True
                observation["last_complete_at"] = old.get("last_complete_at") or previous.get("captured_at")
        merged["coverage"][scope] = observation
    merged["snapshot_complete"] = all(item["status"] == "complete" for item in merged["coverage"].values())
    return merged


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
        scope = ENTITY_SCOPES[entity_type]
        if scope_observation(current_snapshot, scope)["status"] != "complete":
            continue
        if not has_complete_baseline(scope_observation(previous_snapshot, scope)):
            # Version 1 could have stored empty defaults after failed reads.
            # Re-observing those records must not generate a false re-add.
            continue
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
