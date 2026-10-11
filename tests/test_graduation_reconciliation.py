"""Graduation summary: which credit is which, and why the totals disagree. Invented data."""

from __future__ import annotations

import unittest
from typing import Any

from ninova_mcp.graduation import summarize_graduation_plan


def row(code: str, name: str, credit: Any, *, met: bool, counted: Any = None, group: str = "",
        grade: str = "", semester: int = 1) -> dict[str, Any]:
    return {"isMet": met, "bransKodu": code, "dersAdi": name, "grupName": group, "harfNotu": grade,
            "kredisiDec": credit, "kredisi": str(credit), "donemNo": semester,
            "sayilanKredi": credit if counted is None and met else (counted or 0)}


def payload(*rows: dict[str, Any], required: Any = 14, earned: Any = 9) -> dict[str, Any]:
    return {"mezuniyetimeNeKaldiBilgi": {
        "checkMetMezuniyetList": list(rows), "metKrediTotal": earned,
        "dersPlaniVM": {"gerekliMezuniyetKredisi": required, "gerekliMinGPA": 2},
    }}


ROWS = (
    row("QAB 101E", "Intro to Gizmos", 4, met=True, grade="BB"),
    # A 2 credit slot filled by a course OBS counted as 3 credits.
    row("ELC 201E", "Elective Sprockets", 2, met=True, counted=3, group="3rd Sem. Elective (TM)", grade="AA"),
    row("QAB 103E", "Gizmo Lab", 3, met=False),
    row("", "5th Sem. Elective (TM)", 3, met=False, group="5th Sem. Elective (TM)", semester=5),
)


class GraduationCreditTests(unittest.TestCase):
    def test_slot_credit_and_counted_credit_are_labelled(self) -> None:
        summary = summarize_graduation_plan(payload(*ROWS, required=13, earned=7))
        slot = summary["filled_elective_slots"][0]
        self.assertEqual((slot["credit"], slot["counted_credit"]), (2.0, 3))  # old keys unchanged
        self.assertEqual((slot["slot_credit"], slot["credit_differs"]), (2.0, True))
        self.assertIn("slot_credit", summary["credit_fields_note"])
        self.assertIn("counted_credit", summary["credit_fields_note"])
        self.assertEqual(summary["open_elective_slots"][0]["slot_credit"], 3.0)
        self.assertEqual(summary["open_elective_slots"][0]["credit"], 3.0)

    def test_disagreeing_totals_are_reconciled_by_name(self) -> None:
        # The plan is 4 + 2 + 3 + 3 = 12 credits. Earned is 4 + 3 = 7 because the 2 credit slot
        # counted 3, so 5 remain by subtraction while the open items sum to 3 + 3 = 6.
        summary = summarize_graduation_plan(payload(*ROWS, required=12, earned=7))
        self.assertEqual(summary["credits_remaining"], 5.0)
        self.assertEqual(summary["remaining_items_credit_total"], 6.0)
        reconciliation = summary["credit_reconciliation"]
        self.assertEqual(reconciliation["difference"], 1.0)
        self.assertEqual(reconciliation["explained_difference"], 1.0)
        self.assertEqual(reconciliation["explained_by"], [{
            "course_code": "ELC 201E", "slot": "3rd Sem. Elective (TM)",
            "slot_credit": 2.0, "counted_credit": 3.0, "difference": 1.0,
        }])
        self.assertIn("ELC 201E", reconciliation["note"])

    def test_agreeing_totals_need_no_reconciliation(self) -> None:
        rows = (ROWS[0], ROWS[2], ROWS[3])
        summary = summarize_graduation_plan(payload(*rows, required=10, earned=4))
        self.assertEqual(summary["remaining_items_credit_total"], 6.0)
        self.assertNotIn("credit_reconciliation", summary)

    def test_unexplained_difference_is_said_to_be_unexplained(self) -> None:
        rows = (ROWS[0], ROWS[2], ROWS[3])
        summary = summarize_graduation_plan(payload(*rows, required=20, earned=4))
        reconciliation = summary["credit_reconciliation"]
        self.assertEqual((reconciliation["difference"], reconciliation["explained_difference"]), (-10.0, 0.0))
        self.assertEqual(reconciliation["explained_by"], [])
        self.assertIn("açıklanamıyor", reconciliation["note"])

    def test_unreadable_totals_do_not_raise(self) -> None:
        summary = summarize_graduation_plan(payload(*ROWS, required=None, earned="x"))
        self.assertIsNone(summary["credits_remaining"])
        self.assertNotIn("credit_reconciliation", summary)
        self.assertEqual(summary["courses_completed"], None)
        self.assertEqual(summary["completed_course_count"], 2)


if __name__ == "__main__":
    unittest.main()
