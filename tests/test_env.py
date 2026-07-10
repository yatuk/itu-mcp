from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ninova_mcp.client import NinovaClient
from ninova_mcp.env import load_ninova_env
from ninova_mcp.server import NinovaMcpApp


class EnvLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_cwd = Path.cwd()
        self.saved_env = {
            key: os.environ.get(key)
            for key in (
                "NINOVA_USERNAME",
                "NINOVA_PASSWORD",
                "NINOVA_BASE_URL",
                "NINOVA_STATE_DIR",
                "NINOVA_ENV_FILE",
                "UNRELATED_ENV",
            )
        }
        for key in self.saved_env:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        os.chdir(self.original_cwd)
        for key in self.saved_env:
            os.environ.pop(key, None)
        for key, value in self.saved_env.items():
            if value is not None:
                os.environ[key] = value

    def test_load_ninova_env_reads_only_ninova_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            env_path = temp_path / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "NINOVA_USERNAME=file_user",
                        "NINOVA_PASSWORD='file pass'",
                        "UNRELATED_ENV=should_not_load",
                    ]
                ),
                encoding="utf-8",
            )
            os.chdir(temp_path)
            try:
                load_ninova_env()

                self.assertEqual(os.environ["NINOVA_USERNAME"], "file_user")
                self.assertEqual(os.environ["NINOVA_PASSWORD"], "file pass")
                self.assertNotIn("UNRELATED_ENV", os.environ)
            finally:
                # Windows cannot remove a temp dir while it is still cwd.
                os.chdir(self.original_cwd)

    def test_load_ninova_env_does_not_override_existing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            env_path = temp_path / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "NINOVA_USERNAME=file_user",
                        "NINOVA_PASSWORD=file_password",
                    ]
                ),
                encoding="utf-8",
            )
            os.chdir(temp_path)
            try:
                os.environ["NINOVA_USERNAME"] = "process_user"

                load_ninova_env()

                self.assertEqual(os.environ["NINOVA_USERNAME"], "process_user")
                self.assertEqual(os.environ["NINOVA_PASSWORD"], "file_password")
            finally:
                os.chdir(self.original_cwd)

    def test_app_auth_status_uses_dotenv_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            env_path = temp_path / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "NINOVA_USERNAME=file_user",
                        "NINOVA_PASSWORD=file_password",
                    ]
                ),
                encoding="utf-8",
            )
            os.chdir(temp_path)
            try:
                with patch.object(
                    NinovaClient,
                    "ensure_logged_in",
                    return_value={
                        "base_url": "https://ninova.itu.edu.tr",
                        "username": "file_user",
                        "last_login_at": None,
                        "login_method": "mock",
                        "cookie_names": [],
                    },
                ) as ensure_logged_in, patch(
                    "ninova_mcp.server.ObsClient.ensure_ready",
                    return_value={
                        "obs_base_url": "https://obs.itu.edu.tr",
                        "jwt_present": True,
                        "login_method": "mock",
                    },
                ):
                    app = NinovaMcpApp()
                    status = app.auth_status()

                self.assertTrue(status["credentials_present"])
                self.assertTrue(status["authenticated"])
                ensure_logged_in.assert_called()
                self.assertTrue((status.get("obs") or {}).get("jwt_present"))
            finally:
                os.chdir(self.original_cwd)


if __name__ == "__main__":
    unittest.main()
