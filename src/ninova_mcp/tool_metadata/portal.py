"""Tool metadata: İTÜ Portal."""

from __future__ import annotations

from typing import Any

PORTAL_TOOLS: list[dict[str, Any]] = [
    {
        "name": "obs_get_campus_card",
        "title": "OBS Campus Card",
        "description": "Read campus card balance and recent transactions from the İTÜ Portal (requires login). Each transaction has amount (the transaction) and balance_after (card balance after it).",
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
        "description": "Read İTÜ Portal notifications (requires login). By default the recent ones the Portal home page shows; include_all=true reads the whole list. Returns unread_count, total_count, scope (\"recent\" or \"all\") and per notification id, title, summary, created_at, age (relative time as the Portal prints it), is_read and unread. The body is not in the list: pass notification_id to get the content of that one notification. status is \"ok\" or \"unavailable\". When the read failed, count and notifications are null and reason says why: that is not an empty inbox. api_fallback=true marks rows taken from the Portal homepage markup instead of the notification service.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "notification_id": {"type": "string", "description": "Optional id of one notification (the id field of a listed row). Returns that notification with its content as plain text, cut at 8000 characters."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Maximum rows returned; 20 when omitted. A value larger than the recent list makes the tool read the whole list."},
                "include_all": {"type": "boolean", "default": False, "description": "Read every notification instead of only the recent ones shown on the Portal home page."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_help_tickets",
        "title": "Portal Help Tickets",
        "description": "List the user's help tickets. Search by ID, title or status. The default is a quick Portal list read: id, title, status and url, plus list_date, the one date the list carries (it does not say whether that is the creation or the last update). unit, category, created_at, updated_at and has_reply are not in the list view and are named in not_in_list_view. Set include_details=true to open each selected ticket page and fill them (one request per ticket), or call get_help_ticket for one ticket. missing_fields names fields that should have been present and were not. Requires login.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Match ticket ID, title or status."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "include_details": {"type": "boolean", "default": False, "description": "Also open each selected ticket page for unit, category, created_at, updated_at and has_reply. Off by default because it costs one request per ticket."},
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
        "description": "Read İTÜ Mail and İTÜ Bulut storage quota from the Portal (requires login). used and total are the Portal's own text; used_value/total_value are numbers with used_unit/total_unit. The mail quota arrives without a unit: assumed_unit and unit_note say so.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]
