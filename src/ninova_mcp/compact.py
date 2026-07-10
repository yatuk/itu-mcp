"""Shrink large tool payloads so LLM context is not flooded."""

from __future__ import annotations

import copy
import os
from typing import Any


DEFAULT_COMPACT_LIST_LIMIT = 20
DEFAULT_COMPACT_TEXT_CHARS = 800
DEFAULT_COMPACT_LINK_LIMIT = 30

# Keys that are usually verbose and safe to drop or shorten in compact mode.
_DROP_KEYS = {
    "links",
    "text",
    "text_excerpt",
    "text_diff_preview",
    "before",
    "after",
    "pages",
    "body_text",
    "description",
    "upload_items",
    "source_files",
    "required_files",
    "thread",
    "weekly_plan",
    "course_details",
}

_SHORTEN_KEYS = {
    "summary",
    "context",
    "body_text",
    "description",
    "text_excerpt",
    "last_message_text",
    "status_text",
}


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def compact_default_enabled() -> bool:
    return env_flag("NINOVA_COMPACT_DEFAULT", default=False)


def _truncate_str(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


def compact_value(
    value: Any,
    *,
    list_limit: int = DEFAULT_COMPACT_LIST_LIMIT,
    text_chars: int = DEFAULT_COMPACT_TEXT_CHARS,
    link_limit: int = DEFAULT_COMPACT_LINK_LIMIT,
    depth: int = 0,
) -> Any:
    if depth > 8:
        return value

    if isinstance(value, str):
        return _truncate_str(value, text_chars)

    if isinstance(value, list):
        items = value[: max(0, list_limit)]
        compacted = [
            compact_value(item, list_limit=list_limit, text_chars=text_chars, link_limit=link_limit, depth=depth + 1)
            for item in items
        ]
        if len(value) > list_limit:
            return compacted + [{"_omitted": len(value) - list_limit}]
        return compacted

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in _DROP_KEYS and depth > 0:
                # Keep counts instead of huge nested blobs when possible.
                if isinstance(item, list):
                    result[f"{key}_count"] = len(item)
                    continue
                if isinstance(item, str) and len(item) > text_chars:
                    result[key] = _truncate_str(item, text_chars)
                    continue
                if key in {"before", "after", "thread", "pages", "links", "text", "text_diff_preview"}:
                    continue
            if key == "links" and isinstance(item, list):
                result[key] = compact_value(
                    item[:link_limit],
                    list_limit=list_limit,
                    text_chars=text_chars,
                    link_limit=link_limit,
                    depth=depth + 1,
                )
                if len(item) > link_limit:
                    result["links_omitted"] = len(item) - link_limit
                continue
            if key in _SHORTEN_KEYS and isinstance(item, str):
                result[key] = _truncate_str(item, text_chars)
                continue
            result[key] = compact_value(
                item,
                list_limit=list_limit,
                text_chars=text_chars,
                link_limit=link_limit,
                depth=depth + 1,
            )
        return result

    return value


def maybe_compact(
    payload: dict[str, Any],
    *,
    compact: bool | None,
    list_limit: int = DEFAULT_COMPACT_LIST_LIMIT,
    text_chars: int = DEFAULT_COMPACT_TEXT_CHARS,
) -> dict[str, Any]:
    enabled = compact_default_enabled() if compact is None else bool(compact)
    if not enabled:
        return payload
    result = compact_value(copy.deepcopy(payload), list_limit=list_limit, text_chars=text_chars)
    if isinstance(result, dict):
        result["compact"] = True
        return result
    return {"data": result, "compact": True}
