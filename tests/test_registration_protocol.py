"""Exercise registration tool contracts through the official MCP stdio client."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_grade_distribution import OFFICIAL_HTML
from ninova_mcp.server import LOCAL_TOOL_NAMES

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION_TOOLS = (
    "obs_get_registration_draft",
    "obs_get_elective_group",
    "obs_validate_registration_plan",
    "obs_get_grade_distribution",
)
OFFLINE_BOOTSTRAP = """
import os
import requests
import socket

def deny_network(*args, **kwargs):
    raise AssertionError("Offline protocol test attempted an HTTP request.")

requests.sessions.Session.request = deny_network
_real_connect = socket.socket.connect

def loopback_only(sock, address, *args, **kwargs):
    # asyncio on Windows builds its wakeup channel from a loopback socket pair.
    if isinstance(address, tuple) and address and address[0] in ("127.0.0.1", "::1"):
        return _real_connect(sock, address, *args, **kwargs)
    return deny_network()

socket.socket.connect = loopback_only
socket.getaddrinfo = deny_network
from ninova_mcp.obs_client import ObsPublicClient

grade_html = os.environ.pop("REGISTRATION_GRADE_FIXTURE")

def fixture_get_html(self, path, *, params=None):
    if path != "/public/DersNotDagilimi/NotDagilimiSearch" or params != {
        "bransKodu": "UZB", "dersNo": "438E", "yil": 2026,
    }:
        raise AssertionError("Offline protocol test attempted an unexpected public request.")
    return grade_html, "https://obs.itu.edu.tr/public/DersNotDagilimi/NotDagilimiSearch?bransKodu=UZB&dersNo=438E&yil=2026"

ObsPublicClient._get_html = fixture_get_html
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
            "REGISTRATION_GRADE_FIXTURE": OFFICIAL_HTML,
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
                        ("save_draft_disabled", "obs_save_registration_draft", {"crns": ["10001"], "confirm": True}),
                        ("save_draft_unconfirmed", "obs_save_registration_draft", {"crns": ["10001"], "confirm": False}),
                        ("save_draft_missing_confirmation", "obs_save_registration_draft", {"crns": ["10001"]}),
                        ("save_draft_truthy_confirmation", "obs_save_registration_draft", {"crns": ["10001"], "confirm": "true"}),
                        ("plan_without_credentials", "obs_validate_registration_plan", {"crns": ["10001"]}),
                        ("invalid_grade_course", "obs_get_grade_distribution", {"course_code": "UZB"}),
                        ("invalid_grade_year", "obs_get_grade_distribution", {"course_code": "UZB438E", "year": 1800}),
                        ("invalid_grade_term", "obs_get_grade_distribution", {"course_code": "UZB438E", "term_code": "2026"}),
                        ("mismatched_grade_year", "obs_get_grade_distribution", {"course_code": "UZB438E", "year": 2025, "term_code": "202620"}),
                        ("grade_without_credentials", "obs_get_grade_distribution", {"course_code": "UZB438E", "year": 2026, "term_code": "202620"}),
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

    def test_real_stdio_discovery_advertises_the_four_feature_tools(self) -> None:
        self.assertEqual(self.result["server_name"], "itu-mcp")
        self.assertTrue(self.result["protocol_version"])
        self.assertEqual(self.result["tool_count"], len(LOCAL_TOOL_NAMES))
        self.assertEqual(set(self.result["tools"]), set(LOCAL_TOOL_NAMES))
        for name in REGISTRATION_TOOLS:
            with self.subTest(tool=name):
                metadata = self.result["tools"][name]
                # The two tools that POST to the OBS draft-check endpoint take a
                # server lock and use quota, so they are not advertised as reads.
                checks_obs = name in {"obs_validate_registration_plan", "obs_get_elective_group"}
                self.assertEqual(metadata["annotations"]["readOnlyHint"], not checks_obs)
                self.assertFalse(metadata["annotations"]["destructiveHint"])
                self.assertEqual(metadata["annotations"]["idempotentHint"], not checks_obs)

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
        distribution = tools["obs_get_grade_distribution"]["inputSchema"]
        self.assertEqual(distribution["properties"]["course_code"]["type"], "string")
        self.assertEqual({option["type"] for option in distribution["properties"]["year"]["anyOf"]}, {"integer", "null"})
        self.assertEqual({option["type"] for option in distribution["properties"]["term_code"]["anyOf"]}, {"string", "null"})
        self.assertEqual(distribution["required"], ["course_code"])

    def test_draft_write_is_disabled_by_default_over_real_stdio(self) -> None:
        metadata = self.result["tools"]["obs_save_registration_draft"]
        self.assertFalse(metadata["annotations"]["readOnlyHint"])
        self.assertTrue(metadata["annotations"]["destructiveHint"])
        self.assertFalse(metadata["annotations"]["idempotentHint"])
        self.assertEqual(set(metadata["inputSchema"]["required"]), {"crns", "confirm"})
        for name in ("save_draft_disabled", "save_draft_unconfirmed", "save_draft_missing_confirmation", "save_draft_truthy_confirmation"):
            with self.subTest(name=name):
                response = self.result["calls"][name]
                self.assertTrue(response["is_error"])
                self.assertNotIn("must both be set", response["text"])
        self.assertIn("disabled", self.result["calls"]["save_draft_disabled"]["text"])
        self.assertIn("explicit confirmation", self.result["calls"]["save_draft_unconfirmed"]["text"])

    def test_invalid_calls_are_rejected_before_authentication_or_network(self) -> None:
        messages = {
            "empty_plan": "between one and twelve",
            "duplicate_plan": "Duplicate CRNs",
            "invalid_group": "positive integer",
            "invalid_grade_course": "full course code",
            "invalid_grade_year": "year must be an integer",
            "invalid_grade_term": "six-digit code",
            "mismatched_grade_year": "different academic years",
        }
        for label, expected in messages.items():
            with self.subTest(call=label):
                result = self.result["calls"][label]
                self.assertTrue(result["is_error"])
                self.assertIn(expected, result["text"])
                self.assertNotIn("must both be set", result["text"])
        self.assertTrue(self.result["calls"]["missing_argument"]["is_error"])

    def test_public_grade_distribution_succeeds_through_mcp_without_credentials(self) -> None:
        response = self.result["calls"]["grade_without_credentials"]
        self.assertFalse(response["is_error"])
        result = json.loads(response["text"])
        self.assertEqual(result["requested_course_code"], "UZB 438E")
        self.assertEqual(result["aggregation_scope"], "combined_course_codes")
        self.assertEqual(result["reported_course_codes"], ["UZB 438", "UZB 438E"])
        self.assertEqual(result["terms"][0]["term_code"], "202620")
        self.assertEqual(result["terms"][0]["announced_student_count"], 53)
        self.assertEqual(result["terms"][0]["counts_by_grade"]["AA"], 34)
        self.assertEqual(result["terms"][0]["counts_by_grade"]["BB"], 4)
        self.assertEqual(len(result["available_terms"]), 2)
        self.assertTrue(result["complete"])

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
