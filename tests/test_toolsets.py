"""Toolset selection: NINOVA_TOOLSETS and NINOVA_DISABLED_TOOLSETS."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ninova_mcp import toolsets
from ninova_mcp.remote import _selected_remote_tool_names
from ninova_mcp.server import (
    LOCAL_TOOL_NAMES,
    MAIL_TOOL_NAMES,
    OBS_REGISTRATION_WRITE_TOOL_NAMES,
    REMOTE_EXCLUDED_TOOLS,
    REMOTE_TOOL_NAMES,
    TOOLS,
    build_stdio_server,
    main,
)
from ninova_mcp.toolsets import (
    CORE_TOOL_NAMES,
    TOOLSET_NAMES,
    TOOLSETS,
    ToolsetError,
    enabled_toolsets,
    filter_tool_names,
)

_CLEARED = (
    "NINOVA_TOOLSETS",
    "NINOVA_DISABLED_TOOLSETS",
    "NINOVA_REMOTE_ALLOWED_TOOLS",
    "NINOVA_REMOTE_ENABLE_MAIL",
    "NINOVA_OBS_REGISTRATION_WRITES",
)


class _IsolatedEnvTestCase(unittest.TestCase):
    """Start every test with the selection variables unset and no .env file."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.dict(
            os.environ,
            {"NINOVA_ENV_FILE": str(Path(directory.name) / "missing.env")},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in _CLEARED:
            os.environ.pop(key, None)


def _tools_of(*names: str) -> set[str]:
    return {tool for name in names for tool in TOOLSETS[name][1]}


class PartitionTests(unittest.TestCase):
    def test_every_tool_belongs_to_exactly_one_toolset(self) -> None:
        grouped = [tool for _, tools in TOOLSETS.values() for tool in tools]
        duplicates = sorted({tool for tool in grouped if grouped.count(tool) > 1})
        self.assertEqual(duplicates, [], f"tools in more than one toolset: {duplicates}")

        registered = {tool["name"] for tool in TOOLS}
        missing = sorted(registered - set(grouped))
        self.assertEqual(
            missing,
            [],
            f"tools without a toolset, add them to ninova_mcp/toolsets.py: {missing}",
        )
        stale = sorted(set(grouped) - registered)
        self.assertEqual(stale, [], f"toolsets name tools that do not exist: {stale}")

    def test_no_toolset_is_empty_and_names_are_lowercase(self) -> None:
        for name, (summary, tools) in TOOLSETS.items():
            with self.subTest(toolset=name):
                self.assertEqual(name, name.lower())
                self.assertTrue(summary.strip())
                self.assertTrue(tools)

    def test_core_contains_auth_status_and_is_served_remotely(self) -> None:
        self.assertIn("auth_status", CORE_TOOL_NAMES)
        self.assertFalse(CORE_TOOL_NAMES & REMOTE_EXCLUDED_TOOLS)
        self.assertFalse(CORE_TOOL_NAMES & MAIL_TOOL_NAMES)

    def test_mail_toolset_matches_mail_tool_names(self) -> None:
        self.assertEqual(_tools_of("mail"), set(MAIL_TOOL_NAMES))

    def test_cafeteria_menu_is_a_portal_tool_because_it_needs_login(self) -> None:
        self.assertIn("get_cafeteria_menu", _tools_of("portal"))
        self.assertNotIn("get_cafeteria_menu", _tools_of("campus"))

    def test_campus_and_portal_tools_live_in_the_module_named_like_their_toolset(self) -> None:
        from ninova_mcp.app_campus import CampusToolsMixin
        from ninova_mcp.app_portal import PortalToolsMixin
        from ninova_mcp.tool_metadata.campus import CAMPUS_TOOLS
        from ninova_mcp.tool_metadata.portal import PORTAL_TOOLS

        for toolset, mixin, metadata in (
            ("campus", CampusToolsMixin, CAMPUS_TOOLS),
            ("portal", PortalToolsMixin, PORTAL_TOOLS),
        ):
            with self.subTest(toolset=toolset):
                self.assertEqual({tool["name"] for tool in metadata}, _tools_of(toolset))
                for name in _tools_of(toolset):
                    self.assertIn(name, vars(mixin))


