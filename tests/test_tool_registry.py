"""Validates the whole TOOLS registry at once, not just hand-picked subsets.

Existing test files each spot-check a handful of tool names against
LOCAL_TOOL_NAMES/hasattr — none of them iterate the full registry, and none
check that a tool's declared inputSchema actually matches its method's real
signature. This file exists to catch exactly the kind of drift a manual edit
can introduce silently: a schema that lists a parameter the method doesn't
have, a required parameter the schema forgot to mark required, or a stale
tool entry left behind after its method was renamed or removed.

No JSON-Schema library dependency is added — the shape this codebase's
schemas actually use (flat "object" with "properties"/"required") is simple
enough to check by hand.
"""

from __future__ import annotations

import inspect
import unittest

from ninova_mcp.server import LOCAL_TOOL_NAMES, TOOLS, NinovaMcpApp

_EMPTY = inspect.Parameter.empty
_VARARGS_KINDS = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)


def _bound_params(method: object) -> dict[str, inspect.Parameter]:
    """Real parameters of a bound method, excluding *args/**kwargs."""
    return {
        name: param
        for name, param in inspect.signature(method).parameters.items()
        if param.kind not in _VARARGS_KINDS
    }


class ToolMetadataShapeTests(unittest.TestCase):
    """Structural checks on every TOOLS entry, independent of NinovaMcpApp."""

    def test_no_duplicate_names(self) -> None:
        self.assertEqual(len(TOOLS), len(LOCAL_TOOL_NAMES))
        self.assertEqual(len(LOCAL_TOOL_NAMES), len(set(LOCAL_TOOL_NAMES)))

    def test_every_tool_has_nonempty_description(self) -> None:
        empty = [t["name"] for t in TOOLS if not str(t.get("description") or "").strip()]
        self.assertEqual(empty, [], f"tools with empty description: {empty}")

    def test_every_tool_has_nonempty_title(self) -> None:
        empty = [t["name"] for t in TOOLS if not str(t.get("title") or "").strip()]
        self.assertEqual(empty, [], f"tools with empty title: {empty}")

    def test_every_input_schema_is_object_shaped(self) -> None:
        for tool in TOOLS:
            with self.subTest(tool=tool["name"]):
                schema = tool.get("inputSchema")
                self.assertIsInstance(schema, dict)
                self.assertEqual(schema.get("type"), "object")
                self.assertIsInstance(schema.get("properties"), dict)

    def test_required_names_exist_in_properties(self) -> None:
        for tool in TOOLS:
            with self.subTest(tool=tool["name"]):
                schema = tool["inputSchema"]
                required = schema.get("required", [])
                self.assertIsInstance(required, list)
                properties = schema["properties"]
                for req_name in required:
                    self.assertIn(
                        req_name, properties,
                        f"{tool['name']}: 'required' names {req_name!r} which isn't in 'properties'",
                    )

    def test_every_property_has_a_type_or_enum(self) -> None:
        for tool in TOOLS:
            schema = tool["inputSchema"]
            for prop_name, prop_schema in schema["properties"].items():
                with self.subTest(tool=tool["name"], param=prop_name):
                    self.assertIsInstance(prop_schema, dict)
                    self.assertTrue(
                        {"type", "enum", "anyOf", "oneOf"} & prop_schema.keys(),
                        f"{tool['name']}.{prop_name} has no type/enum/anyOf/oneOf",
                    )


class ToolRegistrationTests(unittest.TestCase):
    """Every declared tool must resolve to a real, callable NinovaMcpApp method."""

    def test_every_tool_name_is_a_callable_app_method(self) -> None:
        app = NinovaMcpApp()
        for name in LOCAL_TOOL_NAMES:
            with self.subTest(tool=name):
                self.assertTrue(hasattr(NinovaMcpApp, name), f"no NinovaMcpApp.{name}")
                self.assertTrue(callable(getattr(app, name)))


class ToolSignatureConsistencyTests(unittest.TestCase):
    """The schema and the real method signature must describe the same call.

    This is the check that would have caught drift like a schema parameter
    that no longer exists on the method, or a required parameter the schema
    forgot to list — neither is caught by the hasattr-only checks scattered
    across the other test files.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = NinovaMcpApp()

    def test_schema_properties_match_method_parameters(self) -> None:
        for tool in TOOLS:
            name = tool["name"]
            with self.subTest(tool=name):
                method = getattr(self.app, name)
                params = _bound_params(method)
                schema_props = set(tool["inputSchema"]["properties"])
                self.assertEqual(
                    schema_props,
                    set(params),
                    f"{name}: inputSchema properties {schema_props} != method params {set(params)}",
                )

    def test_schema_required_matches_method_required(self) -> None:
        for tool in TOOLS:
            name = tool["name"]
            with self.subTest(tool=name):
                method = getattr(self.app, name)
                params = _bound_params(method)
                schema_required = set(tool["inputSchema"].get("required", []))
                method_required = {
                    pname for pname, p in params.items() if p.default is _EMPTY
                }
                self.assertEqual(
                    schema_required,
                    method_required,
                    f"{name}: inputSchema required {schema_required} != "
                    f"method required (no-default) params {method_required}",
                )


if __name__ == "__main__":
    unittest.main()
