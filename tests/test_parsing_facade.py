"""``ninova_mcp.parsing`` keeps providing every name it provided as one file.

The rest of ``src/`` and the tests import parsers and helpers through
``ninova_mcp.parsing``, private ones included. The list below was taken from
the module while it was a single file: every function, class, constant and
compiled pattern defined in it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from ninova_mcp import parsing

REPO = Path(__file__).resolve().parent.parent

NAMES = """
COURSE_CODE_RE COURSE_PATH_RE DATE_RE FILENAME_UNSAFE_RE FILE_EXTENSIONS MONTH_NAMES SnapshotReference TURKIYE_TZ
_CLOCK _COURSE_GROUP_RE _CRN_LABEL_RE _DAY_MONTH _HELP_BOX_RE _ORDERED_RANGE_RE _SCHEDULE_PLACEHOLDER_RE
_SCHEDULE_RESERVATION_SEGMENT_RE _SPLIT_RANGE_RE _WEEK_NUMBER_RE _YEARLESS_DATE_RE _YEAR_LIKE_NUMBER_RE
_context_from_anchor _course_group _course_own_text _extract_assignment_counts _extract_course_title
_extract_labeled_value _extract_link_context _extract_links _extract_remote_session_rows _extract_tables
_find_table_by_headers _is_day_month _is_noise_table _is_year_like_code _iter_headings _join_virtual_path
_looks_like_date _pairs_from_rows _parse_schedule_reservation _parse_upload_row _pick_code _remove_once
_schedule_value _table_after_heading _table_to_rows _total_attendance _unique_links _with_inferred_dates
apply_attendance_calendar clean_text compare_snapshot_payloads extension_allowed extract_academic_calendar
extract_announcement_detail extract_announcements_list extract_assignment_detail extract_assignment_upload_form
extract_assignment_upload_status extract_assignments_list extract_attendance extract_cafeteria_menu
extract_campus_card_info extract_course_info extract_course_schedule_table extract_course_search_results
extract_course_sections extract_course_select_options extract_courses extract_file_directory extract_gradebook
extract_help_tickets extract_message_board extract_message_thread_detail extract_named_table extract_notifications
extract_prerequisite_list extract_remote_learning infer_yearless_datetime is_course_root is_internal_ninova_url
link_kind make_snapshot_payload make_soup match_upload_slot ninova_datetime_iso normalize_lookup_text normalize_url
parse_html_page parse_ninova_datetime pretty_json sanitize_filename slugify split_date_range
split_trailing_ninova_datetime summarize_dashboard
""".split()

_IMPORT_RE = re.compile(r"from (?:ninova_mcp)?\.parsing import\s*(\([^)]*\)|[^\n]*)")
_ATTRIBUTE_RE = re.compile(r"(?<![\w.])parsing\.([A-Za-z_]\w*)")


def _python_files() -> list[Path]:
    return sorted([*(REPO / "src").rglob("*.py"), *(REPO / "tests").rglob("*.py")])


def _names_used_through_the_module() -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        names: set[str] = set()
        for match in _IMPORT_RE.finditer(text):
            pieces = re.sub(r"\bas\s+\w+", "", match.group(1))
            names.update(re.findall(r"[A-Za-z_]\w*", pieces))
        if re.search(r"^from ninova_mcp import .*\bparsing\b", text, re.MULTILINE):
            names.update(_ATTRIBUTE_RE.findall(text))
        if names:
            used[path.relative_to(REPO).as_posix()] = names
    return used


class ParsingFacadeTests(unittest.TestCase):
    def test_every_name_of_the_single_file_module_is_still_provided(self) -> None:
        self.assertEqual(len(NAMES), 95)
        missing = [name for name in NAMES if not hasattr(parsing, name)]
        self.assertEqual(missing, [])

    def test_every_name_imported_through_the_module_exists(self) -> None:
        used = _names_used_through_the_module()
        self.assertIn("src/ninova_mcp/client.py", used)
        for path, names in used.items():
            with self.subTest(path=path):
                self.assertEqual(sorted(name for name in names if not hasattr(parsing, name)), [])


if __name__ == "__main__":
    unittest.main()
