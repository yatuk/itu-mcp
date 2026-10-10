"""Guard the draft-write boundary with a fake OBS client and no network."""
from __future__ import annotations

import asyncio
import copy
import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from ninova_mcp.obs_client import ObsError
from ninova_mcp.server import NinovaMcpApp, TOOLS, register_tools


class FakeObs:
    def __init__(self):
        self.writes = []
        self.reads = 0
        self.result = {"status": "saved", "saved": True, "readback_status": "matched"}

    def save_registration_draft(self, crns):
        self.writes.append(crns)
        return copy.deepcopy(self.result)

    def get_registration_draft(self):
        self.reads += 1
        return {"read_number": self.reads}


class DraftWriteToolTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="itu-draft-wrapper-"))
        stack.enter_context(patch.dict(os.environ, {
            "NINOVA_STATE_DIR": directory,
            "NINOVA_OBS_REGISTRATION_WRITES": "1",
        }))
        stack.enter_context(patch("ninova_mcp.server.load_ninova_env"))
        # Detect accidental HTTP and lower-level connections, not merely a
        # missing mock. There are no local network dependencies in these tests.
        for target in ("requests.sessions.Session.request", "socket.socket.connect", "socket.getaddrinfo"):
            stack.enter_context(patch(target, side_effect=AssertionError("Network forbidden in draft wrapper test")))
        self.app = NinovaMcpApp()
        self.obs = FakeObs()
        self.app._obs = self.obs

    def test_explicit_true_is_required_before_client_access(self):
        for value in (False, None, 0, 1, "true", "false", [], {}):
            with self.subTest(confirm=value):
                with self.assertRaisesRegex(ObsError, "explicit confirmation"):
                    self.app.obs_save_registration_draft(["10001"], value)
        self.assertEqual(self.obs.writes, [])

    def test_write_switch_is_default_off_and_checked_on_every_call(self):
        for value in (None, "", "0", "false", "true", "yes", "on", " 1 "):
            with self.subTest(value=value):
                if value is None:
                    os.environ.pop("NINOVA_OBS_REGISTRATION_WRITES", None)
                else:
                    os.environ["NINOVA_OBS_REGISTRATION_WRITES"] = value
                with self.assertRaisesRegex(ObsError, "disabled"):
                    self.app.obs_save_registration_draft(["10001"], True)
        self.assertEqual(self.obs.writes, [])

    def test_invalid_crns_never_reach_the_client(self):
        invalid = (
            [], ["10001", "10001"], [str(10001 + i) for i in range(13)],
            ["123"], ["123456"], ["10001\n"], ["１２３４５"], [10001],
            [True], [None], "10001", ("10001",), ["10001", "abcde"],
        )
        for crns in invalid:
            with self.subTest(crns=crns):
                with self.assertRaises(ObsError):
                    self.app.obs_save_registration_draft(crns, True)
        self.assertEqual(self.obs.writes, [])

    def test_disabled_call_does_not_construct_authenticated_client(self):
        self.app._obs = None
        os.environ.pop("NINOVA_OBS_REGISTRATION_WRITES")
        with patch("ninova_mcp.server.ObsClient", side_effect=AssertionError("Client must not be created")):
            with self.assertRaisesRegex(ObsError, "disabled"):
                self.app.obs_save_registration_draft(["10001"], True)

    def test_invalid_call_does_not_construct_authenticated_client(self):
        self.app._obs = None
        with patch("ninova_mcp.server.ObsClient", side_effect=AssertionError("Client must not be created")):
            with self.assertRaisesRegex(ObsError, "Duplicate CRNs"):
                self.app.obs_save_registration_draft(["10001", "10001"], True)

    def test_confirmed_write_preserves_exact_crns_order_and_result(self):
        original = ["10002", "10001", "10003"]
        result = self.app.obs_save_registration_draft(original, True)
        self.assertEqual(self.obs.writes, [original])
        self.assertIsNot(self.obs.writes[0], original)
        self.assertEqual(result, self.obs.result)
        self.assertEqual(self.obs.reads, 0)

    def test_rejection_and_uncertainty_are_not_promoted_to_success(self):
        for status, saved in (("rejected", False), ("uncertain", None)):
            self.obs.result = {"status": status, "saved": saved, "readback_status": "unavailable"}
            with self.subTest(status=status):
                self.assertEqual(self.app.obs_save_registration_draft(["10001"], True), self.obs.result)

    def test_client_error_is_not_retried(self):
        with patch.object(self.obs, "save_registration_draft", side_effect=ObsError("Save failed")) as save:
            with self.assertRaisesRegex(ObsError, "Save failed"):
                self.app.obs_save_registration_draft(["10001"], True)
            save.assert_called_once_with(["10001"])

    def test_draft_read_remains_uncached_and_does_not_write(self):
        self.assertEqual(self.app.obs_get_registration_draft(), {"read_number": 1})
        self.assertEqual(self.app.obs_get_registration_draft(), {"read_number": 2})
        self.assertEqual(self.obs.writes, [])

    def mcp(self):
        server = FastMCP("draft-write-test")
        register_tools(server, self.app, ["obs_save_registration_draft"])
        return server

    def test_actual_sdk_schema_requires_confirmation_and_bounded_crns(self):
        tool = asyncio.run(self.mcp().list_tools())[0]
        schema = tool.inputSchema
        self.assertEqual(set(schema["required"]), {"crns", "confirm"})
        self.assertEqual(schema["properties"]["confirm"]["type"], "boolean")
        self.assertNotIn("default", schema["properties"]["confirm"])
        crns = schema["properties"]["crns"]
        self.assertEqual(crns["minItems"], 1)
        self.assertEqual(crns["maxItems"], 12)
        self.assertTrue(crns["uniqueItems"])
        self.assertEqual(crns["items"]["pattern"], r"^[0-9]{4,5}$")
        self.assertFalse(tool.annotations.readOnlyHint)
        self.assertTrue(tool.annotations.destructiveHint)
        self.assertFalse(tool.annotations.idempotentHint)
        self.assertTrue(tool.annotations.openWorldHint)
        declared = next(item for item in TOOLS if item["name"] == tool.name)
        self.assertEqual(declared["inputSchema"]["required"], schema["required"])

    def test_sdk_does_not_coerce_truthy_values_into_confirmation(self):
        server = self.mcp()
        for value in (1, 0, "true", "false", "yes", None, False):
            with self.subTest(confirm=value):
                with self.assertRaises(ToolError):
                    asyncio.run(server.call_tool("obs_save_registration_draft", {"crns": ["10001"], "confirm": value}))
        with self.assertRaises(ToolError):
            asyncio.run(server.call_tool("obs_save_registration_draft", {"crns": ["10001"]}))
        self.assertEqual(self.obs.writes, [])

    def test_write_registration_preserves_existing_read_output_schema(self):
        server = FastMCP("draft-schema-compatibility")
        register_tools(server, self.app, ["auth_status", "obs_save_registration_draft"])
        tools = {item.name: item for item in asyncio.run(server.list_tools())}
        self.assertEqual(tools["auth_status"].outputSchema, {
            "properties": {"result": {"additionalProperties": True, "title": "Result", "type": "object"}},
            "required": ["result"], "title": "auth_statusOutput", "type": "object",
        })

    def test_sdk_dispatch_retains_guard_after_discovery(self):
        server = self.mcp()
        asyncio.run(server.list_tools())
        os.environ.pop("NINOVA_OBS_REGISTRATION_WRITES")
        with self.assertRaisesRegex(ToolError, "disabled"):
            asyncio.run(server.call_tool("obs_save_registration_draft", {"crns": ["10001"], "confirm": True}))
        self.assertEqual(self.obs.writes, [])

    def test_sdk_calls_client_once_with_confirmed_crns(self):
        server = self.mcp()
        asyncio.run(server.call_tool("obs_save_registration_draft", {"crns": ["10002", "10001"], "confirm": True}))
        self.assertEqual(self.obs.writes, [["10002", "10001"]])


if __name__ == "__main__":
    unittest.main()
