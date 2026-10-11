"""Name, title, description and input schema of the built-in tools, as plain data.

One module per domain, named like the ``app_*`` module that implements the
tools. Nothing here imports a client, so the metadata can be read without
loading the rest of the server.

``BASE_TOOLS`` is every entry in publish order. Clients list tools in that
order and it is older than the grouping by domain, so it is written out in
``PUBLISH_ORDER`` instead of following the module order. A tool that is
defined in a domain module but not named there is published after the named
ones, in module order.

Features that ship their own module (``archive_tools``, ``seat_watch``,
``calendar_export``) keep their metadata next to their code;
``tool_registry`` appends those to the full ``TOOLS`` list.
"""

from __future__ import annotations

from typing import Any

from .core import CORE_TOOLS
from .mail import MAIL_TOOLS
from .ninova import NINOVA_TOOLS
from .tracking import TRACKING_TOOLS
from .files import FILES_TOOLS
from .obs import OBS_TOOLS
from .registration import REGISTRATION_TOOLS
from .portal import PORTAL_TOOLS
from .public import PUBLIC_TOOLS
from .campus import CAMPUS_TOOLS
from .planning import PLANNING_TOOLS
from .library import LIBRARY_TOOLS
from .archive import ARCHIVE_HISTORY_TOOLS

TOOL_GROUPS: tuple[list[dict[str, Any]], ...] = (
    CORE_TOOLS,
    MAIL_TOOLS,
    NINOVA_TOOLS,
    TRACKING_TOOLS,
    FILES_TOOLS,
    OBS_TOOLS,
    REGISTRATION_TOOLS,
    PORTAL_TOOLS,
    PUBLIC_TOOLS,
    CAMPUS_TOOLS,
    PLANNING_TOOLS,
    LIBRARY_TOOLS,
    ARCHIVE_HISTORY_TOOLS,
)

PUBLISH_ORDER: tuple[str, ...] = (
    "auth_status",
    "mail_status",
    "mail_list_inbox",
    "mail_get_message",
    "mail_get_attachment",
    "refresh_session",
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
    "sync_all_courses",
    "get_updates",
    "get_upcoming_deadlines",
    "read_page",
    "crawl_course",
    "download_resource",
    "read_resource_text",
    "get_assignment_upload_slots",
    "submit_assignment",
    "snapshot_page",
    "diff_snapshot",
    "obs_auth_status",
    "obs_get_profile",
    "obs_list_programs",
    "obs_list_semesters",
    "obs_get_registration_status",
    "obs_get_registration_draft",
    "obs_save_registration_draft",
    "obs_get_elective_group",
    "obs_validate_registration_plan",
    "obs_get_grade_distribution",
    "obs_get_advisor",
    "obs_get_internships",
    "obs_get_contacts",
    "obs_list_registered_courses",
    "obs_get_course_grades",
    "obs_get_attendance",
    "obs_get_schedule",
    "obs_get_graduation_remaining",
    "obs_get_campus_card",
    "obs_download_transcript",
    "obs_search_courses",
    "obs_get_course_prerequisites",
    "obs_calculate_gpa",
    "calculate_target_gpa",
    "estimate_relative_grade",
    "check_course_conflicts",
    "obs_get_notifications",
    "obs_get_help_tickets",
    "get_help_ticket",
    "obs_get_cloud_quota",
    "get_academic_calendar",
    "get_cafeteria_menu",
    "get_public_course_schedule",
    "get_public_course_prerequisites",
    "get_public_exam_schedule",
    "get_personal_exam_calendar",
    "search_itu_directory",
    "get_shuttle_schedule",
    "search_campus_locations",
    "get_sports_facility_hours",
    "get_itu_announcements",
    "library_search",
    "library_get_item",
    "library_check_availability",
    "library_get_account",
    "library_list_loans",
    "library_renew_loan",
    "library_reserve_item",
    "find_open_course_sections",
    "find_empty_classrooms",
    "list_degree_faculties",
    "list_degree_programs",
    "build_degree_plan",
    "explain_course_eligibility",
    "archive_list_terms",
    "archive_course_history",
    "archive_who_taught",
    "archive_instructor_courses",
    "archive_term_sections",
    "archive_fill_rate",
    "archive_search_courses",
    "archive_list_branches",
    "archive_compare_terms",
    "plan_remaining_courses",
)


def _in_publish_order() -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for group in TOOL_GROUPS:
        for tool in group:
            if tool["name"] in by_name:
                raise ValueError(f"Tool metadata is defined twice: {tool['name']}")
            by_name[tool["name"]] = tool
    missing = [name for name in PUBLISH_ORDER if name not in by_name]
    if missing:
        raise ValueError("PUBLISH_ORDER names tools without metadata: " + ", ".join(missing))
    ordered = [by_name.pop(name) for name in PUBLISH_ORDER]
    return ordered + list(by_name.values())


BASE_TOOLS: list[dict[str, Any]] = _in_publish_order()
