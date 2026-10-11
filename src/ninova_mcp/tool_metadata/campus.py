"""Tool metadata: campus life."""

from __future__ import annotations

from typing import Any

CAMPUS_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_itu_directory",
        "title": "Search İTÜ Directory",
        "description": (
            "Search the official public İTÜ directory using its CSRF-protected form. identity_type "
            "is applied by the directory itself, so a person registered under another type is not "
            "returned: when a filtered search is empty (no_results=true), repeat it with 'all'."
        ),
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
        "description": (
            "Read the official SKS campus ring/shuttle timetables and stop lists of the Ayazağa "
            "campus (public): the in-campus ring, the staff housing (kamu konutları) service and "
            "the KYK Mühendishane dormitory service, weekday and weekend. 'validity_note' is the "
            "page's own sentence on the date the tables apply from."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"route": {"type": "string"}, "day_type": {"type": "string", "description": "Optional page text filter, e.g. hafta içi/hafta sonu."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "search_campus_locations",
        "title": "Search Campus Buildings",
        "description": (
            "Search the official OBS building code list used in course schedules (public). "
            "'locations' are buildings (code and name), 'sub_locations' are block or room codes "
            "inside a building (the source lists DZB only this way, without a building name), and "
            "'pseudo_entries' are rows that are not places (online teaching, to be announced). The "
            "list covers teaching buildings and sports areas only: no library, cafeteria or "
            "dormitory, no addresses or coordinates."
        ),
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Building code or part of a building name, case and accents ignored (e.g. 'BBB', 'bilgisayar')."}}, "additionalProperties": False},
    },
    {
        "name": "get_sports_facility_hours",
        "title": "Sports Facility Hours",
        "description": (
            "Read official weekday/weekend opening hours for İTÜ sports facilities (public). Each "
            "period has opens/closes as HH:MM and closed=true when the facility does not open in "
            "that period. A closure written into the facility name on the page, such as "
            "'(Pazartesi Kapalı)', is returned in closed_days and note."
        ),
        "inputSchema": {"type": "object", "properties": {"facility": {"type": "string"}}, "additionalProperties": False},
    },
    {
        "name": "get_itu_announcements",
        "title": "İTÜ Announcements",
        "description": (
            "Aggregate official announcements from İTÜ (www.itu.edu.tr), ÖDEK, İKM, SKS and Erasmus "
            "sources, newest first across sources. 'published_at' is the source's own text and "
            "'published_at_iso' the same date in ISO form; undated items come last. When limit is "
            "smaller than what the sources offer, every source first gets an equal share (see "
            "selection_note); ask for one source to read it in full. 'empty_sources' names sources "
            "that answered with no items, 'source_errors' those that failed. A summary cut short "
            "ends with '…' and has summary_truncated=true."
        ),
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
