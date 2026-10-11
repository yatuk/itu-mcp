"""Tool metadata: İTÜ Portal."""

from __future__ import annotations

from typing import Any

PORTAL_TOOLS: list[dict[str, Any]] = [
    {
        "name": "obs_get_campus_card",
        "title": "OBS Campus Card",
        "description": "Read campus card balance and recent transactions from OBS (requires login).",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_cafeteria_menu",
        "title": "Cafeteria Menu",
        "description": "Read a dated lunch/dinner or vegan menu from the İTÜ Portal (requires login), including calories and allergens.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD or DD.MM.YYYY; default today."},
                "meal": {"type": "string", "description": "lunch/öğle or dinner/akşam."},
                "vegan": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_notifications",
        "title": "Portal Notifications",
        "description": "Read İTÜ Portal notifications (requires login). status is \"ok\" or \"unavailable\". When the read failed, count and notifications are null and reason says why: that is not an empty inbox. api_fallback=true marks rows taken from the Portal homepage markup instead of the notification service.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "notification_id": {"type": "string", "description": "Optional id for full notification detail."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_help_tickets",
        "title": "Portal Help Tickets",
        "description": "List the user's help tickets. Search by ID, title or status. Returns id, title, unit, category, status, created_at, updated_at, url and has_reply. Selected rows are enriched from the help desk by default. Set include_details=false for a quick Portal-only read. Unavailable fields are null with missing_fields. Requires login.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Match ticket ID, title or status."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "include_details": {"type": "boolean", "default": False, "description": "Also open each selected ticket page for unit, category and dates. Off by default because it costs one request per ticket."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_help_ticket",
        "title": "Help Ticket Detail",
        "description": "Read one help ticket by numeric ID: title, description, unit, category, subcategory, status, dates, messages, institutional replies and attachment links. Does not download attachments. Reply detection compares history authors with the original requester. Missing fields are null. Dates are local to the source with no assumed timezone. Requires login.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "pattern": "^[0-9]{1,20}$", "description": "Numeric ID of one of your own tickets, from obs_get_help_tickets."},
                "include_sensitive": {"type": "boolean", "default": False, "description": "Show the real names of the requester and staff instead of role labels. Ignored unless the operator set NINOVA_ALLOW_SENSITIVE_OUTPUT."},
            },
            "required": ["ticket_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_cloud_quota",
        "title": "Cloud & Mail Quota",
        "description": "Read İTÜ Mail and İTÜ Bulut storage quota from the Portal (requires login).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]
