"""Three small defects: Turkish branch codes, programme type aliases, the GPA range.

Every course, grade and term below is invented.
"""

from __future__ import annotations

import unittest
from typing import Any

from ninova_mcp import gpa_common
from ninova_mcp.archive import normalize_course_code, same_course_other_branches, split_course_code
from ninova_mcp.archive_client import branch_path_segment
from ninova_mcp.gpa_common import decide_credits
from ninova_mcp.gpa_simulation import simulate_cumulative_gpa
from ninova_mcp.obs_errors import ObsError
from ninova_mcp.obs_public_schedule import ObsPublicSchedule


class TurkishCourseCodeTests(unittest.TestCase):
    SPELLINGS = ("işl 101", "İŞL 101", "ISL 101", "Işl 101", "isl101", " IŞL  101 ")

    def test_every_spelling_of_a_branch_gives_one_code(self) -> None:
        for spelling in self.SPELLINGS:
            with self.subTest(spelling=spelling):
                self.assertEqual(split_course_code(spelling), ("ISL", "101"))
                self.assertEqual(normalize_course_code(spelling), "ISL 101")

    def test_other_turkish_letters_fold_to_their_base_letter(self) -> None:
        self.assertEqual(normalize_course_code("çev 101e"), "CEV 101E")
        self.assertEqual(normalize_course_code("ÖĞÜ 4901el"), "OGU 4901EL")

    def test_ascii_codes_are_unchanged(self) -> None:
        self.assertEqual(split_course_code("blg 223e"), ("BLG", "223E"))
        self.assertEqual(split_course_code("FIZ 101EL"), ("FIZ", "101EL"))
        self.assertEqual(split_course_code("mim 101i"), ("MIM", "101I"))

    def test_the_branch_is_a_valid_archive_path_segment(self) -> None:
        for spelling in self.SPELLINGS:
            self.assertEqual(branch_path_segment(split_course_code(spelling)[0]), "ISL")

    def test_the_shared_course_key_follows(self) -> None:
        self.assertEqual({gpa_common.course_key(spelling) for spelling in self.SPELLINGS}, {"ISL 101"})

    def test_other_branches_ignore_the_spelling_of_the_own_branch(self) -> None:
        codes = [["ISL 101", "Data-Base & Systems", "ISL", 9], ["EKO 101", "Database and Systems", "EKO", 4]]
        for own in ("işl 101", "İŞL 101", "ISL 101"):
            found = same_course_other_branches(own, "Data Base ve Systems", codes)
            self.assertEqual([row["course_code"] for row in found], ["EKO 101"])

    def test_a_level_marker_still_separates_two_courses(self) -> None:
        codes = [["EKO 101", "Physics II", "EKO", 4]]
        self.assertEqual(same_course_other_branches("ISL 101", "Physics 2", codes), [])


class ProgramTypeAliasTests(unittest.TestCase):
    CASES = {
        "LS": ("ls", "LS", "Lisans", "LİSANS", " lisans "),
        "LU": ("lu", "LU", "Lisansüstü", "LİSANSÜSTÜ", "lisansustu", "Yüksek Lisans", "yuksek lisans",
               "YÜKSEK  LİSANS"),
        "ÖL": ("öl", "ÖL", "ol", "OL", "Önlisans", "ÖNLİSANS", "onlisans"),
        "LUİ": ("lui", "LUI", "luİ", "LUİ", "luı", "Lisansüstü 2", "lisansustu 2", "LİSANSÜSTÜ 2",
                "Lisansüstü 2.Öğretim", "lisansustu 2.ogretim", "LİSANSÜSTÜ 2.ÖĞRETİM"),
    }

    def test_every_alias_is_reachable_in_every_spelling(self) -> None:
        for expected, spellings in self.CASES.items():
            for spelling in spellings:
                with self.subTest(spelling=spelling):
                    self.assertEqual(ObsPublicSchedule._normalize_program_type(spelling), expected)

    def test_every_key_of_the_readable_map_is_accepted_as_written(self) -> None:
        for alias, expected in ObsPublicSchedule.PROGRAM_TYPE_MAP.items():
            with self.subTest(alias=alias):
                self.assertEqual(ObsPublicSchedule._normalize_program_type(alias), expected)
                self.assertEqual(ObsPublicSchedule._normalize_program_type(alias.upper()), expected)

    def test_the_readable_map_keeps_its_shape(self) -> None:
        mapping = ObsPublicSchedule.PROGRAM_TYPE_MAP
        self.assertEqual(mapping["lisansüstü 2.öğretim"], "LUİ")
        self.assertEqual(set(mapping.values()), {"LS", "LU", "ÖL", "LUİ"})

    def test_an_unknown_type_lists_the_accepted_values(self) -> None:
        for bad in ("XX", "", "doktora", "lisans 3"):
            with self.subTest(bad=bad), self.assertRaises(ObsError) as caught:
                ObsPublicSchedule._normalize_program_type(bad)
            message = str(caught.exception)
            for value in ("LS", "LU", "ÖL", "LUİ", "lisansüstü 2.öğretim", "yüksek lisans", "önlisans"):
                self.assertIn(value, message)

    def test_a_value_that_is_not_text_is_refused_the_same_way(self) -> None:
        with self.assertRaises(ObsError):
            ObsPublicSchedule._normalize_program_type(None)  # type: ignore[arg-type]


