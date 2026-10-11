"""Tool metadata: campus life."""

from __future__ import annotations

from typing import Any

CAMPUS_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_itu_directory",
        "title": "Search İTÜ Directory",
        "description": "Search the official public İTÜ directory using its CSRF-protected form.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string", "minLength": 3},
                "last_name": {"type": "string", "minLength": 2},
                "identity_type": {"type": "string", "enum": ["all", "administrative", "academic", "student"], "default": "all"},
                "include_details": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
            },
            "required": ["first_name", "last_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_shuttle_schedule",
        "title": "İTÜ Shuttle Schedule",
        "description": "Read official SKS shuttle/ring timetables and stop lists (public).",
        "inputSchema": {
            "type": "object",
            "properties": {"route": {"type": "string"}, "day_type": {"type": "string", "description": "Optional page text filter, e.g. hafta içi/hafta sonu."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "search_campus_locations",
        "title": "Search Campus Buildings",
        "description": "Search official OBS building codes and names (public).",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "additionalProperties": False},
    },
    {
        "name": "get_sports_facility_hours",
        "title": "Sports Facility Hours",
        "description": "Read official weekday/weekend opening hours for İTÜ sports facilities (public).",
        "inputSchema": {"type": "object", "properties": {"facility": {"type": "string"}}, "additionalProperties": False},
    },
    {
        "name": "get_itu_announcements",
        "title": "İTÜ Announcements",
        "description": "Aggregate official announcements from İTÜ, ÖDEK, İKM, SKS and Erasmus sources.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sources": {"type": "array", "items": {"type": "string", "enum": ["itu", "odek", "ikm", "sks", "erasmus"]}, "uniqueItems": True},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
            },
            "additionalProperties": False,
        },
    },
]
