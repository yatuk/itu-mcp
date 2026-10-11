"""estimate_relative_grade must not turn bad or degenerate input into a letter grade."""

from __future__ import annotations

import unittest

from ninova_mcp.relative_grading import MIN_CLASS_SIZE, estimate_relative_grade

CLASS = [35, 42, 48, 55, 58, 61, 64, 70, 77, 85, 92]


class ScoreValidationTests(unittest.TestCase):
    def test_out_of_range_class_score_is_named(self) -> None:
        with self.assertRaises(ValueError) as caught:
            estimate_relative_grade([50, -10, 250], 60)
        self.assertIn("class_scores[1]", str(caught.exception))
        self.assertIn("-10", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            estimate_relative_grade([50, 60, 250], 60)
        self.assertIn("class_scores[2]", str(caught.exception))
        self.assertIn("250", str(caught.exception))

    def test_out_of_range_own_score_is_named(self) -> None:
        with self.assertRaises(ValueError) as caught:
            estimate_relative_grade(CLASS, 300)
        self.assertIn("my_score", str(caught.exception))
        self.assertIn("300", str(caught.exception))

    def test_nan_and_infinity_are_rejected(self) -> None:
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError) as caught:
                    estimate_relative_grade([50, bad, 60], 55)
                self.assertIn("class_scores[1]", str(caught.exception))
                with self.assertRaises(ValueError):
                    estimate_relative_grade(CLASS, bad)

    def test_non_numbers_are_rejected(self) -> None:
        for bad in ("70", None, True):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    estimate_relative_grade([50, bad, 60], 55)  # type: ignore[list-item]

    def test_boundaries_are_valid(self) -> None:
        result = estimate_relative_grade([0, 100, *CLASS], 100)
        self.assertTrue(result["estimate_available"])


class DegenerateDistributionTests(unittest.TestCase):
    def assert_no_letter(self, result: dict, reason: str) -> None:
        self.assertIs(result["estimate_available"], False)
        self.assertEqual(result["no_estimate_reason"], reason)
        self.assertTrue(result["no_estimate_message"])
        self.assertIsNone(result["yontem_1"])
        self.assertIsNone(result["yontem_2"])
        self.assertNotIn("my_t_score", result)
        self.assertNotIn("harf_notu", str(result))

    def test_empty_class_gives_no_letter(self) -> None:
        result = estimate_relative_grade([], 50)
        self.assert_no_letter(result, "class_too_small")
        self.assertEqual((result["n"], result["class_average"], result["std_dev"]), (1, 50.0, 0.0))

    def test_single_score_gives_no_letter(self) -> None:
        self.assert_no_letter(estimate_relative_grade([70], 70), "class_too_small")

    def test_small_class_gives_statistics_but_no_letter(self) -> None:
        result = estimate_relative_grade([40, 50, 60, 70], 90)
        self.assert_no_letter(result, "class_too_small")
        self.assertEqual(result["n"], 5)
        self.assertEqual(result["class_average"], 62.0)
        self.assertGreater(result["std_dev"], 0)
        self.assertEqual((result["class_min"], result["class_max"]), (40.0, 90.0))
        self.assertEqual(result["min_class_size"], MIN_CLASS_SIZE)
        self.assertIn(str(MIN_CLASS_SIZE), result["no_estimate_message"])

    def test_identical_scores_give_no_letter_even_in_a_large_class(self) -> None:
        result = estimate_relative_grade([10] * 12, 10)
        self.assert_no_letter(result, "zero_std_dev")
        self.assertEqual(result["std_dev"], 0.0)
        self.assertEqual(result["n"], 12)

    def test_class_at_the_minimum_size_gets_both_methods(self) -> None:
        scores = CLASS[:MIN_CLASS_SIZE]
        result = estimate_relative_grade(scores, scores[-1])
        self.assertIs(result["estimate_available"], True)
        self.assertNotIn("no_estimate_reason", result)
        self.assertIn("harf_notu", result["yontem_1"])
        self.assertIn("harf_notu", result["yontem_2"])
        below = estimate_relative_grade(scores[:-1], scores[0])
        self.assertIs(below["estimate_available"], False)


class OwnScoreInclusionTests(unittest.TestCase):
    def test_default_assumes_a_matching_score_is_the_callers(self) -> None:
        result = estimate_relative_grade(CLASS, 70)
        self.assertEqual(result["n"], len(CLASS))
        self.assertIs(result["my_score_included"], True)
        self.assertIs(result["my_score_included_assumed"], True)
        self.assertIn("my_score_included=false", result["note"])
        self.assertNotIn("auto_added_my_score", result)

    def test_default_adds_a_score_that_is_not_in_the_list(self) -> None:
        result = estimate_relative_grade(CLASS, 71)
        self.assertEqual(result["n"], len(CLASS) + 1)
        self.assertIs(result["my_score_included"], False)
        self.assertIs(result["auto_added_my_score"], True)
        self.assertNotIn("my_score_included_assumed", result)

    def test_explicit_false_adds_even_when_a_classmate_has_the_same_score(self) -> None:
        result = estimate_relative_grade(CLASS, 70, my_score_included=False)
        self.assertEqual(result["n"], len(CLASS) + 1)
        self.assertIs(result["my_score_included"], False)
        self.assertIs(result["auto_added_my_score"], True)

    def test_explicit_true_does_not_add(self) -> None:
        result = estimate_relative_grade(CLASS, 70, my_score_included=True)
        self.assertEqual(result["n"], len(CLASS))
        self.assertNotIn("my_score_included_assumed", result)

    def test_explicit_true_with_a_missing_score_is_an_error(self) -> None:
        with self.assertRaises(ValueError) as caught:
            estimate_relative_grade(CLASS, 71, my_score_included=True)
        self.assertIn("my_score_included", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