class SelectionTests(unittest.TestCase):
    def test_default_is_every_tool_in_the_same_order(self) -> None:
        self.assertEqual(filter_tool_names(LOCAL_TOOL_NAMES, {}), LOCAL_TOOL_NAMES)
        self.assertEqual(enabled_toolsets({}), list(TOOLSET_NAMES))
        blank = {"NINOVA_TOOLSETS": " ", "NINOVA_DISABLED_TOOLSETS": ","}
        self.assertEqual(filter_tool_names(LOCAL_TOOL_NAMES, blank), LOCAL_TOOL_NAMES)

    def test_allow_list_keeps_only_named_toolsets_plus_core(self) -> None:
        selected = filter_tool_names(
            LOCAL_TOOL_NAMES, {"NINOVA_TOOLSETS": "ninova, OBS ,archive"}
        )
        self.assertEqual(set(selected), _tools_of("core", "ninova", "obs", "archive"))
        self.assertEqual(
            selected, [name for name in LOCAL_TOOL_NAMES if name in set(selected)]
        )

    def test_deny_list_removes_only_named_toolsets(self) -> None:
        selected = filter_tool_names(
            LOCAL_TOOL_NAMES, {"NINOVA_DISABLED_TOOLSETS": "library,mail"}
        )
        self.assertEqual(
            set(selected), set(LOCAL_TOOL_NAMES) - _tools_of("library", "mail")
        )

    def test_deny_list_wins_when_both_are_set(self) -> None:
        env = {
            "NINOVA_TOOLSETS": "ninova,obs,mail",
            "NINOVA_DISABLED_TOOLSETS": "mail,library",
        }
        self.assertEqual(enabled_toolsets(env), ["core", "ninova", "obs"])
        self.assertEqual(
            set(filter_tool_names(LOCAL_TOOL_NAMES, env)),
            _tools_of("core", "ninova", "obs"),
        )

    def test_unknown_name_fails_and_lists_valid_names(self) -> None:
        for variable in ("NINOVA_TOOLSETS", "NINOVA_DISABLED_TOOLSETS"):
            with self.subTest(variable=variable):
                with self.assertRaises(ToolsetError) as raised:
                    filter_tool_names(LOCAL_TOOL_NAMES, {variable: "ninova,kutuphane"})
                message = str(raised.exception)
                self.assertIn(variable, message)
                self.assertIn("kutuphane", message)
                for name in TOOLSET_NAMES:
                    self.assertIn(name, message)

    def test_core_tools_survive_any_selection(self) -> None:
        for env in (
            {"NINOVA_TOOLSETS": "mail"},
            {"NINOVA_TOOLSETS": "core"},
            {"NINOVA_DISABLED_TOOLSETS": ",".join(n for n in TOOLSET_NAMES if n != "core")},
        ):
            with self.subTest(env=env):
                self.assertTrue(
                    CORE_TOOL_NAMES <= set(filter_tool_names(LOCAL_TOOL_NAMES, env))
                )
        only_core = filter_tool_names(LOCAL_TOOL_NAMES, {"NINOVA_TOOLSETS": "core"})
        self.assertEqual(set(only_core), set(CORE_TOOL_NAMES))

    def test_disabling_core_is_refused(self) -> None:
        with self.assertRaisesRegex(ToolsetError, "always on"):
            enabled_toolsets({"NINOVA_DISABLED_TOOLSETS": "core"})


class StdioServerTests(_IsolatedEnvTestCase):
    @staticmethod
    def _registered() -> list[str]:
        return [tool.name for tool in build_stdio_server()._tool_manager.list_tools()]

    def test_default_registers_every_tool(self) -> None:
        self.assertEqual(self._registered(), LOCAL_TOOL_NAMES)

    def test_selection_is_applied_at_registration(self) -> None:
        os.environ["NINOVA_TOOLSETS"] = "archive"
        self.assertEqual(set(self._registered()), _tools_of("core", "archive"))

    def test_unknown_name_stops_the_server_build(self) -> None:
        os.environ["NINOVA_DISABLED_TOOLSETS"] = "nope"
        with self.assertRaises(ToolsetError):
            build_stdio_server()

    def test_selection_is_read_from_the_env_file(self) -> None:
        env_file = Path(os.environ["NINOVA_ENV_FILE"])
        env_file.write_text("NINOVA_TOOLSETS=library\n", encoding="utf-8")
        self.assertEqual(
            toolsets.local_tool_names(LOCAL_TOOL_NAMES),
            [n for n in LOCAL_TOOL_NAMES if n in _tools_of("core", "library")],
        )


