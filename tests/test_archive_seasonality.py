"""Seasonality claims are only as strong as the records behind them. Invented data."""

from __future__ import annotations

import unittest

from ninova_mcp.archive import (
    MIN_SEASONALITY_TERMS,
    recommend_course_timing,
    seasonality,
    seasonality_sentence,
)


def terms(guz: int = 0, bahar: int = 0, yaz: int = 0) -> list[str]:
    slugs: list[str] = []
    for season, count in (("guz", guz), ("bahar", bahar), ("yaz", yaz)):
        slugs += [f"{2000 + i}-{2001 + i}-{season}" for i in range(count)]
    return slugs


class SeasonalityPatternTests(unittest.TestCase):
    def test_too_few_records_claim_nothing(self) -> None:
        self.assertEqual(MIN_SEASONALITY_TERMS, 3)
        for slugs in (terms(guz=1), terms(guz=2), terms(guz=1, bahar=1), terms(yaz=2)):
            with self.subTest(slugs=slugs):
                summary = seasonality(slugs)
                self.assertEqual(summary["pattern"], "insufficient_data")
                self.assertIsNone(summary["only_season"])
                self.assertIsNone(summary["dominant_season"])
                self.assertEqual(summary["total_terms"], len(slugs))
                sentence = seasonality_sentence("QAB 102E", summary)
                self.assertIn("yetersiz", sentence)
                self.assertNotIn("yalnızca Güz döneminde", sentence)
                self.assertNotIn("çoğunlukla", sentence)

    def test_no_records(self) -> None:
        summary = seasonality([])
        self.assertEqual((summary["pattern"], summary["total_terms"]), ("no_data", 0))
        self.assertIn("kaydı yok", seasonality_sentence("QAB 102E", summary))

    def test_even_counts_mean_every_term_whatever_summer_does(self) -> None:
        for guz, bahar, yaz in ((9, 9, 10), (9, 9, 8), (5, 4, 0), (3, 5, 1)):
            with self.subTest(counts=(guz, bahar, yaz)):
                summary = seasonality(terms(guz, bahar, yaz))
                self.assertEqual(summary["pattern"], "every_regular_term")
                self.assertIsNone(summary["dominant_season"])
                self.assertIsNone(summary["only_season"])
                self.assertEqual(summary["counts"].get("Yaz", 0), yaz)
                self.assertEqual((summary["regular_terms"], summary["summer_terms"]), (guz + bahar, yaz))
                sentence = seasonality_sentence("QAB 201E", summary)
                self.assertIn("her iki normal dönemde", sentence)
                self.assertNotIn("çoğunlukla", sentence)
                self.assertNotIn("yalnızca", sentence)

    def test_mostly_needs_two_thirds_of_the_regular_terms(self) -> None:
        summary = seasonality(terms(guz=6, bahar=3, yaz=4))
        self.assertEqual((summary["pattern"], summary["dominant_season"]), ("mostly_one_season", "Güz"))
        self.assertIn("çoğunlukla Güz", seasonality_sentence("QAB 210E", summary))
        self.assertEqual(seasonality(terms(guz=5, bahar=3))["pattern"], "every_regular_term")
        self.assertEqual(seasonality(terms(guz=1, bahar=2))["dominant_season"], "Bahar")

    def test_single_regular_season(self) -> None:
        summary = seasonality(terms(bahar=4))
        self.assertEqual(summary["pattern"], "single_season")
        self.assertEqual((summary["only_season"], summary["only_regular_season"]), ("Bahar", "Bahar"))
        self.assertIn("yalnızca Bahar döneminde açılıyor (4 dönem kaydı)", seasonality_sentence("QAB 4901E", summary))

    def test_single_regular_season_with_summer_school(self) -> None:
        summary = seasonality(terms(guz=5, yaz=2))
        self.assertEqual(summary["pattern"], "single_season")
        self.assertIsNone(summary["only_season"])
        self.assertEqual((summary["only_regular_season"], summary["dominant_season"]), ("Güz", "Güz"))
        sentence = seasonality_sentence("QAB 300", summary)
        self.assertIn("normal dönemlerden yalnızca Güz", sentence)
        self.assertIn("2 yaz", sentence)

    def test_summer_only(self) -> None:
        summary = seasonality(terms(yaz=3))
        self.assertEqual((summary["pattern"], summary["only_season"]), ("summer_only", "Yaz"))
        self.assertIsNone(summary["only_regular_season"])

    def test_recommendation_uses_the_same_wording(self) -> None:
        self.assertIn("yetersiz", recommend_course_timing("QAB 102E", seasonality(terms(guz=1)), []))
        self.assertIn("her iki normal dönemde", recommend_course_timing("QAB 201E", seasonality(terms(9, 9, 10)), []))
        self.assertIn("yalnızca Güz", recommend_course_timing("QAB 411E", seasonality(terms(guz=3)), []))


if __name__ == "__main__":
    unittest.main()
