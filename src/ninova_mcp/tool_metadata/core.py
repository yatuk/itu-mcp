"""Tool metadata: login and session diagnostics."""

from __future__ import annotations

from typing import Any

CORE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "auth_status",
        "title": "Authentication Status",
        "description": "Check whether Ninova credentials are configured and whether a fresh session can be created.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "refresh_session",
        "title": "Refresh Ninova Session",
        "description": "Force a new login with NINOVA_USERNAME and NINOVA_PASSWORD.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "obs_auth_status",
        "title": "OBS Auth Status",
        "description": "Check whether an OBS JWT can be obtained with the configured İTÜ credentials.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]
