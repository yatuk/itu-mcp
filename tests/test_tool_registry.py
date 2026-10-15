"""Check required public tools, shared metadata and callable app methods.

The independent tool catalogue also catches a tool removed from both the
registry and implementation. Signature and registration checks validate the
shared MCP contract.
"""

from __future__ import annotations

import inspect
import unittest

from ninova_mcp.server import (
    LOCAL_TOOL_NAMES,
    MAIL_TOOL_NAMES,
    TOOLS,
    NinovaMcpApp,
    register_tools,
)

# Fixed public expectations remain independent of the production registry.
EXPECTED_TOOL_GROUPS = {
    "obs": (
        "obs_auth_status",
        "obs_get_profile",
        "obs_list_registered_courses",
        "obs_get_course_grades",
        "obs_download_transcript",
    ),
    "public_obs": (
        "obs_search_courses",
        "obs_get_course_prerequisites",
    ),
    "schedule": (
        "get_public_course_schedule",
        "get_public_course_prerequisites",
    ),
    "public_services": (
        "build_degree_plan",
        "calculate_target_gpa",
        "explain_course_eligibility",
        "find_empty_classrooms",
        "find_open_course_sections",
        "get_itu_announcements",
        "get_personal_exam_calendar",
        "get_public_exam_schedule",
        "get_shuttle_schedule",
        "get_sports_facility_hours",
        "library_check_availability",
        "library_get_account",
        "library_get_item",
        "library_list_loans",
        "library_renew_loan",
        "library_reserve_item",
        "library_search",
        "list_degree_faculties",
        "list_degree_programs",
        "search_campus_locations",
        "search_itu_directory",
    ),
    "archive": (
        "archive_list_terms",
        "archive_course_history",
        "archive_who_taught",
        "archive_instructor_courses",
        "archive_term_sections",
        "archive_fill_rate",
        "archive_search_courses",
        "archive_list_branches",
        "archive_compare_terms",
        "plan_remaining_courses",
    ),
}


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
    """Required tool names and real app methods must remain available."""

    def test_expected_tool_groups_remain_registered_and_callable(self) -> None:
        app = NinovaMcpApp()
        registered = set(LOCAL_TOOL_NAMES)
        for group, names in EXPECTED_TOOL_GROUPS.items():
            for name in names:
                with self.subTest(group=group, tool=name):
                    self.assertIn(name, registered)
                    self.assertTrue(callable(getattr(app, name)))

    def test_sent_mail_methods_forward_mailbox_to_client(self) -> None:
        from unittest.mock import Mock

        listing = {"mailbox": "SENT", "messages": [{"uid": "102"}]}
        message = {"mailbox": "SENT", "uid": "102", "body_text": "Sent body"}
        attachment = {"kind": "pdf_text", "text": "Attachment body"}
        app = NinovaMcpApp()
        app._mail = Mock()
        app._mail.list_sent.return_value = listing
        app._mail.get_message.return_value = message
        app._mail.get_attachment.return_value = attachment
        self.assertIs(app.mail_list_sent(since_days=0, limit=1), listing)
        app._mail.list_sent.assert_called_once_with(since_days=0, limit=1)
        self.assertIs(app.mail_get_message("102", mailbox="SENT"), message)
        app._mail.get_message.assert_called_once_with("102", max_chars=12000, mailbox="SENT")
        self.assertIs(app.mail_get_attachment("102", "2", mailbox="SENT"), attachment)
        app._mail.get_attachment.assert_called_once_with("102", "2", max_chars=50000, mailbox="SENT")

    def test_every_tool_name_is_a_callable_app_method(self) -> None:
        app = NinovaMcpApp()
        for name in LOCAL_TOOL_NAMES:
            with self.subTest(tool=name):
                self.assertTrue(hasattr(NinovaMcpApp, name), f"no NinovaMcpApp.{name}")
                self.assertTrue(callable(getattr(app, name)))

    def test_registration_includes_safety_annotations(self) -> None:
        class FakeMcp:
            def __init__(self) -> None:
                self.registered: dict[str, object] = {}

            def add_tool(self, fn: object, **kwargs: object) -> None:
                del fn
                self.registered[str(kwargs["name"])] = kwargs["annotations"]

        fake = FakeMcp()
        register_tools(fake, NinovaMcpApp(), ["auth_status", "sync_all_courses", "submit_assignment"])

        read = fake.registered["auth_status"]
        self.assertTrue(read.readOnlyHint)
        self.assertFalse(read.destructiveHint)
        self.assertTrue(read.idempotentHint)
        self.assertTrue(read.openWorldHint)

        stateful = fake.registered["sync_all_courses"]
        self.assertFalse(stateful.readOnlyHint)
        self.assertFalse(stateful.destructiveHint)
        self.assertFalse(stateful.idempotentHint)

        destructive = fake.registered["submit_assignment"]
        self.assertFalse(destructive.readOnlyHint)
        self.assertTrue(destructive.destructiveHint)

    def test_tool_without_metadata_is_refused_before_anything_is_registered(self) -> None:
        class FakeMcp:
            def __init__(self) -> None:
                self.registered: list[str] = []

            def add_tool(self, fn: object, **kwargs: object) -> None:
                del fn
                self.registered.append(str(kwargs["name"]))

        fake = FakeMcp()
        # invalidate_caches is a real app method, but it is not a tool.
        with self.assertRaisesRegex(ValueError, "no metadata in TOOLS: invalidate_caches"):
            register_tools(fake, NinovaMcpApp(), ["auth_status", "invalidate_caches"])
        self.assertEqual(fake.registered, [])

    def test_attachment_tool_uses_mixed_mcp_content(self) -> None:
        class FakeMcp:
            def __init__(self) -> None:
                self.kwargs: dict[str, object] = {}

            def add_tool(self, fn: object, **kwargs: object) -> None:
                del fn
                self.kwargs = kwargs

        fake = FakeMcp()
        register_tools(fake, NinovaMcpApp(), ["mail_get_attachment"])
        self.assertFalse(fake.kwargs["structured_output"])

    def test_all_mail_tools_are_annotated_read_only(self) -> None:
        class FakeMcp:
            def __init__(self) -> None:
                self.registered: dict[str, object] = {}

            def add_tool(self, fn: object, **kwargs: object) -> None:
                del fn
                self.registered[str(kwargs["name"])] = kwargs["annotations"]

        fake = FakeMcp()
        register_tools(fake, NinovaMcpApp(), sorted(MAIL_TOOL_NAMES))
        for tool_annotations in fake.registered.values():
            self.assertTrue(tool_annotations.readOnlyHint)
            self.assertFalse(tool_annotations.destructiveHint)
            self.assertTrue(tool_annotations.idempotentHint)

    def test_attachment_image_becomes_text_and_image_content(self) -> None:
        from mcp.server.fastmcp.utilities.types import Image
        from mcp.types import TextContent

        class FakeMail:
            def get_attachment(self, uid: str, part_id: str, *, max_chars: int, mailbox: str):
                del uid, part_id, max_chars, mailbox
                return {
                    "kind": "image",
                    "content_type": "image/png",
                    "filename": "diagram.png",
                    "untrusted_external_content": True,
                    "image_bytes": b"png bytes",
                }

        app = NinovaMcpApp()
        app._mail = FakeMail()  # type: ignore[assignment]
        result = app.mail_get_attachment("1", "2")
        self.assertIsInstance(result[0], TextContent)
        self.assertIsInstance(result[1], Image)
        self.assertEqual(result[1].to_image_content().mimeType, "image/png")


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
