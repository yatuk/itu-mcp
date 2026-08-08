"""Protocol tests that drive the stdio server with the OFFICIAL MCP client.

This exercises the real newline-delimited JSON framing that Claude Desktop,
Claude Code, Cursor, and Codex use. A hand-rolled Content-Length handshake
would pass against a broken server, so we deliberately go through the SDK
client instead.
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def _handshake() -> tuple[str, str, list[str], bool, list[str], list[str]]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    # Dummy credentials: initialize/tools-list/auth_status never hit the network.
    env.setdefault("NINOVA_USERNAME", "dummy")
    env.setdefault("NINOVA_PASSWORD", "dummy")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ninova_mcp"],
        env=env,
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            result = await session.call_tool("auth_status", {})
            prompts = await session.list_prompts()
            prompt_names = [prompt.name for prompt in prompts.prompts]
            resources = await session.list_resources()
            resource_uris = [str(resource.uri) for resource in resources.resources]
            return (
                init.serverInfo.name,
                init.protocolVersion,
                names,
                result.isError,
                prompt_names,
                resource_uris,
            )


class ServerProtocolTests(unittest.TestCase):
    def test_initialize_list_tools_and_call(self) -> None:
        (
            name,
            protocol_version,
            tool_names,
            auth_is_error,
            prompt_names,
            resource_uris,
        ) = asyncio.run(_handshake())

        self.assertEqual(name, "itu-mcp")
        self.assertTrue(protocol_version)  # SDK negotiates a real MCP version

        for expected in (
            "auth_status",
            "get_dashboard",
            "download_resource",
            "get_course_announcements",
            "get_course_assignments",
            "get_course_class_files",
            "get_dashboard_assignments",
            "get_course_grades",
            "get_course_message_board",
            "get_course_overview",
            "sync_all_courses",
            "get_updates",
            "get_upcoming_deadlines",
            "read_resource_text",
            "get_assignment_upload_slots",
            "submit_assignment",
            "obs_auth_status",
            "obs_list_registered_courses",
            "obs_get_profile",
        ):
            self.assertIn(expected, tool_names)

        # auth_status returns cleanly (credentials present but not authenticated).
        self.assertFalse(auth_is_error)

        # Prompts and resources are advertised over the real protocol, not just
        # registered in-process.
        for expected in ("plan_next_term", "check_course_eligibility", "research_course"):
            self.assertIn(expected, prompt_names)
        self.assertIn("itu://reference/grade-scale", resource_uris)
        self.assertIn("itu://reference/program-types", resource_uris)


if __name__ == "__main__":
    unittest.main()
