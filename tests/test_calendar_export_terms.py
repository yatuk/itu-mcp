"""Term dates for the .ics export, read from the academic-year calendar."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
import unittest

from ninova_mcp import academic_calendar
from ninova_mcp.academic_calendar import parse_academic_calendar
from ninova_mcp.calendar_export import find_term_dates

FIXTURES = Path(__file__).parent / "fixtures"
EN_URL = "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/EN/academic-calendar/index.php"
TR_URL = "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/TR/akademik-takvim/index.php"


def bilingual() -> dict[str, Any]:
    merged = academic_calendar.merge_calendar_languages(
        parse_academic_calendar((FIXTURES / "academic_calendar_year_en.html").read_text(encoding="utf-8"), EN_URL),
        parse_academic_calendar((FIXTURES / "academic_calendar_year_tr.html").read_text(encoding="utf-8"), TR_URL))
    return academic_calendar.combine_calendars([merged], today=date(2026, 10, 11))


class TermDatesForExportTests(unittest.TestCase):
    def test_export_lookup_uses_the_stated_term_dates(self) -> None:
        calendar = bilingual()
        self.assertEqual(find_term_dates(calendar, "2026-2027 Güz Dönemi", "LS"), {"term_start": "2026-09-28", "term_end": "2026-12-31"})
        self.assertEqual(find_term_dates(calendar, "2026-2027 Bahar Dönemi", "LS"), {"term_start": "2027-02-01", "term_end": "2027-04-30"})
        self.assertEqual(find_term_dates(calendar, "2026-2027 Güz Dönemi", "LU"), {"term_start": "2026-09-28", "term_end": "2026-12-31"})
        self.assertEqual(find_term_dates(calendar, "2026-2027 Yaz Dönemi", "LS"), {"term_start": "2027-07-05", "term_end": "2027-08-13"})
        # The summer school calendar is shared by undergraduate and graduate students.
        self.assertEqual(find_term_dates(calendar, "2026-2027 Yaz Dönemi", "LU"), {"term_start": "2027-07-05", "term_end": "2027-08-13"})
        self.assertEqual(find_term_dates(calendar, "2025-2026 Güz Dönemi", "LS"), {"term_start": None, "term_end": None})

    def test_export_lookup_never_fills_a_date_the_calendar_does_not_state(self) -> None:
        calendar = bilingual()
        calendar["terms"] = [dict(t, classes_end=None) if t["term"] == "fall" else t for t in calendar["terms"]]
        calendar["events"] = [e for e in calendar["events"] if e["uid"] not in {"1615", "1679"}]
        self.assertEqual(find_term_dates(calendar, "2026-2027 Güz Dönemi", "LS"), {"term_start": "2026-09-28", "term_end": None})


if __name__ == "__main__":
    unittest.main()
