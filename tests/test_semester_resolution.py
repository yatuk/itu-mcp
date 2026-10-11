"""A loose term name must never resolve silently to the wrong term."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, Mock

from ninova_mcp.obs_client import ObsClient, ObsError, describe_semester

# Synthetic term list, oldest first, the order OBS returns.
TERMS = {"ogrenciDonemListesi": [
    {"akademikDonemId": 41, "donemKodu": "204010", "akademikDonemAdi": "2039-2040 Güz Dönemi", "akademikDonemAdiEN": "2039-2040 Fall Semester"},
    {"akademikDonemId": 42, "donemKodu": "204020", "akademikDonemAdi": "2039-2040 Bahar Dönemi", "akademikDonemAdiEN": "2039-2040 Spring Semester"},
    {"akademikDonemId": 43, "donemKodu": "204030", "akademikDonemAdi": "2039-2040 Yaz Dönemi", "akademikDonemAdiEN": "2039-2040 Summer Semester"},
    {"akademikDonemId": 44, "donemKodu": "204110", "akademikDonemAdi": "2040-2041 Güz Dönemi", "akademikDonemAdiEN": "2040-2041 Fall Semester"},
    {"akademikDonemId": 45, "donemKodu": "204120", "akademikDonemAdi": "2040-2041 Bahar Dönemi", "akademikDonemAdiEN": "2040-2041 Spring Semester"},
    {"akademikDonemId": 46, "donemKodu": "204210", "akademikDonemAdi": "2041-2042 Güz Dönemi", "akademikDonemAdiEN": "2041-2042 Fall Semester"},
]}


def client(terms: dict = TERMS) -> ObsClient:
    obs = ObsClient(ninova_client=MagicMock())
    obs.list_semesters = Mock(return_value=terms)  # type: ignore[method-assign]
    return obs


class SeasonOnlyTests(unittest.TestCase):
    def test_season_only_query_resolves_to_the_most_recent_matching_term(self) -> None:
        for query, expected in (("bahar", 45), ("Bahar", 45), ("spring", 45), ("güz", 46), ("guz", 46),
                                ("GÜZ", 46), ("fall", 46), ("yaz", 43), ("summer", 43),
                                ("bahar dönemi", 45), ("Spring Semester", 45)):
            with self.subTest(query=query):
                self.assertEqual(client().resolve_semester(query)["akademikDonemId"], expected)

    def test_season_only_result_says_how_it_was_chosen(self) -> None:
        resolved = client().resolve_semester("bahar")
        note = resolved["resolution"]
        self.assertEqual(note["rule"], "most_recent_term_of_season")
        self.assertEqual(note["query"], "bahar")
        self.assertEqual([item["code"] for item in note["other_candidates"]], ["204020"])
        self.assertIn("2040-2041 Bahar", note["note"])

    def test_most_recent_is_decided_by_term_code_not_list_order(self) -> None:
        shuffled = {"ogrenciDonemListesi": list(reversed(TERMS["ogrenciDonemListesi"]))}
        self.assertEqual(client(shuffled).resolve_semester("bahar")["akademikDonemId"], 45)

    def test_single_match_carries_no_resolution_note(self) -> None:
        self.assertNotIn("resolution", client().resolve_semester("yaz"))
        self.assertNotIn("resolution", client().resolve_semester("204020"))


class YearAndSeasonTests(unittest.TestCase):
    def test_year_range_plus_season_matches_exactly_one(self) -> None:
        for query, expected in (("2039-2040 Bahar", 42), ("2039-2040 yaz", 43), ("2040-2041 güz", 44),
                                ("2040-2041 Fall", 44), ("2039/2040 bahar", 42), ("2039-2040 Bahar Dönemi", 42),
                                ("bahar 2040-2041", 45)):
            with self.subTest(query=query):
                resolved = client().resolve_semester(query)
                self.assertEqual(resolved["akademikDonemId"], expected)
                self.assertNotIn("resolution", resolved)

    def test_year_range_without_season_is_ambiguous_and_lists_candidates(self) -> None:
        with self.assertRaises(ObsError) as caught:
            client().resolve_semester("2039-2040")
        message = str(caught.exception)
        self.assertIn("ambiguous", message)
        for code in ("204010", "204020", "204030"):
            self.assertIn(code, message)
        self.assertNotIn("204110", message)

    def test_unknown_year_and_season_is_not_found(self) -> None:
        with self.assertRaisesRegex(ObsError, "not found"):
            client().resolve_semester("2050-2051 Bahar")


class BareNumberTests(unittest.TestCase):
    def test_a_bare_number_never_matches_inside_a_year(self) -> None:
        for query in ("7", 7, "40", "204", "2040", 2040, "2039", "0"):
            with self.subTest(query=query), self.assertRaises(ObsError):
                client().resolve_semester(query)

    def test_a_bare_year_error_lists_the_terms_of_that_year(self) -> None:
        with self.assertRaises(ObsError) as caught:
            client().resolve_semester("2041")
        message = str(caught.exception)
        self.assertIn("204110", message)
        self.assertIn("204210", message)
        self.assertNotIn("204010", message)

    def test_a_bare_year_does_not_resolve_even_when_one_term_holds_it(self) -> None:
        with self.assertRaises(ObsError):
            client().resolve_semester("2042")

    def test_a_fragment_of_a_word_does_not_match(self) -> None:
        with self.assertRaises(ObsError):
            client().resolve_semester("ah")


class ExistingFormsTests(unittest.TestCase):
    def test_id_code_and_full_name_forms_still_work(self) -> None:
        for query in (204020, "204020", ' "204020" ', 204020.0, 42, "42",
                      "2039-2040 Bahar Dönemi", "2039-2040 Spring Semester", "2039-2040 bahar"):
            with self.subTest(query=query):
                self.assertEqual(client().resolve_semester(query)["akademikDonemId"], 42)
        self.assertEqual(client().resolve_semester(None)["akademikDonemId"], 46)

    def test_describe_semester_names_the_term_and_code(self) -> None:
        text = describe_semester(client().resolve_semester("bahar"))
        self.assertIn("2040-2041 Bahar Dönemi", text)
        self.assertIn("204120", text)
        self.assertEqual(describe_semester({"akademikDonemId": 9}), "term id 9")


if __name__ == "__main__":
    unittest.main()
