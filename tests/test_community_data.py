from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ninova_mcp.community_data import (
    CrossCheckDataClient,
    CrossCheckDataError,
    space_out_operators,
)
from ninova_mcp.prerequisites import (
    compare_required_course_sets,
    extract_branch_prerequisites,
    parse_prerequisite_expression,
)
from ninova_mcp.server import NinovaMcpApp

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class SpaceOutOperatorsTests(unittest.TestCase):
    def test_unspaced_veya_becomes_a_token(self) -> None:
        spaced = space_out_operators("BLG 322 MIN DDveya BLG 322E MIN DD")
        tree = parse_prerequisite_expression(spaced)
        self.assertEqual(tree["type"], "or")

    def test_unspaced_ve_becomes_a_token(self) -> None:
        spaced = space_out_operators("(BLG 322 MIN DD)ve (MAT 210 MIN DD)")
        tree = parse_prerequisite_expression(spaced)
        self.assertEqual(tree["type"], "and")

    def test_grade_without_period_is_attached(self) -> None:
        spaced = space_out_operators("CEN 4901E MIN BB")
        tree = parse_prerequisite_expression(spaced)
        self.assertEqual(tree, {"type": "course", "code": "CEN 4901E", "min_grade": "BB"})


class CompareRequiredCourseSetsTests(unittest.TestCase):
    def test_identical_trees_match(self) -> None:
        a = parse_prerequisite_expression("BLG 322E MIN. DD")
        b = parse_prerequisite_expression(space_out_operators("BLG 322E MIN DD"))
        result = compare_required_course_sets(a, b)
        self.assertTrue(result["matches"])

    def test_extra_course_is_flagged(self) -> None:
        a = parse_prerequisite_expression("BLG 322E MIN. DD")
        b = parse_prerequisite_expression(space_out_operators("BLG 322E MIN DDve CEN 322E MIN DD"))
        result = compare_required_course_sets(a, b)
        self.assertFalse(result["matches"])
        self.assertEqual(result["only_in_second"], ["CEN 322E"])

    def test_grade_mismatch_is_flagged(self) -> None:
        a = parse_prerequisite_expression("CEN 4901E MIN. BB")
        b = parse_prerequisite_expression(space_out_operators("CEN 4901E MIN CC"))
        result = compare_required_course_sets(a, b)
        self.assertFalse(result["matches"])
        self.assertEqual(result["grade_mismatches"], ["CEN 4901E"])

    def test_none_trees_match(self) -> None:
        self.assertTrue(compare_required_course_sets(None, None)["matches"])


class CrossCheckDataClientTests(unittest.TestCase):
    def test_rejects_plain_http_base(self) -> None:
        with self.assertRaises(CrossCheckDataError):
            CrossCheckDataClient(base_url="http://example.com")

    def test_offhost_url_is_rejected(self) -> None:
        client = CrossCheckDataClient()
        with self.assertRaises(CrossCheckDataError):
            client._validate_url("https://evil.example.com/courses.psv")

    def test_parses_psv_and_finds_capstone(self) -> None:
        client = CrossCheckDataClient()
        response = Mock(status_code=200, text=fixture("crosscheck_courses.psv"))
        with patch(
            "ninova_mcp.community_data.request_with_safe_redirects",
            return_value=response,
        ):
            courses = client.get_courses()
        self.assertIn("CEN 4901E", courses)
        self.assertEqual(courses["CEN 4901E"]["credit_requirement_text"], "95,00")

    def test_course_prerequisite_tree_for_capstone(self) -> None:
        client = CrossCheckDataClient()
        response = Mock(status_code=200, text=fixture("crosscheck_courses.psv"))
        with patch(
            "ninova_mcp.community_data.request_with_safe_redirects",
            return_value=response,
        ):
            result = client.get_course_prerequisite_tree("CEN 4901E")
        self.assertIsNotNone(result)
        self.assertEqual(result["credit_requirement"], 95.0)
        codes = {c["code"] for c in _flatten(result["tree"])}
        self.assertIn("MAT 210", codes)
        self.assertIn("MAT 210E", codes)

    def test_course_with_empty_expression_has_no_tree(self) -> None:
        client = CrossCheckDataClient()
        response = Mock(status_code=200, text=fixture("crosscheck_courses.psv"))
        with patch(
            "ninova_mcp.community_data.request_with_safe_redirects",
            return_value=response,
        ):
            result = client.get_course_prerequisite_tree("CEN 101E")
        self.assertIsNone(result["tree"])

    def test_unknown_course_returns_none(self) -> None:
        client = CrossCheckDataClient()
        response = Mock(status_code=200, text=fixture("crosscheck_courses.psv"))
        with patch(
            "ninova_mcp.community_data.request_with_safe_redirects",
            return_value=response,
        ):
            self.assertIsNone(client.get_course_prerequisite_tree("ZZZ 999"))

    def test_http_error_raises(self) -> None:
        client = CrossCheckDataClient()
        response = Mock(status_code=500, text="")
        with patch(
            "ninova_mcp.community_data.request_with_safe_redirects",
            return_value=response,
        ):
            with self.assertRaises(CrossCheckDataError):
                client.get_courses()


