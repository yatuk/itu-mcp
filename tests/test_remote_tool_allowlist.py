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

    def test_registration_write_requires_exact_opt_in_even_in_explicit_allowlist(self) -> None:
        with patch.dict(os.environ, {"NINOVA_REMOTE_ALLOWED_TOOLS": "obs_save_registration_draft"}):
            for value in (None, "", "0", "true", "yes", "on", " 1 ", "01"):
                with self.subTest(value=value):
                    if value is None:
                        os.environ.pop("NINOVA_OBS_REGISTRATION_WRITES", None)
                    else:
                        os.environ["NINOVA_OBS_REGISTRATION_WRITES"] = value
                    with self.assertRaisesRegex(RuntimeError, "disabled"):
                        _selected_remote_tool_names()
            os.environ["NINOVA_OBS_REGISTRATION_WRITES"] = "1"
            self.assertEqual(_selected_remote_tool_names(), ["obs_save_registration_draft"])

    def test_registration_opt_in_respects_existing_remote_allowlist(self) -> None:
        with patch.dict(os.environ, {
            "NINOVA_OBS_REGISTRATION_WRITES": "1",
            "NINOVA_REMOTE_ALLOWED_TOOLS": "auth_status",
        }):
            self.assertEqual(_selected_remote_tool_names(), ["auth_status"])
            os.environ.pop("NINOVA_REMOTE_ALLOWED_TOOLS")
            self.assertIn("obs_save_registration_draft", _selected_remote_tool_names())


if __name__ == "__main__":
    unittest.main()