class CliTests(_IsolatedEnvTestCase):
    @staticmethod
    def _run(*argv: str) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(list(argv))
        return buf.getvalue()

    def test_list_tools_default_prints_every_tool(self) -> None:
        self.assertEqual(self._run("--list-tools").split(), LOCAL_TOOL_NAMES)

    def test_list_tools_reflects_selection(self) -> None:
        os.environ["NINOVA_TOOLSETS"] = "mail"
        self.assertEqual(
            set(self._run("--list-tools").split()), _tools_of("core", "mail")
        )

    def test_list_toolsets_shows_counts_and_state(self) -> None:
        os.environ["NINOVA_DISABLED_TOOLSETS"] = "library"
        rows = {
            line.split()[0]: line.split()[1:3]
            for line in self._run("--list-toolsets").splitlines()
        }
        self.assertEqual(list(rows), list(TOOLSET_NAMES))
        for name, (_, tools) in TOOLSETS.items():
            with self.subTest(toolset=name):
                self.assertEqual(rows[name][0], str(len(tools)))
                self.assertEqual(rows[name][1], "off" if name == "library" else "on")
        self.assertEqual(
            sum(int(count) for count, _ in rows.values()), len(LOCAL_TOOL_NAMES)
        )

    def test_unknown_name_exits_with_a_clear_message(self) -> None:
        os.environ["NINOVA_TOOLSETS"] = "ninova,typo"
        for argv in (["--list-tools"], ["--list-toolsets"], []):
            with self.subTest(argv=argv):
                err = io.StringIO()
                with redirect_stderr(err), redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main(argv)
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("typo", err.getvalue())
                self.assertIn("Valid toolsets: core, ninova", err.getvalue())


class RemoteSelectionTests(_IsolatedEnvTestCase):
    def test_unset_variables_leave_remote_rules_unchanged(self) -> None:
        hidden = MAIL_TOOL_NAMES | OBS_REGISTRATION_WRITE_TOOL_NAMES
        self.assertEqual(
            _selected_remote_tool_names(),
            [name for name in REMOTE_TOOL_NAMES if name not in hidden],
        )
        os.environ["NINOVA_REMOTE_ENABLE_MAIL"] = "1"
        self.assertEqual(_selected_remote_tool_names(), REMOTE_TOOL_NAMES)
        os.environ["NINOVA_REMOTE_ALLOWED_TOOLS"] = "list_courses"
        self.assertEqual(_selected_remote_tool_names(), ["list_courses"])

    def test_toolsets_narrow_the_remote_list(self) -> None:
        os.environ["NINOVA_DISABLED_TOOLSETS"] = "library,archive"
        selected = _selected_remote_tool_names()
        self.assertEqual(
            selected,
            [
                name
                for name in REMOTE_TOOL_NAMES
                if name not in MAIL_TOOL_NAMES | _tools_of("library", "archive")
            ],
        )

    def test_toolsets_never_reenable_remote_excluded_tools(self) -> None:
        os.environ["NINOVA_TOOLSETS"] = "ninova,files,registration,library,obs"
        os.environ["NINOVA_OBS_REGISTRATION_WRITES"] = "1"
        self.assertFalse(set(_selected_remote_tool_names()) & REMOTE_EXCLUDED_TOOLS)

    def test_mail_toolset_still_needs_the_remote_mail_opt_in(self) -> None:
        os.environ["NINOVA_TOOLSETS"] = "mail"
        self.assertEqual(set(_selected_remote_tool_names()), set(CORE_TOOL_NAMES))
        os.environ["NINOVA_REMOTE_ENABLE_MAIL"] = "1"
        self.assertEqual(
            set(_selected_remote_tool_names()), _tools_of("core", "mail")
        )

    def test_allowed_tools_cannot_name_a_tool_in_a_disabled_toolset(self) -> None:
        os.environ["NINOVA_DISABLED_TOOLSETS"] = "ninova"
        os.environ["NINOVA_REMOTE_ALLOWED_TOOLS"] = "auth_status,list_courses"
        with self.assertRaisesRegex(RuntimeError, "list_courses"):
            _selected_remote_tool_names()

    def test_allowed_tools_still_narrows_inside_the_selection(self) -> None:
        os.environ["NINOVA_TOOLSETS"] = "ninova"
        os.environ["NINOVA_REMOTE_ALLOWED_TOOLS"] = "list_courses"
        self.assertEqual(_selected_remote_tool_names(), ["list_courses"])

    def test_unknown_toolset_fails_remote_startup(self) -> None:
        os.environ["NINOVA_TOOLSETS"] = "nope"
        with self.assertRaises(ToolsetError):
            _selected_remote_tool_names()


if __name__ == "__main__":
    unittest.main()
