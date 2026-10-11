"""Tool metadata: İTÜ Library."""

from __future__ import annotations

from typing import Any

LIBRARY_TOOLS: list[dict[str, Any]] = [
    {
        "name": "library_search",
        "title": "Search İTÜ Library",
        "description": "Search the current public İTÜ Sirsi catalog. Reuse next_offset for pagination. Each record lists its ISBNs one per entry. Public network access is required; no Ninova credentials are used.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2},
                "search_type": {"type": "string", "enum": ["keyword", "title", "author", "subject", "call_number", "isbn"], "default": "keyword"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                "offset": {"type": "integer", "minimum": 0, "maximum": 10000, "default": 0},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "library_get_item",
        "title": "İTÜ Library Item",
        "description": "Read a public Sirsi record and copy list using the SD_ILS: ID returned by library_search. fields.author is the record's main author; when the record has none (works catalogued under a title or an institution) it falls back to the corporate or added authors and fields.author_source names the field used. Copy status loaded asynchronously may be unknown.",
        "inputSchema": {"type": "object", "properties": {"record_id": {"type": "string", "pattern": "^(SD_ILS:[0-9]{1,12}|b[0-9]{5,12})$"}}, "required": ["record_id"], "additionalProperties": False},
    },
    {
        "name": "library_check_availability",
        "title": "İTÜ Library Availability",
        "description": "Read public copy status. Returns available=null when the catalog requires an asynchronous status lookup; verify current shelf availability in the official catalog.",
        "inputSchema": {"type": "object", "properties": {"record_id": {"type": "string", "pattern": "^(SD_ILS:[0-9]{1,12}|b[0-9]{5,12})$"}}, "required": ["record_id"], "additionalProperties": False},
    },
    {
        "name": "library_get_account",
        "title": "İTÜ Library Account",
        "description": "Legacy library account reader. Unavailable on the current Sirsi platform; use the official catalog account page.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "library_list_loans",
        "title": "İTÜ Library Loans",
        "description": "Legacy library loan reader. Unavailable on the current Sirsi platform; use the official catalog account page.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "library_renew_loan",
        "title": "Renew İTÜ Library Loan",
        "description": "Legacy library renewal tool. Unavailable on the current Sirsi platform, including with confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {"loan_id": {"type": "string"}, "confirm": {"type": "boolean", "default": False}},
            "required": ["loan_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "library_reserve_item",
        "title": "Reserve İTÜ Library Item",
        "description": "Legacy library hold tool. Unavailable on the current Sirsi platform, including with confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string", "pattern": "^(SD_ILS:[0-9]{1,12}|b[0-9]{5,12})$"},
                "pickup_location": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["record_id"],
            "additionalProperties": False,
        },
    },
]
