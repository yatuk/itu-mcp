import os
import unittest
from unittest.mock import patch

from ninova_mcp.remote import _selected_remote_tool_names
from ninova_mcp.server import MAIL_TOOL_NAMES, OBS_REGISTRATION_WRITE_TOOL_NAMES, REMOTE_TOOL_NAMES


class RemoteToolAllowlistTests(unittest.TestCase):
    def test_mail_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NINOVA_REMOTE_ALLOWED_TOOLS", None)
            os.environ.pop("NINOVA_REMOTE_ENABLE_MAIL", None)
            os.environ.pop("NINOVA_OBS_REGISTRATION_WRITES", None)
            self.assertEqual(
                _selected_remote_tool_names(),
                [name for name in REMOTE_TOOL_NAMES if name not in MAIL_TOOL_NAMES | OBS_REGISTRATION_WRITE_TOOL_NAMES],
            )

    def test_allowlist_preserves_canonical_order(self) -> None:
        with patch.dict(
            os.environ,
            {"NINOVA_REMOTE_ALLOWED_TOOLS": "list_courses,auth_status"},
            clear=False,
        ):
            selected = _selected_remote_tool_names()
        self.assertEqual(set(selected), {"auth_status", "list_courses"})
        self.assertEqual(selected, [name for name in REMOTE_TOOL_NAMES if name in set(selected)])

    def test_unknown_or_excluded_tool_fails_closed(self) -> None:
        with patch.dict(
            os.environ,
            {"NINOVA_REMOTE_ALLOWED_TOOLS": "auth_status,submit_assignment"},
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                _selected_remote_tool_names()

    def test_mail_requires_explicit_remote_opt_in(self) -> None:
        with patch.dict(
            os.environ,
            {"NINOVA_REMOTE_ALLOWED_TOOLS": "auth_status,mail_status"},
            clear=False,
        ):
            os.environ.pop("NINOVA_REMOTE_ENABLE_MAIL", None)
            with self.assertRaises(RuntimeError):
                _selected_remote_tool_names()

            os.environ["NINOVA_REMOTE_ENABLE_MAIL"] = "1"
            self.assertEqual(_selected_remote_tool_names(), ["auth_status", "mail_status"])

    def test_registration_write_is_never_served_remotely_even_when_requested(self) -> None:
        with patch.dict(os.environ, {"NINOVA_REMOTE_ALLOWED_TOOLS": "obs_save_registration_draft"}):
            for value in (None, "", "0", "true", "1"):
                with self.subTest(value=value):
                    if value is None:
                        os.environ.pop("NINOVA_OBS_REGISTRATION_WRITES", None)
                    else:
                        os.environ["NINOVA_OBS_REGISTRATION_WRITES"] = value
                    with self.assertRaisesRegex(RuntimeError, "remote-excluded"):
                        _selected_remote_tool_names()

    def test_registration_opt_in_respects_existing_remote_allowlist(self) -> None:
        with patch.dict(os.environ, {
            "NINOVA_OBS_REGISTRATION_WRITES": "1",
            "NINOVA_REMOTE_ALLOWED_TOOLS": "auth_status",
        }):
            self.assertEqual(_selected_remote_tool_names(), ["auth_status"])
            os.environ.pop("NINOVA_REMOTE_ALLOWED_TOOLS")
            self.assertNotIn("obs_save_registration_draft", _selected_remote_tool_names())

    def test_registration_write_stays_out_if_it_leaves_the_excluded_set(self) -> None:
        from ninova_mcp import remote
        from ninova_mcp.server import LOCAL_TOOL_NAMES, REMOTE_EXCLUDED_TOOLS

        # Simulate someone removing the tool from REMOTE_EXCLUDED_TOOLS: the
        # remote list is derived from that set when tool_registry is imported.
        excluded = REMOTE_EXCLUDED_TOOLS - OBS_REGISTRATION_WRITE_TOOL_NAMES
        leaked = [name for name in LOCAL_TOOL_NAMES if name not in excluded]
        self.assertIn("obs_save_registration_draft", leaked)
        with patch.object(remote, "REMOTE_TOOL_NAMES", leaked), patch.dict(os.environ, {}, clear=False):
            for key in ("NINOVA_REMOTE_ALLOWED_TOOLS", "NINOVA_TOOLSETS", "NINOVA_DISABLED_TOOLSETS"):
                os.environ.pop(key, None)
            # Including the opt-in flag set: it enables the tool locally and
            # must not open the remote transport.
            for value in (None, "", "0", "false", "1", "true"):
                with self.subTest(value=value):
                    if value is None:
                        os.environ.pop("NINOVA_OBS_REGISTRATION_WRITES", None)
                    else:
                        os.environ["NINOVA_OBS_REGISTRATION_WRITES"] = value
                    self.assertNotIn("obs_save_registration_draft", _selected_remote_tool_names())
                    os.environ["NINOVA_REMOTE_ALLOWED_TOOLS"] = "obs_save_registration_draft"
                    with self.assertRaisesRegex(RuntimeError, "remote-excluded"):
                        _selected_remote_tool_names()
                    os.environ.pop("NINOVA_REMOTE_ALLOWED_TOOLS")

    def test_submit_assignment_stays_out_if_it_leaves_the_excluded_set(self) -> None:
        from ninova_mcp import remote
        from ninova_mcp.server import LOCAL_TOOL_NAMES, REMOTE_EXCLUDED_TOOLS

        leaked = [name for name in LOCAL_TOOL_NAMES if name not in REMOTE_EXCLUDED_TOOLS - {"submit_assignment"}]
        self.assertIn("submit_assignment", leaked)
        with patch.object(remote, "REMOTE_TOOL_NAMES", leaked), patch.dict(os.environ, {}, clear=False):
            for key in ("NINOVA_REMOTE_ALLOWED_TOOLS", "NINOVA_TOOLSETS", "NINOVA_DISABLED_TOOLSETS"):
                os.environ.pop(key, None)
            for value in (None, "0", "1"):
                with self.subTest(value=value):
                    if value is None:
                        os.environ.pop("NINOVA_ALLOW_UPLOADS", None)
                    else:
                        os.environ["NINOVA_ALLOW_UPLOADS"] = value
                    self.assertNotIn("submit_assignment", _selected_remote_tool_names())
                    os.environ["NINOVA_REMOTE_ALLOWED_TOOLS"] = "submit_assignment"
                    with self.assertRaisesRegex(RuntimeError, "remote-excluded"):
                        _selected_remote_tool_names()
                    os.environ.pop("NINOVA_REMOTE_ALLOWED_TOOLS")

    def test_server_build_refuses_a_write_tool_that_passed_selection(self) -> None:
        from ninova_mcp import remote

        self.assertEqual(
            remote.NEVER_REMOTE_TOOL_NAMES,
            {"submit_assignment", "obs_save_registration_draft"},
        )
        for name in sorted(remote.NEVER_REMOTE_TOOL_NAMES):
            with self.subTest(name=name):
                with patch.object(remote, "_selected_remote_tool_names", return_value=["auth_status", name]):
                    with self.assertRaisesRegex(RuntimeError, "never be served remotely"):
                        remote._build_fastmcp(object(), mount_path="/mcp")  # type: ignore[arg-type]

    def test_tool_results_cannot_carry_their_own_trust_marker(self) -> None:
        import asyncio

        from mcp.server.fastmcp import FastMCP

        from ninova_mcp.server_build import register_tools

        class _App:
            def auth_status(self) -> dict[str, object]:
                # As if passed through from remote JSON.
                return {"untrusted_external_content": False, "x": 1}

        mcp = FastMCP("t")
        register_tools(mcp, _App(), ["auth_status"])  # type: ignore[arg-type]
        _, structured = asyncio.run(mcp.call_tool("auth_status", {}))
        self.assertIs(structured["untrusted_external_content"], True)
        self.assertEqual(structured["x"], 1)


if __name__ == "__main__":
    unittest.main()
