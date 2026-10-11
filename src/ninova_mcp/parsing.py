"""HTML and JSON parsers of the server, kept importable under their old path.

The parsers live in the ``parsing_*`` modules, grouped by what they parse.
This module re-exports every name it used to define, private helpers
included, because the rest of the package and the tests import them from
here. The names are the same objects as in their home modules, so a parser
does NOT look its helpers up here: to replace a helper in a test, patch it
in the module that defines the caller (``ninova_mcp.parsing_course.clean_text``),
not on this module.
"""

# ruff: noqa: F401

from __future__ import annotations

from .parsing_common import (
    COURSE_CODE_RE,
    COURSE_PATH_RE,
    DATE_RE,
    FILENAME_UNSAFE_RE,
    FILE_EXTENSIONS,
    MONTH_NAMES,
    TURKIYE_TZ,
    _CLOCK,
    _DAY_MONTH,
    _ORDERED_RANGE_RE,
    _SPLIT_RANGE_RE,
    _YEARLESS_DATE_RE,
    _is_day_month,
    _looks_like_date,
    clean_text,
    infer_yearless_datetime,
    is_course_root,
    is_internal_ninova_url,
    link_kind,
    make_soup,
    ninova_datetime_iso,
    normalize_lookup_text,
    normalize_url,
    parse_ninova_datetime,
    pretty_json,
    sanitize_filename,
    slugify,
    split_date_range,
    split_trailing_ninova_datetime,
)
from .parsing_page import (
    SnapshotReference,
    _extract_link_context,
    _extract_links,
    _extract_tables,
    _find_table_by_headers,
    _iter_headings,
    _table_after_heading,
    _table_to_rows,
    _unique_links,
    compare_snapshot_payloads,
    extract_named_table,
    make_snapshot_payload,
    parse_html_page,
)
from .parsing_dashboard import (
    _COURSE_GROUP_RE,
    _CRN_LABEL_RE,
    _YEAR_LIKE_NUMBER_RE,
    _context_from_anchor,
    _course_group,
    _course_own_text,
    _extract_course_title,
    _is_year_like_code,
    _pick_code,
    _with_inferred_dates,
    extract_courses,
    summarize_dashboard,
)
from .parsing_assignments import (
    _extract_assignment_counts,
    _extract_labeled_value,
    _parse_upload_row,
    extension_allowed,
    extract_assignment_detail,
    extract_assignment_upload_form,
    extract_assignment_upload_status,
    extract_assignments_list,
    match_upload_slot,
)
from .parsing_attendance import (
    _WEEK_NUMBER_RE,
    _total_attendance,
    apply_attendance_calendar,
    extract_attendance,
)
from .parsing_course import (
    _HELP_BOX_RE,
    _extract_remote_session_rows,
    _is_noise_table,
    _join_virtual_path,
    _pairs_from_rows,
    _remove_once,
    extract_announcement_detail,
    extract_announcements_list,
    extract_course_info,
    extract_course_sections,
    extract_file_directory,
    extract_gradebook,
    extract_message_board,
    extract_message_thread_detail,
    extract_remote_learning,
)
from .parsing_obs_public import (
    _SCHEDULE_PLACEHOLDER_RE,
    _SCHEDULE_RESERVATION_SEGMENT_RE,
    _parse_schedule_reservation,
    _schedule_value,
    extract_academic_calendar,
    extract_course_schedule_table,
    extract_course_search_results,
    extract_course_select_options,
    extract_prerequisite_list,
)
from .parsing_portal import (
    extract_cafeteria_menu,
    extract_campus_card_info,
    extract_help_tickets,
    extract_notifications,
)
