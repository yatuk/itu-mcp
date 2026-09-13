"""Exercise registration tool contracts through the official MCP stdio client."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION_TOOLS = (
    "obs_get_registration_draft",
    "obs_get_elective_group",
    "obs_validate_registration_plan",
)
OFFLINE_BOOTSTRAP = """
import requests

def deny_network(*args, **kwargs):
    raise AssertionError("Offline protocol test attempted an HTTP request.")

requests.sessions.Session.request = deny_network
from ninova_mcp.server import main
main()
"""


async def exchange() -> dict:
    with tempfile.TemporaryDirectory(prefix="itu-registration-protocol-") as directory:
        environment = {key: value for key, value in os.environ.items() if not key.startswith("NINOVA_")}
        environment.update({
            "PYTHONPATH": str(ROOT / "src"),
            "NINOVA_ENV_FILE": str(Path(directory) / "missing.env"),
            "NINOVA_STATE_DIR": str(Path(directory) / "state"),
            "NINOVA_SESSION_PERSIST": "0",
            "NINOVA_USERNAME": "",
            "NINOVA_PASSWORD": "",
            "REGISTRATION_TEST_SECRET": "synthetic-private-environment-value",
        })
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-c", OFFLINE_BOOTSTRAP],
            env=environment,
            cwd=directory,
        )
        with (Path(directory) / "server.log").open("w+", encoding="utf-8") as error_log:
            async with stdio_client(parameters, errlog=error_log) as (read, write):
                async with ClientSession(read, write) as session:
                    initialization = await session.initialize()
                    catalog = await session.list_tools()
                    calls = {}
                    for label, name, arguments in (
                        ("empty_plan", "obs_validate_registration_plan", {"crns": []}),
                        ("duplicate_plan", "obs_validate_registration_plan", {"crns": ["10001", "10001"]}),
                        ("invalid_group", "obs_get_elective_group", {"group_id": 0}),
                        ("missing_argument", "obs_validate_registration_plan", {}),
                        ("draft_without_credentials", "obs_get_registration_draft", {}),
                        ("plan_without_credentials", "obs_validate_registration_plan", {"crns": ["10001"]}),
                    ):
                        result = await session.call_tool(name, arguments)
                        calls[label] = {
                            "is_error": result.isError,
                            "text": "\n".join(item.text for item in result.content if hasattr(item, "text")),
                        }
            return {
                "server_name": initialization.serverInfo.name,
                "protocol_version": initialization.protocolVersion,
                "tools": {tool.name: tool.model_dump(by_alias=True) for tool in catalog.tools},
                "tool_count": len(catalog.tools),
                "calls": calls,
                "state_files": [str(path.relative_to(directory)) for path in Path(directory).rglob("*") if path.is_file() and path.name != "server.log"],
                "temporary_directory": directory,
            }


class RegistrationProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = asyncio.run(asyncio.wait_for(exchange(), timeout=30))

    def test_real_stdio_discovery_advertises_the_three_registration_tools(self) -> None:
        self.assertEqual(self.result["server_name"], "itu-mcp")
        self.assertTrue(self.result["protocol_version"])
        self.assertEqual(self.result["tool_count"], 93)
        self.assertEqual(len(self.result["tools"]), 93)
        for name in REGISTRATION_TOOLS:
            with self.subTest(tool=name):
                metadata = self.result["tools"][name]
                self.assertTrue(metadata["annotations"]["readOnlyHint"])
                self.assertFalse(metadata["annotations"]["destructiveHint"])
                self.assertTrue(metadata["annotations"]["idempotentHint"])

    def test_actual_protocol_argument_schemas_match_registration_methods(self) -> None:
        tools = self.result["tools"]
        draft = tools["obs_get_registration_draft"]["inputSchema"]
        self.assertEqual(draft.get("properties"), {})
        group = tools["obs_get_elective_group"]["inputSchema"]
        self.assertEqual(group["properties"]["group_id"]["type"], "integer")
        self.assertIn("group_id", group["required"])
        plan = tools["obs_validate_registration_plan"]["inputSchema"]
        self.assertEqual(plan["properties"]["crns"]["type"], "array")
        self.assertEqual(plan["properties"]["crns"]["items"]["type"], "string")
        self.assertIn("crns", plan["required"])

    def test_invalid_calls_are_rejected_before_authentication_or_network(self) -> None:
        messages = {
            "empty_plan": "between one and twelve",
            "duplicate_plan": "Duplicate CRNs",
            "invalid_group": "positive integer",
        }
        for label, expected in messages.items():
            with self.subTest(call=label):
                result = self.result["calls"][label]
                self.assertTrue(result["is_error"])
                self.assertIn(expected, result["text"])
                self.assertNotIn("must both be set", result["text"])
        self.assertTrue(self.result["calls"]["missing_argument"]["is_error"])

    def test_missing_credentials_produce_clean_errors_without_state_or_secret_leaks(self) -> None:
        for label in ("draft_without_credentials", "plan_without_credentials"):
            with self.subTest(call=label):
                result = self.result["calls"][label]
                self.assertTrue(result["is_error"])
                self.assertIn("NINOVA_USERNAME and NINOVA_PASSWORD must both be set", result["text"])
        for result in self.result["calls"].values():
            self.assertNotIn("Offline protocol test attempted", result["text"])
            self.assertNotIn("synthetic-private-environment-value", result["text"])
            self.assertNotIn(self.result["temporary_directory"], result["text"])
            self.assertNotIn("Traceback", result["text"])
        self.assertEqual(self.result["state_files"], [])


if __name__ == "__main__":
    unittest.main()