class SurvivingCombinationTests(unittest.TestCase):
    OPEN = [((3.0, 4.0), 4.0), ((3.0, 4.0), 4.0)]
    FIXED = [(3.0, 2.0)]

    def test_several_reproduce_exposes_exactly_those(self) -> None:
        decision = decide_credits(self.FIXED, self.OPEN, 3.4)
        self.assertEqual(decision.reason, "several_reproduce")
        self.assertEqual(decision.decided, (None, None))
        self.assertEqual(decision.surviving, ((0, 1), (1, 0)))

    def test_one_reproduces_exposes_that_one(self) -> None:
        decision = decide_credits(self.FIXED, self.OPEN, 3.45)
        self.assertEqual((decision.reason, decision.decided, decision.surviving), ("one_reproduces", (1, 1), ((1, 1),)))

    def test_without_a_verdict_every_combination_remains(self) -> None:
        everything = ((0, 0), (0, 1), (1, 0), (1, 1))
        for reason, kwargs, official in (
            ("no_official_average", {}, None),
            ("not_comparable", {"comparable": False}, 3.4),
            ("none_reproduces", {}, 1.0),
            ("too_many_candidates", {"max_rows": 1}, 3.4),
        ):
            with self.subTest(reason=reason):
                decision = decide_credits(self.FIXED, self.OPEN, official, **kwargs)
                self.assertEqual(decision.reason, reason)
                self.assertIsNone(decision.surviving)
                self.assertEqual(decision.combinations(self.OPEN), everything)


def _met(code: str, credit: Any, grade: str, term: str, **extra: Any) -> dict[str, Any]:
    row = {"isMet": True, "bransKodu": code, "dersAdi": f"Course {code}", "harfNotu": grade, "donem": term,
           "kredisi": str(credit), "kredisiDec": credit, "sayilanKredi": credit, "grupName": ""}
    row.update(extra)
    return row


def _status(*rows: tuple[str, Any, Any]) -> dict[str, Any]:
    return {"statusCode": 0, "kayitDurumuList": [{"kayitDurumuDonemList": [
        {"akademikDonemKodu": term, "donemlikNotOrtalamasi": term_gpa, "genelNotOrtalamasi": cumulative,
         "verilenKredi": 9}
        for term, term_gpa, cumulative in rows
    ]}]}


class GpaRangeTests(unittest.TestCase):
    """203010: ZZA 101 3 CC, ZZB 201 AA (3 or 4), ZZC 202 AA (3 or 4).

    Both 3: 30 / 9 = 3.33. One 3 and one 4: 34 / 10 = 3.40. Both 4: 38 / 11 = 3.45.
    """

    def simulate(self, term_gpa: Any, cumulative: Any) -> dict[str, Any]:
        graduation = {"mezuniyetimeNeKaldiBilgi": {"gpa": cumulative, "checkMetMezuniyetList": [
            _met("ZZA 101", 3, "CC", "203010"),
            _met("ZZB 201", 3, "AA", "203010", sayilanKredi=4),
            _met("ZZC 202", 3, "AA", "203010", sayilanKredi=4),
        ]}}
        return simulate_cumulative_gpa(graduation, _status(("203010", term_gpa, cumulative)), [], term_code="203020")

    def test_only_the_combinations_the_official_average_allows_are_in_the_range(self) -> None:
        result = self.simulate(3.4, 3.4)
        self.assertEqual([choice["decided"] for choice in result["credit_choices"]], [False, False])
        # gpa keeps kredisi for both rows; the range is the one value the two survivors give.
        self.assertEqual(result["current"]["gpa"], 3.33)
        self.assertEqual(result["current"]["gpa_range"], [3.4, 3.4])
        self.assertEqual(result["projected"]["gpa_range"], [3.4, 3.4])
        self.assertTrue(any(w.startswith("ambiguous_credit") for w in result["warnings"]))

    def test_it_is_strictly_narrower_than_the_independent_range(self) -> None:
        narrowed = self.simulate(3.4, 3.4)["current"]["gpa_range"]
        independent = self.simulate(None, None)["current"]["gpa_range"]
        self.assertEqual(independent, [3.33, 3.45])
        self.assertGreater(narrowed[0], independent[0])
        self.assertLess(narrowed[1], independent[1])

    def test_an_average_no_combination_reproduces_leaves_all_of_them(self) -> None:
        self.assertEqual(self.simulate(1.0, 1.0)["current"]["gpa_range"], [3.33, 3.45])


if __name__ == "__main__":
    unittest.main()
