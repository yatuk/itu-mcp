"""Tool metadata: Ninova change tracking, updates and deadlines."""

from __future__ import annotations

from typing import Any

TRACKING_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_course_overview",
        "title": "Get Course Overview",
        "description": "Return a combined view of a course's sections, announcements, assignments, files, grades, message board, attendance, and remote learning routes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
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
            "skipped by default for speed."
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
        "description": "Read the stored Ninova tracking history and return recent detected changes.",
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
                    "type": "string",
                    "description": "Optional course code, title, path, or full course URL filter.",
                },
                "entity_type": {
                    "type": "string",
                    "description": "Optional entity type filter such as assignments, announcements, grades, or message_topics.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_upcoming_deadlines",
        "title": "Get Upcoming Deadlines",
        "description": "Return assignments whose submission deadline is approaching based on the stored tracking snapshot.",
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
                    "description": "Run a sync before computing deadlines.",
                },
            },
            "additionalProperties": False,
        },
    },
]
