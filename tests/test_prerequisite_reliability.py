from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ninova_mcp.obs_client import ObsError, ObsPublicClient
from ninova_mcp.prerequisites import extract_branch_prerequisites
from ninova_mcp.server import NinovaMcpApp


BRANCH_URL = "https://obs.itu.edu.tr/public/GenelTanimlamalar/OnsartAra?DersBransKoduId=3"
BRANCH_HTML = """
<table><tr><th>Course Code</th><th>Course Name</th><th>Prerequisite</th><th>Credit</th></tr>
<tr><td>BLG 223 - BLG 223E</td><td>Data Structures</td>
<td>( BLG 102 MIN. DD Veya BLG 102E MIN. DD ) Veya BIL 104E MIN. BB</td><td></td></tr>
<tr><td>BLG 335E</td><td>Algorithms</td><td>BLG 223E MIN. DD Ve MAT 271E MIN. CC</td><td>60,00</td></tr>
</table>
"""


def branch_data() -> dict:
    return extract_branch_prerequisites(BRANCH_HTML, BRANCH_URL, "BLG")


def rule_result(code: str, children: list[str], *, unknown: bool = False) -> dict:
    return {
        "course_code": code,
        "available": not unknown,
        "prerequisite_status": "unknown" if unknown else "has_prerequisites" if children else "no_prerequisites",
        "prerequisites": [{"code": child, "type": "prerequisite_reference"} for child in children],
        "requirement_tree": {"type": "or", "operands": [{"type": "course", "code": child, "min_grade": "DD"} for child in children]},
    }


class PrerequisiteClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ObsPublicClient()

    def test_course_detail_returns_authoritative_references_and_full_or_tree(self) -> None:
        with patch.object(self.client, "get_branch_prerequisites", return_value=branch_data()) as read:
            with patch.object(self.client, "_get_html") as wrapper:
                result = self.client.get_prerequisite_detail("blg", "223e")
        read.assert_called_once_with("BLG")
        wrapper.assert_not_called()
        self.assertEqual(result["course_code"], "BLG 223E")
        self.assertEqual(result["prerequisite_status"], "has_prerequisites")
        self.assertEqual({r["code"] for r in result["prerequisites"]}, {"BLG 102", "BLG 102E", "BIL 104E"})
        self.assertEqual(result["requirement_tree"]["type"], "or")
        self.assertEqual(result["official_prerequisite"]["minimum_grades"]["BIL 104E"], "BB")
        self.assertIn("not a list of courses that are all mandatory", result["prerequisite_status_note"])
        self.assertEqual(result["prerequisite_source"], BRANCH_URL)

    def test_exact_course_lookup_does_not_confuse_department_id_with_course(self) -> None:
        with patch.object(self.client, "get_branch_prerequisites", return_value=branch_data()):
            result = self.client.get_prerequisites("BLG335E")
        self.assertEqual(result["course_code"], "BLG 335E")
        self.assertEqual(result["requirement_tree"]["type"], "and")
        self.assertEqual(result["credit_requirement"], 60.0)
        self.assertEqual({r["code"] for r in result["prerequisites"]}, {"BLG 223E", "MAT 271E"})

    def test_credit_only_four_digit_course_is_not_reported_prerequisite_free(self) -> None:
        rule = {"course_codes": ["CEN 4901E"], "requirement_tree": None, "credit_requirement": 95.0,
                "credit_requirement_text": "95,00", "expression": ""}
        with patch.object(self.client, "get_branch_prerequisites", return_value={"rules": {"CEN 4901E": rule}, "table_parsed": True, "url": BRANCH_URL}):
            result = self.client.get_prerequisites("CEN4901E")
        self.assertEqual(result["prerequisites"], [])
        self.assertEqual(result["prerequisite_status"], "has_prerequisites")
        self.assertEqual(result["credit_requirement"], 95.0)

    def test_course_absent_from_rules_requires_exact_catalog_identity(self) -> None:
        with patch.object(self.client, "get_branch_prerequisites", return_value=branch_data()):
            with patch.object(self.client, "search_courses", return_value=[{"code": "BLG 102E"}]) as catalog:
                result = self.client.get_prerequisites("BLG102E")
        catalog.assert_called_once_with("BLG 102E")
        self.assertEqual(result["prerequisite_status"], "no_prerequisites")
        self.assertTrue(result["available"])

    def test_nonexistent_or_wrong_language_course_does_not_gain_no_prerequisites(self) -> None:
        for rows in ([], [{"code": "BLG 9999"}]):
            with self.subTest(rows=rows):
                with patch.object(self.client, "get_branch_prerequisites", return_value=branch_data()):
                    with patch.object(self.client, "search_courses", return_value=rows):
                        result = self.client.get_prerequisites("BLG9999E")
                self.assertEqual(result["prerequisite_status"], "unknown")
                self.assertFalse(result["available"])

    def test_catalog_failure_does_not_gain_no_prerequisites(self) -> None:
        with patch.object(self.client, "get_branch_prerequisites", return_value=branch_data()):
            with patch.object(self.client, "search_courses", side_effect=ObsError("Unavailable")):
                result = self.client.get_prerequisites("BLG102E")
        self.assertEqual(result["prerequisite_status"], "unknown")

    def test_unreadable_branch_is_unknown_and_is_not_cached_as_empty(self) -> None:
        with patch.object(self.client, "get_branch_prerequisites", side_effect=[
            {"table_parsed": False, "rules": {}, "url": BRANCH_URL}, branch_data(),
        ]) as read:
            first = self.client.get_prerequisites("BLG223E")
            second = self.client.get_prerequisites("BLG223E")
        self.assertEqual(first["prerequisite_status"], "unknown")
        self.assertEqual(second["prerequisite_status"], "has_prerequisites")
        self.assertEqual(read.call_count, 2)

    def test_branch_failure_preserves_unknown_status(self) -> None:
        with patch.object(self.client, "get_branch_prerequisites", side_effect=ObsError("Unavailable")):
            result = self.client.get_prerequisites("BLG223E")
        self.assertEqual(result["prerequisite_status"], "unknown")
        self.assertIn("not evidence", result["prerequisite_status_note"])

    def test_invalid_course_input_is_rejected_before_network(self) -> None:
        with patch.object(self.client, "_get_html") as request:
            with self.assertRaises(ObsError):
                self.client.get_prerequisites("BLG ../../private")
            with self.assertRaises(ObsError):
                self.client.get_prerequisite_detail("BLG", "not-a-number")
        request.assert_not_called()

    def test_legacy_numeric_id_is_explicitly_branch_scoped(self) -> None:
        with patch.object(self.client, "_get_html", return_value=(BRANCH_HTML, BRANCH_URL)) as read:
            result = self.client.get_prerequisites(3)
        read.assert_called_once_with("/public/GenelTanimlamalar/OnsartAra", params={"DersBransKoduId": "3"})
        self.assertEqual(result["scope"], "branch")
        self.assertEqual(result["prerequisite_status"], "unknown")


class PostrequisiteClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ObsPublicClient()

    def test_reverse_lookup_scans_branches_once_and_keeps_complete_expressions(self) -> None:
        with patch.object(self.client, "_build_course_index", return_value={"blg": 3, "3": 3}):
            with patch.object(self.client, "get_branch_prerequisites", return_value=branch_data()) as read:
                result = self.client.get_postrequisites("BLG102E")
        read.assert_called_once_with("BLG")
        self.assertEqual([r["code"] for r in result["postrequisites"]], ["BLG 223", "BLG 223E"])
        self.assertEqual(result["postrequisites"][0]["requirement_tree"]["type"], "or")
        self.assertTrue(result["complete"])
        self.assertEqual(result["scanned_branches"], 1)

    def test_reverse_lookup_keeps_language_suffix_exact(self) -> None:
        with patch.object(self.client, "_build_course_index", return_value={"blg": 3}):
            with patch.object(self.client, "get_branch_prerequisites", return_value=branch_data()):
                result = self.client.get_postrequisites("BIL104")
        self.assertEqual(result["postrequisites"], [])
        self.assertEqual(result["postrequisite_status"], "no_postrequisites")

    def test_failed_branch_scan_is_incomplete_even_when_no_matches_were_found(self) -> None:
        with patch.object(self.client, "_build_course_index", return_value={"blg": 3, "mat": 7}):
            with patch.object(self.client, "get_branch_prerequisites", side_effect=[branch_data(), ObsError("Unavailable")]):
                result = self.client.get_postrequisites("FIZ101E")
        self.assertFalse(result["complete"])
        self.assertEqual(result["postrequisite_status"], "unknown")
        self.assertEqual(result["unknown_branches"], ["MAT"])

    def test_unreadable_table_is_incomplete(self) -> None:
        with patch.object(self.client, "_build_course_index", return_value={"blg": 3}):
            with patch.object(self.client, "get_branch_prerequisites", return_value={"table_parsed": False, "rules": {}}):
                result = self.client.get_postrequisites("BLG102E")
        self.assertFalse(result["complete"])
        self.assertEqual(result["postrequisite_status"], "unknown")

    def test_scan_limit_is_visible_and_does_not_prove_no_postrequisites(self) -> None:
        index = {f"branch_{i:03}": i for i in range(201)}
        with patch.object(self.client, "_build_course_index", return_value=index):
            with patch.object(self.client, "get_branch_prerequisites", return_value=branch_data()) as read:
                result = self.client.get_postrequisites("FIZ101E")
        self.assertEqual(read.call_count, 200)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["unscanned_branches"]), 1)
        self.assertEqual(result["postrequisite_status"], "unknown")


class PrerequisiteToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = NinovaMcpApp()
        self.public = Mock(spec=ObsPublicClient)
        self.app._obs_public = self.public
        self.public.resolve_course_code.side_effect = lambda code: {"code": code, "brans_kodu_id": 3}

    def test_both_directions_receive_full_course_code_and_preserve_rule_metadata(self) -> None:
        direct = rule_result("BLG 223E", ["BLG 102E"])
        self.public.get_prerequisites.return_value = direct
        self.public.get_postrequisites.return_value = {"postrequisites": [], "complete": False, "postrequisite_status": "unknown", "unknown_branches": ["MAT"]}
        result = self.app.obs_get_course_prerequisites("BLG223E", direction="both")
        self.public.get_prerequisites.assert_called_once_with("BLG 223E")
        self.public.get_postrequisites.assert_called_once_with("BLG 223E")
        self.assertEqual(result["requirement_tree"], direct["requirement_tree"])
        self.assertFalse(result["postreq_complete"])
        self.assertEqual(result["postrequisite_status"], "unknown")

    def test_prerequisite_chain_obeys_depth_and_retains_unknown_nodes(self) -> None:
        rules = {"BLG 335E": rule_result("BLG 335E", ["BLG 223E", "MAT 271E"]),
                 "BLG 223E": rule_result("BLG 223E", ["BLG 102E"]),
                 "MAT 271E": rule_result("MAT 271E", [], unknown=True)}
        self.public.get_prerequisites.side_effect = lambda code: rules[code]
        result = self.app.obs_get_course_prerequisites("BLG335E", max_depth=2)
        chain = result["prerequisite_chain"]
        self.assertIn("BLG 102E", chain["nodes"])
        self.assertEqual(chain["unknown_courses"], ["MAT 271E"])
        self.assertFalse(chain["complete"])
        self.assertEqual(chain["requirements"]["BLG 335E"]["type"], "or")
        self.assertNotIn("BLG 102E", [c.args[0] for c in self.public.get_prerequisites.call_args_list])
        self.public.get_postrequisites.assert_not_called()

    def test_postrequisite_chain_uses_full_codes_and_preserves_partial_coverage(self) -> None:
        tree = {"type": "course", "code": "BLG 102E", "min_grade": "DD"}
        rules = {"BLG 102E": {"postrequisites": [{"code": "BLG 223E", "requirement_tree": tree}], "complete": True},
                 "BLG 223E": {"postrequisites": [], "complete": False}}
        self.public.get_postrequisites.side_effect = lambda code: rules[code]
        result = self.app.obs_get_course_prerequisites("BLG102E", direction="postrequisites", max_depth=2)
        chain = result["postrequisite_chain"]
        self.assertFalse(chain["complete"])
        self.assertEqual(chain["unknown_courses"], ["BLG 223E"])
        self.assertEqual(chain["edges"][0]["requirement_tree"], tree)
        self.public.get_prerequisites.assert_not_called()

    def test_direction_and_course_validation_happen_before_network(self) -> None:
        with self.assertRaises(ObsError):
            self.app.obs_get_course_prerequisites("BLG223E", direction="invalid")
        with self.assertRaises(ObsError):
            self.app.obs_get_course_prerequisites("BLG")
        self.public.resolve_course_code.assert_not_called()

    def test_fuzzy_resolver_cannot_replace_requested_language_variant(self) -> None:
        self.public.resolve_course_code.side_effect = None
        self.public.resolve_course_code.return_value = {
            "code": "BLG 223", "name": "A different course variant", "source": "search_fuzzy",
        }
        rules = {"BLG 223E": rule_result("BLG 223E", ["BLG 102E"]),
                 "BLG 102E": rule_result("BLG 102E", [])}
        self.public.get_prerequisites.side_effect = lambda code: rules[code]
        result = self.app.obs_get_course_prerequisites("BLG223E", max_depth=2)
        self.assertEqual(result["course"]["code"], "BLG 223E")
        self.assertNotIn("name", result["course"])
        self.assertEqual(result["prerequisite_chain"]["nodes"], ["BLG 102E", "BLG 223E"])
        self.assertTrue(result["prerequisite_chain"]["complete"])

    def test_depth_cutoff_is_explicit_for_both_chain_directions(self) -> None:
        codes = ["BLG 335E", "BLG 223E", "BLG 102E"]
        for direction in ("prerequisites", "postrequisites"):
            with self.subTest(direction=direction):
                self.public.reset_mock()
                if direction == "prerequisites":
                    rules = {codes[index]: rule_result(codes[index], [codes[index + 1]]) for index in range(2)}
                    self.public.get_prerequisites.side_effect = lambda code: rules[code]
                else:
                    rules = {codes[index]: {"postrequisites": [{"code": codes[index + 1]}], "complete": True} for index in range(2)}
                    self.public.get_postrequisites.side_effect = lambda code: rules[code]
                result = self.app.obs_get_course_prerequisites(codes[0], direction=direction, max_depth=2)
                chain = result["prerequisite_chain" if direction == "prerequisites" else "postrequisite_chain"]
                self.assertFalse(chain["complete"])
                self.assertTrue(chain["complete_within_requested_depth"])
                self.assertTrue(chain["depth_limited"])
                self.assertEqual(chain["unexpanded_courses"], [codes[2]])
                self.assertEqual(chain["unknown_courses"], [])

    def test_cycle_at_depth_limit_does_not_count_an_expanded_node_as_truncated(self) -> None:
        rules = {"BLG 223E": rule_result("BLG 223E", ["BLG 102E"]),
                 "BLG 102E": rule_result("BLG 102E", ["BLG 223E"])}
        self.public.get_prerequisites.side_effect = lambda code: rules[code]
        result = self.app.obs_get_course_prerequisites("BLG223E", max_depth=2)
        chain = result["prerequisite_chain"]
        self.assertTrue(chain["complete"])
        self.assertFalse(chain["depth_limited"])
        self.assertEqual(chain["unexpanded_courses"], [])


if __name__ == "__main__":
    unittest.main()
