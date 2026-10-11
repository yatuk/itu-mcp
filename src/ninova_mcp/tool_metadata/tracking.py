"""Tool metadata: Ninova change tracking, updates and deadlines."""

from __future__ import annotations

from typing import Any

TRACKING_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_course_overview",
        "title": "Get Course Overview",
        "description": (
            "Return a combined view of a course's sections, announcements, assignments, files, grades, "
            "message board, attendance, and remote learning routes. Without refresh=true a course that "
            "was synced before is answered from the stored snapshot (source: tracked_state). "
            "The result carries snapshot_age_hours and stale: stale=true means the stored data is older than the limit (12 hours by default, NINOVA_TRACKING_MAX_AGE_SECONDS) and must not be presented as the current state. Call again with refresh=true for live data."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": ["string", "integer"],
                    "description": "Course code, title, course path, or full course URL.",
                },
                "refresh": {
                    "type": "boolean",
                    "default": False,
                    "description": "Fetch live data instead of using the stored tracking state when available.",
                },
                "file_max_depth": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 8,
                    "default": 3,
                },
                "include_assignment_details": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "When true (and refresh is needed), fetch each assignment detail page. "
                        "Default false for a faster overview."
                    ),
                },
                "compact": {
                    "type": "boolean",
                    "default": False,
                    "description": "Shrink large fields for smaller LLM context.",
                },
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sync_all_courses",
        "title": "Sync All Courses",
        "description": (
            "Fetch all visible courses, store a tracking snapshot, and return newly "
            "detected changes since the previous sync. Assignment detail pages are "
            "skipped by default for speed. change_summary is the short readable version of the "
            "changes (Turkish lines grouped by course); updates keeps the full records. What was "
            "read is also indexed for search_content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_files": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include class and lesson file inventories in the sync.",
                },
                "file_max_depth": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 8,
                    "default": 3,
                },
                "course_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Optional limit for how many courses to sync.",
                },
                "include_assignment_details": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "When true, fetch each assignment detail/upload page during sync. "
                        "Much slower; list-page dates and counts are usually enough for deadlines."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_updates",
        "title": "Get Tracked Updates",
        "description": (
            "Read the stored Ninova tracking history and return the changes detected by past syncs. "
            "It never contacts Ninova, so nothing newer than last_sync_at is listed. "
            "The result carries snapshot_age_hours and stale: stale=true means the stored data is older than the limit (12 hours by default, NINOVA_TRACKING_MAX_AGE_SECONDS) and must not be presented as the current state. status is ok, stale_snapshot or no_snapshot; when it is not ok an "
            "empty list does not mean nothing changed: run sync_all_courses first. count is the "
            "number returned, total_count the number matching before the limit. With summary=true "
            "the raw list is replaced by change_summary: a headline and short Turkish lines grouped "
            "by course, for 'what changed since Monday' questions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 100,
                },
                "course": {
                    "type": ["string", "integer"],
                    "description": "Optional course code, title, path, or full course URL filter.",
                },
                "entity_type": {
                    "type": "string",
                    "description": "Optional entity type filter such as assignments, announcements, grades, or message_topics.",
                },
                "summary": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Return change_summary (headline and short lines grouped by course) instead of "
                        "the raw updates list. It covers every matching change, not only the first limit."
                    ),
                },
                "since": {
                    "type": "string",
                    "description": (
                        "Only changes detected at or after this ISO date or date-time, e.g. 2026-10-05 "
                        "or 2026-10-05T08:00:00+03:00. A value without a zone is read as UTC."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_upcoming_deadlines",
        "title": "Get Upcoming Deadlines",
        "description": (
            "Return assignments whose submission deadline is approaching, from the stored tracking "
            "snapshot. A snapshot that is missing, older than the staleness limit (12 hours by default, "
            "NINOVA_TRACKING_MAX_AGE_SECONDS) or about other courses than the current course list is "
            "refreshed automatically first (refreshed=true, details in refresh). Always read status "
            "before answering: ok means the list is current and complete; stale_snapshot or no_snapshot "
            "(stale=true) means the refresh failed or was skipped and the list is old; "
            "incomplete_snapshot means some assignment list could not be read. Unless status is ok, an "
            "empty list does NOT mean nothing is due: say the deadlines could not be verified, or call "
            "again with refresh=true. snapshot_age_hours is the age of the data and courses_covered "
            "names the courses the answer is about."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 180,
                    "default": 14,
                },
                "refresh": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Run a full sync before computing deadlines, even when the snapshot is recent. "
                        "A stale snapshot is refreshed without this flag."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
]
