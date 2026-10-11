"""CRNs, course numbers and term codes arrive as JSON numbers as often as strings.

Every tool parameter with one of the names below must accept an integer. The
check goes through pydantic the way FastMCP validates a call, so a signature
that says ``str`` fails here exactly as it fails for a client.

Tools whose signature lives outside the public-tools modules are listed in
``KNOWN_OUTSIDE`` with the change they need. They are expected failures: when
one of them is fixed the run reports an unexpected success, and its entry
must be removed.
"""

from __future__ import annotations

import inspect
import typing
import unittest
from typing import Any
from unittest.mock import MagicMock

from pydantic import TypeAdapter, ValidationError

from ninova_mcp.server import NinovaMcpApp
from ninova_mcp.tool_registry import TOOLS

NUMERIC_IDENTIFIER_PARAMS = ("crn", "crns", "ders_no", "semester", "term_code", "year")

KNOWN_OUTSIDE: dict[tuple[str, str], str] = {
    ("obs_validate_registration_plan", "crns"): "app_registration.py: strict on purpose, validated before any login (see test_registration_tools)",
    ("obs_save_registration_draft", "crns"): "app_registration.py: crns is strict on purpose for the write tool; widen only with the owner",
}


def _cases() -> list[tuple[str, str, Any]]:
    cases = []
    for tool in TOOLS:
        method = getattr(NinovaMcpApp, tool["name"])
        hints = typing.get_type_hints(method)
        for name in inspect.signature(method).parameters:
            if name in NUMERIC_IDENTIFIER_PARAMS:
                cases.append((tool["name"], name, hints[name]))
    return cases


def _accepts_integer(name: str, annotation: Any) -> bool:
    value: Any = [12513, 12469] if name == "crns" else 12513
    try:
        TypeAdapter(annotation).validate_python(value)
    except ValidationError:
        return False
    return True


class NumericIdentifierParameterTests(unittest.TestCase):
    """One generated test per (tool, parameter); see the module docstring."""

    def test_the_parameter_list_is_not_empty(self) -> None:
        found = {(tool, name) for tool, name, _ in _cases()}
        self.assertIn(("get_public_course_schedule", "crn"), found)
        self.assertIn(("get_public_course_prerequisites", "ders_no"), found)
        self.assertGreaterEqual(len(found), 15)

    def test_known_outside_entries_still_name_real_parameters(self) -> None:
        found = {(tool, name) for tool, name, _ in _cases()}
        self.assertEqual(set(KNOWN_OUTSIDE) - found, set())


def _make_test(tool: str, name: str, annotation: Any) -> Any:
    def test(self: unittest.TestCase) -> None:
        self.assertTrue(
            _accepts_integer(name, annotation),
            f"{tool}({name}=...) rejects an integer; its annotation is {annotation!r}",
        )

    reason = KNOWN_OUTSIDE.get((tool, name))
    test.__doc__ = f"{tool}.{name} accepts an integer" + (f" (expected failure: {reason})" if reason else "")
    return unittest.expectedFailure(test) if reason else test


for _tool, _name, _annotation in _cases():
    setattr(NumericIdentifierParameterTests, f"test_{_tool}__{_name}_accepts_integer", _make_test(_tool, _name, _annotation))


class NumericInputIsUsedTests(unittest.TestCase):
    """The widened public tools convert the number instead of passing it on raw."""

    def setUp(self) -> None:
        self.app = NinovaMcpApp()
        self.app._obs_public = MagicMock()

    def test_schedule_crn_as_number(self) -> None:
        self.app._obs_public.get_course_schedule_by_crn.return_value = {"courses": [], "count": 0}
        self.app.get_public_course_schedule("LS", "BLG", crn=12513)
        self.app._obs_public.get_course_schedule_by_crn.assert_called_once_with("LS", "BLG", "12513")

    def test_prerequisite_course_number_as_number(self) -> None:
        self.app._obs_public.get_prerequisite_detail.return_value = {}
        self.app.get_public_course_prerequisites("BLG", 4901)
        self.app._obs_public.get_prerequisite_detail.assert_called_once_with("BLG", "4901")

    def test_published_schema_accepts_both_types(self) -> None:
        import asyncio

        from ninova_mcp.server import build_stdio_server

        tools = {tool.name: tool for tool in asyncio.run(build_stdio_server(self.app).list_tools())}
        crn = tools["get_public_course_schedule"].inputSchema["properties"]["crn"]
        self.assertEqual({item.get("type") for item in crn["anyOf"]}, {"string", "integer", "null"})
        ders_no = tools["get_public_course_prerequisites"].inputSchema["properties"]["ders_no"]
        self.assertEqual({item.get("type") for item in ders_no["anyOf"]}, {"string", "integer"})


if __name__ == "__main__":
    unittest.main()
