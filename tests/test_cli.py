from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from ninova_mcp.server import SERVER_NAME, SERVER_VERSION, main


class CliTests(unittest.TestCase):
    def test_version_flag(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as raised:
                main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn(SERVER_NAME, buf.getvalue())
        self.assertIn(SERVER_VERSION, buf.getvalue())

    def test_list_tools(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["--list-tools"])
        output = buf.getvalue()
        self.assertIn("auth_status", output)
        self.assertIn("list_courses", output)
        self.assertIn("get_upcoming_deadlines", output)

    def test_check_auth_missing_credentials(self) -> None:
        import os
        import tempfile
        from pathlib import Path

        # Isolate from process env and any project .env file on disk.
        saved = {
            key: os.environ.pop(key, None)
            for key in ("NINOVA_USERNAME", "NINOVA_PASSWORD", "NINOVA_ENV_FILE")
        }
        original_cwd = Path.cwd()
        temp_dir = tempfile.mkdtemp()
        try:
            os.chdir(temp_dir)
            os.environ["NINOVA_ENV_FILE"] = str(Path(temp_dir) / "missing.env")
            buf = io.StringIO()
            with redirect_stdout(buf):
                with self.assertRaises(SystemExit) as raised:
                    main(["--check-auth"])
            self.assertEqual(raised.exception.code, 1)
            self.assertIn("credentials_present", buf.getvalue())
            self.assertIn("false", buf.getvalue().lower())
        finally:
            os.chdir(original_cwd)
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            # Best-effort cleanup (Windows may lock briefly).
            try:
                import shutil

                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    def test_help_exits_zero(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as raised:
                main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("stdio", buf.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