def _flatten(tree):
    from ninova_mcp.prerequisites import flatten_courses

    return flatten_courses(tree)


class ExplainEligibilityCrossCheckTests(unittest.TestCase):
    """Tool-level: explain_course_eligibility attaches a cross_check block."""

    def setUp(self) -> None:
        self.app = NinovaMcpApp()
        self.branch_rules = extract_branch_prerequisites(
            fixture("onsart_ara_cen.html"), "https://obs.itu.edu.tr/x", "CEN"
        )

    def _patch_obs(self):
        return patch.object(
            self.app.obs_public,
            "get_branch_prerequisites",
            return_value=self.branch_rules,
        )

    def test_agreement_is_reported(self) -> None:
        secondary_result = {
            "tree": self.branch_rules["rules"]["CEN 4901E"]["requirement_tree"],
            "credit_requirement": 95.0,
            "raw_expression": "x",
        }
        with self._patch_obs(), patch.object(
            self.app.prereq_crosscheck, "get_course_prerequisite_tree", return_value=secondary_result
        ):
            result = self.app.explain_course_eligibility("CEN 4901E")
        self.assertTrue(result["cross_check"]["available"])
        self.assertTrue(result["cross_check"]["agrees_with_obs"])

    def test_disagreement_does_not_change_obs_verdict(self) -> None:
        """OBS stays authoritative even when the secondary source disagrees."""
        secondary_result = {
            "tree": {"type": "course", "code": "CEN 999E", "min_grade": None},
            "credit_requirement": 50.0,
            "raw_expression": "x",
        }
        with self._patch_obs(), patch.object(
            self.app.prereq_crosscheck, "get_course_prerequisite_tree", return_value=secondary_result
        ):
            result = self.app.explain_course_eligibility("CEN 4901E")
        self.assertEqual(result["credit_requirement"], 95.0)  # unaffected by disagreement
        self.assertFalse(result["cross_check"]["agrees_with_obs"])
        self.assertIn("CEN 999E", result["cross_check"]["only_in_secondary_source"])
        self.assertFalse(result["cross_check"]["credit_requirement_matches"])

    def test_fetch_failure_is_non_fatal(self) -> None:
        with self._patch_obs(), patch.object(
            self.app.prereq_crosscheck,
            "get_course_prerequisite_tree",
            side_effect=CrossCheckDataError("network down"),
        ):
            result = self.app.explain_course_eligibility("CEN 4901E")
        self.assertIn("prerequisite_status", result)  # OBS answer still present
        self.assertFalse(result["cross_check"]["available"])

    def test_course_absent_from_secondary_source_is_unavailable(self) -> None:
        with self._patch_obs(), patch.object(
            self.app.prereq_crosscheck, "get_course_prerequisite_tree", return_value=None
        ):
            result = self.app.explain_course_eligibility("CEN 4901E")
        self.assertFalse(result["cross_check"]["available"])

    def test_cross_check_present_on_no_prerequisites_path(self) -> None:
        with self._patch_obs(), patch.object(
            self.app.prereq_crosscheck, "get_course_prerequisite_tree", return_value=None
        ):
            result = self.app.explain_course_eligibility("CEN 101E")
        self.assertEqual(result["prerequisite_status"], "no_prerequisites")
        self.assertIn("cross_check", result)

    def test_cross_check_present_on_unknown_table_path(self) -> None:
        unparsed = {"branch": "CEN", "url": "u", "constrained_course_count": 0, "rules": {}, "table_parsed": False}
        with patch.object(self.app.obs_public, "get_branch_prerequisites", return_value=unparsed), \
             patch.object(self.app.prereq_crosscheck, "get_course_prerequisite_tree", return_value=None):
            result = self.app.explain_course_eligibility("CEN 4901E")
        self.assertEqual(result["prerequisite_status"], "unknown")
        self.assertIn("cross_check", result)


if __name__ == "__main__":
    unittest.main()
