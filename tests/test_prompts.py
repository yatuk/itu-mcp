from __future__ import annotations

import asyncio
import json
import unittest

from ninova_mcp.prompts import PROMPT_NAMES, PROMPTS
from ninova_mcp.resources import RESOURCE_URIS, grade_scale, program_types
from ninova_mcp.server import build_stdio_server


def _server():
    """Build the stdio server once per test that needs the live MCP surface."""
    return build_stdio_server()


class PromptMetadataTests(unittest.TestCase):
    def test_all_prompts_have_metadata(self) -> None:
        for meta in PROMPTS:
            self.assertTrue(meta["name"])
            self.assertTrue(meta["title"])
            self.assertTrue(meta["description"])
            self.assertTrue(callable(meta["builder"]))

    def test_prompt_names_unique(self) -> None:
        self.assertEqual(len(PROMPT_NAMES), len(set(PROMPT_NAMES)))

    def test_expected_prompts_registered(self) -> None:
        for name in (
            "weekly_briefing",
            "plan_next_term",
            "check_course_eligibility",
            "research_course",
            "gpa_scenario",
        ):
            self.assertIn(name, PROMPT_NAMES)


class PromptRenderTests(unittest.TestCase):
    """Render prompts through the real FastMCP surface, not the raw builders."""

    def test_list_prompts_matches_metadata(self) -> None:
        prompts = asyncio.run(_server().list_prompts())
        self.assertEqual(sorted(p.name for p in prompts), sorted(PROMPT_NAMES))

    def test_required_and_optional_arguments(self) -> None:
        prompts = {p.name: p for p in asyncio.run(_server().list_prompts())}

        # No-argument prompt.
        self.assertEqual(prompts["plan_next_term"].arguments or [], [])

        # Required argument.
        eligibility = {a.name: a.required for a in prompts["check_course_eligibility"].arguments}
        self.assertEqual(eligibility, {"course_code": True})

        # Optional argument with a default.
        briefing = {a.name: a.required for a in prompts["weekly_briefing"].arguments}
        self.assertEqual(briefing, {"days": False})

    def test_course_code_is_normalised_into_text(self) -> None:
        result = asyncio.run(
            _server().get_prompt("check_course_eligibility", {"course_code": "cen 4901e"})
        )
        text = result.messages[0].content.text
        self.assertIn("CEN 4901E", text)

    def test_eligibility_prompt_states_the_unknown_distinction(self) -> None:
        """The trap this prompt exists to prevent must survive rewording."""
        result = asyncio.run(
            _server().get_prompt("check_course_eligibility", {"course_code": "BLG 223E"})
        )
        text = result.messages[0].content.text
        self.assertIn("no_prerequisites", text)
        self.assertIn("unknown", text)
        self.assertIn("cross_check", text)
        self.assertIn("archive_seasonality", text)

    def test_research_prompt_states_coverage_rule(self) -> None:
        result = asyncio.run(_server().get_prompt("research_course", {"course": "BLG 102E"}))
        text = result.messages[0].content.text
        self.assertIn("coverage", text)
        self.assertIn("term_missing", text)

    def test_numeric_argument_arrives_as_string(self) -> None:
        """MCP sends prompt arguments as strings; the builder must parse them."""
        result = asyncio.run(_server().get_prompt("weekly_briefing", {"days": "7"}))
        text = result.messages[0].content.text
        self.assertIn("days=7", text)

    def test_invalid_number_falls_back_to_default(self) -> None:
        result = asyncio.run(_server().get_prompt("weekly_briefing", {"days": "abc"}))
        self.assertIn("days=14", result.messages[0].content.text)

    def test_optional_prompt_renders_without_arguments(self) -> None:
        result = asyncio.run(_server().get_prompt("gpa_scenario", {}))
        self.assertTrue(result.messages[0].content.text.strip())

    def test_unknown_prompt_raises(self) -> None:
        with self.assertRaises(Exception):
            asyncio.run(_server().get_prompt("no_such_prompt", {}))


class ResourceTests(unittest.TestCase):
    def test_list_resources_matches_metadata(self) -> None:
        resources = asyncio.run(_server().list_resources())
        self.assertEqual(sorted(str(r.uri) for r in resources), sorted(RESOURCE_URIS))

    def test_resources_are_concrete_not_templates(self) -> None:
        """Both resources take no parameters, so nothing should be a template."""
        self.assertEqual(asyncio.run(_server().list_resource_templates()), [])

    def test_grade_scale_resource_reads_as_json(self) -> None:
        contents = list(asyncio.run(_server().read_resource("itu://reference/grade-scale")))
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0].mime_type, "application/json")
        data = json.loads(contents[0].content)
        self.assertEqual(data["coefficients"]["AA"], 4.0)
        self.assertEqual(data["coefficients"]["VF"], 0.0)

    def test_grade_scale_separates_counted_from_excluded(self) -> None:
        data = grade_scale()
        self.assertIn("VF", data["coefficients"])
        self.assertIn("GE", data["excluded_from_gpa"])
        self.assertNotIn("GE", data["coefficients"])
        self.assertEqual(data["failing_grades"], ["FF", "VF"])

    def test_program_types_resource_reads_as_json(self) -> None:
        contents = list(asyncio.run(_server().read_resource("itu://reference/program-types")))
        data = json.loads(contents[0].content)
        self.assertIn("LS", data["canonical_values"])
        self.assertEqual(data["default"], "LS")

    def test_program_types_aliases_match_client(self) -> None:
        """The resource must not drift from the map the client actually uses."""
        from ninova_mcp.obs_client import ObsPublicClient

        data = program_types()
        self.assertEqual(data["aliases"], dict(ObsPublicClient.PROGRAM_TYPE_MAP))
        self.assertEqual(
            sorted(data["canonical_values"]),
            sorted(set(ObsPublicClient.PROGRAM_TYPE_MAP.values())),
        )

    def test_every_canonical_value_has_a_description(self) -> None:
        data = program_types()
        for value in data["canonical_values"]:
            self.assertIn(value, data["descriptions"])

    def test_unknown_resource_raises(self) -> None:
        with self.assertRaises(Exception):
            list(asyncio.run(_server().read_resource("itu://reference/nope")))


if __name__ == "__main__":
    unittest.main()
