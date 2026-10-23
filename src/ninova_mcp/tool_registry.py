"""The full tool list and the name sets that classify tools.

``TOOLS`` is the metadata of every tool in publish order: the built-in
entries from ``tool_metadata`` followed by the features that ship their own
module. The sets below decide what the remote transport may expose and which
annotations a tool is registered with.
"""

from __future__ import annotations

from typing import Any

from .archive_tools import ARCHIVE_DATASET_TOOLS
from .calendar_export import CALENDAR_EXPORT_TOOLS
from .moodle_tools import MOODLE_TOOLS
from .seat_watch import SEAT_WATCH_STATEFUL_TOOL_NAMES, SEAT_WATCH_TOOLS
from .tool_metadata import BASE_TOOLS

TOOLS: list[dict[str, Any]] = list(BASE_TOOLS)
TOOLS.extend(ARCHIVE_DATASET_TOOLS)
TOOLS.extend(SEAT_WATCH_TOOLS)
TOOLS.extend(CALENDAR_EXPORT_TOOLS)
TOOLS.extend(MOODLE_TOOLS)
LOCAL_TOOL_NAMES: list[str] = [tool["name"] for tool in TOOLS]
MAIL_TOOL_NAMES = {
    "mail_status",
    "mail_list_inbox",
    "mail_list_sent",
    "mail_get_message",
    "mail_get_attachment",
}
OBS_REGISTRATION_WRITE_TOOL_NAMES = {"obs_save_registration_draft"}
REMOTE_EXCLUDED_TOOLS = {
    "download_resource",
    "snapshot_page",
    "diff_snapshot",
    "submit_assignment",
    "get_assignment_upload_slots",
    "read_resource_text",
    "moodle_read_resource",
    "read_page",
    "library_renew_loan",
    "library_reserve_item",
    # Writes to a caller-supplied output_dir on the host filesystem, same as
    # download_resource/snapshot_page above — belongs in the same exclusion
    # for the same reason.
    "obs_download_transcript",
    # Changes the student's saved registration draft. Like submit_assignment
    # it stays on the local stdio transport, whatever the opt-in flag says.
    "obs_save_registration_draft",
    # With save=true it writes a file on the host, like download_resource.
    "export_table",
}
REMOTE_TOOL_NAMES: list[str] = [
    name for name in LOCAL_TOOL_NAMES if name not in REMOTE_EXCLUDED_TOOLS
]
STATEFUL_TOOL_NAMES = {
    "refresh_session",
    "sync_all_courses",
    "get_upcoming_deadlines",
    "download_resource",
    "snapshot_page",
    "obs_download_transcript",
    "submit_assignment",
    "library_renew_loan",
    "library_reserve_item",
    "obs_save_registration_draft",
    # These POST to the OBS draft-check endpoint. Nothing is saved, but OBS
    # takes a short transaction lock and counts each call against a quota,
    # so they are not advertised as free, repeatable reads.
    "obs_validate_registration_plan",
    "obs_get_elective_group",
    # With save=true it creates a new local file on every call.
    "export_table",
}
STATEFUL_TOOL_NAMES |= SEAT_WATCH_STATEFUL_TOOL_NAMES
DESTRUCTIVE_TOOL_NAMES = {"submit_assignment", "obs_save_registration_draft"}
