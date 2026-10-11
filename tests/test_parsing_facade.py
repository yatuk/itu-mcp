"""``ninova_mcp.parsing`` keeps providing every name it provided as one file.

The rest of ``src/`` and the tests import parsers and helpers through
``ninova_mcp.parsing``, private ones included. The lists below were taken from
the module while it was a single file: every function, class, constant and
compiled pattern defined in it, now grouped by the module it lives in.
"""

from __future__ import annotations

import importlib
import re
import unittest
from pathlib import Path

from ninova_mcp import parsing

REPO = Path(__file__).resolve().parent.parent

HOMES = {
    "parsing_common": """
        COURSE_CODE_RE COURSE_PATH_RE DATE_RE FILENAME_UNSAFE_RE FILE_EXTENSIONS MONTH_NAMES TURKIYE_TZ _CLOCK _DAY_MONTH
        _ORDERED_RANGE_RE _SPLIT_RANGE_RE _YEARLESS_DATE_RE _is_day_month _looks_like_date clean_text
        infer_yearless_datetime is_course_root is_internal_ninova_url link_kind make_soup ninova_datetime_iso
        normalize_lookup_text normalize_url parse_ninova_datetime pretty_json sanitize_filename slugify split_date_range
        split_trailing_ninova_datetime""",
    "parsing_page": """
        SnapshotReference _extract_link_context _extract_links _extract_tables _find_table_by_headers _iter_headings
        _table_after_heading _table_to_rows _unique_links compare_snapshot_payloads extract_named_table
        make_snapshot_payload parse_html_page""",
    "parsing_dashboard": """
        _COURSE_GROUP_RE _CRN_LABEL_RE _YEAR_LIKE_NUMBER_RE _context_from_anchor _course_group _course_own_text
        _extract_course_title _is_year_like_code _pick_code _with_inferred_dates extract_courses summarize_dashboard""",
    "parsing_assignments": """
        _extract_assignment_counts _extract_labeled_value _parse_upload_row extension_allowed extract_assignment_detail
        extract_assignment_upload_form extract_assignment_upload_status extract_assignments_list match_upload_slot""",
    "parsing_attendance": "_WEEK_NUMBER_RE _total_attendance apply_attendance_calendar extract_attendance",
    "parsing_course": """
        _HELP_BOX_RE _extract_remote_session_rows _is_noise_table _join_virtual_path _pairs_from_rows _remove_once
        extract_announcement_detail extract_announcements_list extract_course_info extract_course_sections
        extract_file_directory extract_gradebook extract_message_board extract_message_thread_detail
        extract_remote_learning""",
    "parsing_obs_public": """
        _SCHEDULE_PLACEHOLDER_RE _SCHEDULE_RESERVATION_SEGMENT_RE _parse_schedule_reservation _schedule_value
        extract_academic_calendar extract_course_schedule_table extract_course_search_results
        extract_course_select_options extract_prerequisite_list""",
    "parsing_portal": "extract_cafeteria_menu extract_campus_card_info extract_help_tickets extract_notifications",
}
NAMES = [name for names in HOMES.values() for name in names.split()]

_IMPORT_RE = re.compile(r"from (?:ninova_mcp)?\.parsing import\s*(\([^)]*\)|[^\n]*)")
_ATTRIBUTE_RE = re.compile(r"(?<![\w.])parsing\.([A-Za-z_]\w*)")
_PATCH_RE = re.compile(
    r"""patch\(\s*["']ninova_mcp\.parsing\.|patch\.object\(\s*parsing\s*,|setattr\(\s*parsing\s*,"""
    r"""|setattr\(\s*["']ninova_mcp\.parsing\."""
)


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
        self.assertEqual(len(set(NAMES)), 95)
        missing = [name for name in NAMES if not hasattr(parsing, name)]
        self.assertEqual(missing, [])

    def test_every_name_is_the_same_object_as_in_its_home_module(self) -> None:
        for module_name, names in HOMES.items():
            module = importlib.import_module(f"ninova_mcp.{module_name}")
            for name in names.split():
                with self.subTest(module=module_name, name=name):
                    self.assertIs(getattr(parsing, name), getattr(module, name))
                    home = getattr(getattr(module, name), "__module__", None)
                    if home is not None and home.startswith("ninova_mcp"):
                        self.assertEqual(home, f"ninova_mcp.{module_name}")

    def test_the_shared_helpers_import_none_of_the_other_parsing_modules(self) -> None:
        source = (REPO / "src" / "ninova_mcp" / "parsing_common.py").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"^\s*(?:from|import) \.?(parsing\w*)", source, re.MULTILINE), [])

    def test_every_name_imported_through_the_module_exists(self) -> None:
        used = _names_used_through_the_module()
        self.assertIn("src/ninova_mcp/client.py", used)
        for path, names in used.items():
            with self.subTest(path=path):
                self.assertEqual(sorted(name for name in names if not hasattr(parsing, name)), [])

    def test_a_patch_on_the_facade_is_one_the_caller_actually_reads(self) -> None:
        """A parser calls the helper of its own module, so most patches here would do nothing.

        The exception is a caller that imports the name from the facade inside
        the function body, at call time. It reads the facade attribute on every
        call, so patching the facade works. Any other
        patch on ``ninova_mcp.parsing.<name>`` is silently ineffective and has to
        target the module the calling parser lives in instead.
        """
        this_file = Path(__file__).resolve()
        patched = re.compile(r"""["']ninova_mcp\.parsing\.(\w+)["']""")
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in (REPO / "src" / "ninova_mcp").glob("*.py")
        )
        offenders = []
        for path in _python_files():
            if path.resolve() == this_file:
                continue
            text = path.read_text(encoding="utf-8")
            if not _PATCH_RE.search(text):
                continue
            for name in sorted(set(patched.findall(text))):
                lazy_import = re.compile(
                    rf"^[ \t]+from \.parsing import [^\n]*\b{re.escape(name)}\b", re.MULTILINE
                )
                if not lazy_import.search(source):
                    offenders.append(f"{path.relative_to(REPO).as_posix()}: {name}")
        self.assertEqual(offenders, [])

if __name__ == "__main__":
    unittest.main()
