from __future__ import annotations

import unittest

from ninova_mcp.relative_grading import (
    GRADE_ORDER,
    TABLE_1,
    TABLE_2_COEFFICIENTS,
    class_average,
    estimate_method1,
    estimate_method2,
    estimate_relative_grade,
    pick_class_level,
    standard_deviation,
    t_score,
)


class BasicStatsTests(unittest.TestCase):
    SCORES = [40, 50, 60, 70, 80]  # x̄=60, population std=14.142... -> 14.14

    def test_class_average(self) -> None:
        self.assertEqual(class_average(self.SCORES), 60.0)

    def test_standard_deviation_matches_population_stdev(self) -> None:
        import statistics

        self.assertEqual(standard_deviation(self.SCORES), round(statistics.pstdev(self.SCORES), 2))
        self.assertEqual(standard_deviation(self.SCORES), 14.14)

    def test_t_score_of_the_mean_is_fifty(self) -> None:
        self.assertEqual(t_score(60, 60.0, 14.14), 50.0)

    def test_t_score_above_and_below_mean(self) -> None:
        self.assertEqual(t_score(80, 60.0, 14.14), 64.14)
        self.assertEqual(t_score(40, 60.0, 14.14), 35.86)

    def test_empty_scores_rejected(self) -> None:
        with self.assertRaises(ValueError):
            class_average([])
        with self.assertRaises(ValueError):
            standard_deviation([])

    def test_zero_std_t_score_is_undefined(self) -> None:
        with self.assertRaises(ValueError):
            t_score(70, 70.0, 0.0)


class Table1StructureTests(unittest.TestCase):
    """Guards the hand-reconstructed 'Mükemmel' row against regression."""

    def test_every_row_has_all_thirteen_grades(self) -> None:
        for row in TABLE_1:
            with self.subTest(row=row["label"]):
                self.assertEqual(set(row["thresholds"]), set(GRADE_ORDER))

    def test_every_row_thresholds_strictly_increasing(self) -> None:
        for row in TABLE_1:
            values = [row["thresholds"][g] for g in GRADE_ORDER]
            with self.subTest(row=row["label"]):
                self.assertEqual(values, sorted(values))
                self.assertEqual(len(values), len(set(values)))

    def test_every_row_follows_the_plus_2_plus_3_alternating_pattern(self) -> None:
        """The pattern present in all 7 fully-legible rows, used to fill the
        one missing value ('Mükemmel' -> BA) in the eighth.
        """
        for row in TABLE_1:
            values = [row["thresholds"][g] for g in GRADE_ORDER]
            diffs = [b - a for a, b in zip(values, values[1:])]
            with self.subTest(row=row["label"]):
                self.assertEqual(diffs, [2, 3] * 6)

    def test_mukemmel_row_reconstructed_ba_value(self) -> None:
        mukemmel = next(row for row in TABLE_1 if row["label"] == "Mükemmel")
        self.assertEqual(mukemmel["thresholds"]["BA"], 54)

    def test_bands_are_contiguous_with_no_gaps(self) -> None:
        ordered = sorted(TABLE_1, key=lambda r: r["min_avg"])
        for lower, upper in zip(ordered, ordered[1:]):
            with self.subTest(lower=lower["label"], upper=upper["label"]):
                self.assertEqual(lower["max_avg"], upper["min_avg"])

    def test_pick_class_level_boundaries(self) -> None:
        self.assertEqual(pick_class_level(79.99)["label"], "Mükemmel")
        self.assertEqual(pick_class_level(70.00)["label"], "Mükemmel")
        self.assertEqual(pick_class_level(69.99)["label"], "Pekiyi")
        self.assertEqual(pick_class_level(100.00)["label"], "Üstün başarı")
        self.assertEqual(pick_class_level(0.0)["label"], "Kötü")


class EstimateMethod1Tests(unittest.TestCase):
    def test_exact_threshold_is_met_not_missed(self) -> None:
        # Pekiyi row, BB threshold is 51.
        result = estimate_method1(t=51, mean=65.0)
        self.assertEqual(result["harf_notu"], "BB")

    def test_just_below_threshold_gets_previous_grade(self) -> None:
        result = estimate_method1(t=50.99, mean=65.0)
        self.assertEqual(result["harf_notu"], "CB+")

    def test_below_lowest_threshold_is_ff(self) -> None:
        result = estimate_method1(t=10, mean=65.0)
        self.assertEqual(result["harf_notu"], "FF")

    def test_very_high_t_score_is_aa(self) -> None:
        result = estimate_method1(t=100, mean=65.0)
        self.assertEqual(result["harf_notu"], "AA")

    def test_reports_class_level_label(self) -> None:
        result = estimate_method1(t=60, mean=50.0)
        self.assertEqual(result["sinif_duzeyi"], "Orta")


class EstimateMethod2Tests(unittest.TestCase):
    def test_score_at_mean_is_cc(self) -> None:
        result = estimate_method2(score=60.0, mean=60.0, std=10.0)
        self.assertEqual(result["harf_notu"], "CC")

    def test_score_two_std_above_mean_is_aa(self) -> None:
        result = estimate_method2(score=80.0, mean=60.0, std=10.0)
        self.assertEqual(result["harf_notu"], "AA")

    def test_score_one_std_below_mean_is_dd(self) -> None:
        result = estimate_method2(score=50.0, mean=60.0, std=10.0)
        self.assertEqual(result["harf_notu"], "DD")

    def test_score_more_than_one_std_below_mean_is_ff(self) -> None:
        result = estimate_method2(score=45.0, mean=60.0, std=10.0)
        self.assertEqual(result["harf_notu"], "FF")

    def test_bounds_cover_every_grade(self) -> None:
        result = estimate_method2(score=60.0, mean=60.0, std=10.0)
        self.assertEqual(set(result["sinir_degerleri"]), set(TABLE_2_COEFFICIENTS))


class EstimateRelativeGradeTests(unittest.TestCase):
    def test_my_score_already_in_class_scores(self) -> None:
        result = estimate_relative_grade(class_scores=[40, 50, 60, 70, 80], my_score=80)
        self.assertEqual(result["n"], 5)
        self.assertNotIn("auto_added_my_score", result)
        self.assertEqual(result["class_average"], 60.0)

    def test_my_score_auto_added_when_missing(self) -> None:
        result = estimate_relative_grade(class_scores=[40, 50, 60, 70], my_score=90)
        self.assertEqual(result["n"], 5)
        self.assertTrue(result["auto_added_my_score"])
        self.assertIn("otomatik eklendi", result["note"])

    def test_both_methods_present_for_normal_input(self) -> None:
        result = estimate_relative_grade(class_scores=[30, 40, 50, 60, 70, 80, 90], my_score=70)
        self.assertIsNotNone(result["yontem_1"])
        self.assertIn("harf_notu", result["yontem_1"])
        self.assertIn("harf_notu", result["yontem_2"])
        self.assertIn("my_t_score", result)

    def test_zero_variance_class_skips_method_1_gracefully(self) -> None:
        """Every student got the same score: T-score is undefined, but the
        tool must still return Method 2 and explain why Method 1 is absent
        rather than raising.
        """
        result = estimate_relative_grade(class_scores=[70, 70, 70], my_score=70)
        self.assertIsNone(result["yontem_1"])
        self.assertIn("yontem_1_uyari", result)
        self.assertIsNotNone(result["yontem_2"])

    def test_note_always_present(self) -> None:
        result = estimate_relative_grade(class_scores=[40, 50, 60], my_score=50)
        self.assertIn("bilgi amaçlı", result["note"])


if __name__ == "__main__":
    unittest.main()
