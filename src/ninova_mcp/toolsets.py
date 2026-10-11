"""Named tool groups ("toolsets") that can be switched on and off.

The server registers every tool by default, and each tool's name, description
and schema is sent to the model at the start of a conversation. Two
environment variables let a user publish only the groups they need:

- ``NINOVA_TOOLSETS``: comma-separated allow-list of groups.
- ``NINOVA_DISABLED_TOOLSETS``: comma-separated groups to switch off.

With neither set, every group is on. The ``core`` group is always on so a
user can still diagnose their setup. Unknown group names fail at startup.

This module must not import ``server`` (``server`` imports it). The mapping
below is checked against ``server.TOOLS`` by ``tests/test_toolsets.py``: a
tool that is added without a group makes that test fail.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

from .env import load_ninova_env

ALLOW_ENV = "NINOVA_TOOLSETS"
DENY_ENV = "NINOVA_DISABLED_TOOLSETS"
CORE_TOOLSET = "core"


class ToolsetError(RuntimeError):
    """Raised when a toolset variable names a group that cannot be applied."""


# name -> (one-line description, tool names). Order is the display order.
TOOLSETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "core": (
        "Always on: login and session diagnostics",
        (
            "auth_status",
            "refresh_session",
            "obs_auth_status",
        ),
    ),
    "ninova": (
        "Ninova courses: announcements, files, assignments, grades, upload",
        (
            "get_dashboard",
            "list_courses",
            "get_course_announcements",
            "get_course_class_files",
            "get_course_lesson_files",
            "get_course_assignments",
            "get_course_info",
            "get_course_sections",
            "get_course_grades",
            "get_course_message_board",
            "get_course_attendance",
            "get_course_remote_learning",
            "get_course_overview",
            "get_dashboard_announcements",
            "get_dashboard_assignments",
            "get_assignment_upload_slots",
            "submit_assignment",
        ),
    ),
    "tracking": (
        "Ninova change tracking: sync, updates, upcoming deadlines",
        (
            "sync_all_courses",
            "get_updates",
            "get_upcoming_deadlines",
        ),
    ),
    "files": (
        "Ninova pages and files: read, crawl, download, text extraction, snapshots",
        (
            "read_page",
            "crawl_course",
            "download_resource",
            "read_resource_text",
            "snapshot_page",
            "diff_snapshot",
        ),
    ),
    "obs": (
        "OBS student record: profile, grades, schedule, attendance, transcript",
        (
            "obs_get_profile",
            "obs_list_programs",
            "obs_list_semesters",
            "obs_get_grade_distribution",
            "obs_get_advisor",
            "obs_get_internships",
            "obs_get_contacts",
            "obs_list_registered_courses",
            "obs_get_course_grades",
            "obs_get_attendance",
            "obs_get_schedule",
            "obs_get_graduation_remaining",
            "obs_download_transcript",
            "get_personal_exam_calendar",
        ),
    ),
    "registration": (
        "OBS course registration: status, draft, elective groups, plan check",
        (
            "obs_get_registration_status",
            "obs_get_registration_draft",
            "obs_save_registration_draft",
            "obs_get_elective_group",
            "obs_validate_registration_plan",
        ),
    ),
    "portal": (
        "Portal: campus card, notifications, help tickets, cloud and mail quota",
        (
            "obs_get_campus_card",
            "obs_get_notifications",
            "obs_get_help_tickets",
            "get_help_ticket",
            "obs_get_cloud_quota",
        ),
    ),
    "planning": (
        "Planning: GPA, relative grade, conflicts, degree plan, eligibility",
        (
            "obs_calculate_gpa",
            "calculate_target_gpa",
            "estimate_relative_grade",
            "check_course_conflicts",
            "list_degree_faculties",
            "list_degree_programs",
            "build_degree_plan",
            "explain_course_eligibility",
            "plan_remaining_courses",
        ),
    ),
    "public": (
        "Public OBS catalog: course search, schedules, prerequisites, exams, calendar",
        (
            "obs_search_courses",
            "obs_get_course_prerequisites",
            "get_academic_calendar",
            "get_public_course_schedule",
            "get_public_course_prerequisites",
            "get_public_exam_schedule",
            "find_open_course_sections",
            "find_empty_classrooms",
        ),
    ),
    "campus": (
        "Campus life: cafeteria, shuttle, directory, buildings, sports, announcements",
        (
            "get_cafeteria_menu",
            "search_itu_directory",
            "get_shuttle_schedule",
            "search_campus_locations",
            "get_sports_facility_hours",
            "get_itu_announcements",
        ),
    ),
    "library": (
        "Library: catalog search, availability, account, loans",
        (
            "library_search",
            "library_get_item",
            "library_check_availability",
            "library_get_account",
            "library_list_loans",
            "library_renew_loan",
            "library_reserve_item",
        ),
    ),
    "archive": (
        "Course archive since 2016: history, instructors, fill rates, grade distribution",
        (
            "archive_list_terms",
            "archive_course_history",
            "archive_who_taught",
            "archive_instructor_courses",
            "archive_term_sections",
            "archive_fill_rate",
            "archive_search_courses",
            "archive_list_branches",
            "archive_compare_terms",
            "archive_grade_distribution",
            "archive_course_catalog",
            "archive_course_unlocks",
            "archive_search_sections",
            "archive_exam_schedule",
            "archive_status",
        ),
    ),
    "mail": (
        "ITU Mail, read-only: inbox, messages, attachments",
        (
            "mail_status",
            "mail_list_inbox",
            "mail_get_message",
            "mail_get_attachment",
        ),
    ),
}

TOOLSET_NAMES: tuple[str, ...] = tuple(TOOLSETS)
CORE_TOOL_NAMES: frozenset[str] = frozenset(TOOLSETS[CORE_TOOLSET][1])
TOOL_TO_TOOLSET: dict[str, str] = {
    tool: toolset for toolset, (_, tools) in TOOLSETS.items() for tool in tools
}


def _parse(variable: str, env: Mapping[str, str]) -> list[str]:
    names = [item.strip().lower() for item in env.get(variable, "").split(",")]
    names = [name for name in names if name]
    unknown = sorted(set(names) - set(TOOLSET_NAMES))
    if unknown:
        raise ToolsetError(
            f"{variable} contains unknown toolset(s): {', '.join(unknown)}. "
            f"Valid toolsets: {', '.join(TOOLSET_NAMES)}."
        )
    return names


def enabled_toolsets(env: Mapping[str, str] | None = None) -> list[str]:
    """Return the active toolset names, in display order.

    Raises ``ToolsetError`` for an unknown name or an attempt to disable the
    always-on ``core`` group.
    """
    env = os.environ if env is None else env
    allowed = _parse(ALLOW_ENV, env)
    denied = set(_parse(DENY_ENV, env))
    if CORE_TOOLSET in denied:
        raise ToolsetError(
            f"{DENY_ENV} cannot disable '{CORE_TOOLSET}': it is always on. "
            f"Valid toolsets: {', '.join(TOOLSET_NAMES)}."
        )
    active = set(allowed) if allowed else set(TOOLSET_NAMES)
    active -= denied
    active.add(CORE_TOOLSET)
    return [name for name in TOOLSET_NAMES if name in active]


def filter_tool_names(
    tool_names: Iterable[str],
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Keep the tools whose toolset is active, preserving the given order.

    With neither variable set this returns ``tool_names`` unchanged. A tool
    that has no toolset is kept, so a missing mapping can never hide a tool
    silently; the partition test is what catches that case.
    """
    active = set(enabled_toolsets(env))
    return [
        name
        for name in tool_names
        if TOOL_TO_TOOLSET.get(name, CORE_TOOLSET) in active
    ]


def local_tool_names(tool_names: Iterable[str]) -> list[str]:
    """Selection for the local stdio server and the CLI listings.

    Loads ``.env`` first so the variables work from the same file as the
    credentials, even before a ``NinovaMcpApp`` exists.
    """
    load_ninova_env()
    return filter_tool_names(tool_names)


def describe_toolsets(tool_names: Iterable[str]) -> list[str]:
    """Lines for ``itu-mcp --list-toolsets``: name, tool count, state, summary."""
    load_ninova_env()
    active = set(enabled_toolsets())
    available = set(tool_names)
    width = max(len(name) for name in TOOLSET_NAMES)
    lines = []
    for name, (summary, tools) in TOOLSETS.items():
        count = sum(1 for tool in tools if tool in available)
        state = "on " if name in active else "off"
        lines.append(f"{name:<{width}}  {count:>3}  {state}  {summary}")
    return lines
