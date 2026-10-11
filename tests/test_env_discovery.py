"""Where ``.env`` is looked for and which keys an auto-discovered one may set."""

from __future__ import annotations

import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ninova_mcp import env as env_module
from ninova_mcp.env import load_ninova_env
from ninova_mcp.mail_client import ItuMailClient, ItuMailError

SRC = Path(__file__).resolve().parents[1] / "src" / "ninova_mcp"
ENV_NAME = re.compile(r"[\"'](NINOVA_[A-Z0-9_]+)[\"']")


class _FakeImap:
    def __init__(self) -> None:
        self.login_args: tuple[str, str] | None = None

    def login(self, username: str, password: str):
        self.login_args = (username, password)
        return "OK", [b"ok"]

    def select(self, mailbox: str, readonly: bool = False):
        return "OK", [b"0"]

    def uid(self, *args: object):
        return "OK", [b""]

    def logout(self):
        return "BYE", [b"bye"]


class _Factory:
    def __init__(self) -> None:
        self.connection = _FakeImap()
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object, **kwargs: object) -> _FakeImap:
        self.calls.append(args)
        return self.connection


class _EnvCase(unittest.TestCase):
    def setUp(self) -> None:
        self.original_cwd = Path.cwd()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        # Start from an environment without any project variable, so the test
        # sees only what the file under test contributes.
        cleaned = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("NINOVA_", "ITU_ARCHIVE_", "PREREQ_CROSSCHECK_"))
        }
        self.env = patch.dict(os.environ, cleaned, clear=True)
        self.env.start()
        env_module.reset_env_notices()

    def tearDown(self) -> None:
        # Windows cannot remove a temp dir while it is still cwd.
        os.chdir(self.original_cwd)
        self.env.stop()
        self.temp.cleanup()
        env_module.reset_env_notices()

    def write_env(self, directory: Path, *lines: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ".env"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def load(self, **kwargs: object) -> tuple[Path | None, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            path = load_ninova_env(**kwargs)
        return path, stderr.getvalue()


class EnvDiscoveryTests(_EnvCase):
    def test_parent_directory_env_is_not_read(self) -> None:
        self.write_env(self.root, "NINOVA_USERNAME=planted_user")
        nested = self.root / "repo" / "sub"
        nested.mkdir(parents=True)
        os.chdir(nested)

        path, _ = self.load()

        self.assertIsNone(path)
        self.assertNotIn("NINOVA_USERNAME", os.environ)

    def test_cwd_env_still_supplies_credentials(self) -> None:
        self.write_env(self.root, "NINOVA_USERNAME=file_user", "NINOVA_PASSWORD=file_pass")
        os.chdir(self.root)

        path, stderr = self.load()

        self.assertEqual(path, self.root / ".env")
        self.assertEqual(os.environ["NINOVA_USERNAME"], "file_user")
        self.assertEqual(os.environ["NINOVA_PASSWORD"], "file_pass")
        self.assertEqual(stderr, "")

    def test_explicit_env_file_is_the_only_file_read(self) -> None:
        self.write_env(self.root, "NINOVA_USERNAME=cwd_user")
        explicit = self.write_env(self.root / "conf", "NINOVA_USERNAME=explicit_user")
        os.chdir(self.root)
        os.environ["NINOVA_ENV_FILE"] = str(explicit)

        path, _ = self.load()

        self.assertEqual(path, explicit)
        self.assertEqual(os.environ["NINOVA_USERNAME"], "explicit_user")

    def test_missing_explicit_env_file_does_not_fall_back_to_cwd(self) -> None:
        self.write_env(self.root, "NINOVA_USERNAME=cwd_user")
        os.chdir(self.root)
        os.environ["NINOVA_ENV_FILE"] = str(self.root / "absent.env")

        path, _ = self.load()

        self.assertIsNone(path)
        self.assertNotIn("NINOVA_USERNAME", os.environ)


class AutoDiscoveredEnvKeyTests(_EnvCase):
    PLANTED = (
        "NINOVA_MAIL_HOST=imap.attacker.example",
        "NINOVA_MAIL_PORT=1993",
        "NINOVA_BASE_URL=https://ninova.attacker.example",
        "NINOVA_OBS_BASE_URL=https://obs.attacker.example",
        "NINOVA_LIBRARY_BASE_URL=https://lib.attacker.example",
        "NINOVA_LIBRARY_CA_BUNDLE=/planted/ca.pem",
        "NINOVA_LIBRARY_PROXY_URL=http://127.0.0.1:18888",
        "NINOVA_STATE_DIR=/planted/state",
        "NINOVA_OBS_REGISTRATION_WRITES=1",
        "NINOVA_ALLOW_UPLOADS=1",
        "NINOVA_REQUIRE_ELICITATION=0",
        "NINOVA_REMOTE_HOST=0.0.0.0",
        "NINOVA_REMOTE_ENABLE_MAIL=1",
        "NINOVA_REMOTE_DISABLE_RATE_LIMIT=1",
        "NINOVA_REMOTE_TRUST_PROXY_HEADERS=1",
        "NINOVA_REMOTE_MCP_PATH=/planted",
        "NINOVA_ALLOWED_HOSTS=attacker.example",
        "NINOVA_ALLOWED_ORIGINS=https://attacker.example",
        "NINOVA_DISABLE_DNS_REBINDING_PROTECTION=1",
        "NINOVA_PUBLIC_BASE_URL=https://attacker.example",
        "NINOVA_SOME_FUTURE_SWITCH=secret-value-xyz",
    )

    def test_auto_discovered_env_cannot_set_security_relevant_keys(self) -> None:
        self.write_env(self.root, "NINOVA_USERNAME=file_user", *self.PLANTED)
        os.chdir(self.root)

        _, stderr = self.load()

        self.assertEqual(os.environ["NINOVA_USERNAME"], "file_user")
        for line in self.PLANTED:
            key, value = line.split("=", 1)
            self.assertNotIn(key, os.environ, key)
            self.assertIn(key, stderr, key)
            if len(value) > 1:
                self.assertNotIn(value, stderr, key)
        self.assertEqual(
            set(env_module.ignored_env_keys()),
            {line.split("=", 1)[0] for line in self.PLANTED},
        )

    def test_override_does_not_unlock_security_relevant_keys(self) -> None:
        self.write_env(self.root, "NINOVA_MAIL_HOST=imap.attacker.example")
        os.chdir(self.root)

        self.load(override=True)

        self.assertNotIn("NINOVA_MAIL_HOST", os.environ)

    def test_notice_is_printed_once_per_process(self) -> None:
        self.write_env(self.root, "NINOVA_STATE_DIR=/planted/state")
        os.chdir(self.root)

        _, first = self.load()
        _, second = self.load()

        self.assertIn("NINOVA_STATE_DIR", first)
        self.assertEqual(second, "")

    def test_a_key_the_process_environment_already_sets_is_not_reported(self) -> None:
        self.write_env(self.root, "NINOVA_STATE_DIR=/planted/state")
        os.chdir(self.root)
        os.environ["NINOVA_STATE_DIR"] = "/chosen/state"

        _, stderr = self.load()

        self.assertEqual(os.environ["NINOVA_STATE_DIR"], "/chosen/state")
        self.assertEqual(stderr, "")

    def test_explicit_env_file_may_set_security_relevant_keys(self) -> None:
        explicit = self.write_env(
            self.root / "conf",
            "NINOVA_STATE_DIR=/chosen/state",
            "NINOVA_OBS_REGISTRATION_WRITES=1",
            "NINOVA_REMOTE_HOST=0.0.0.0",
        )
        os.chdir(self.root)
        os.environ["NINOVA_ENV_FILE"] = str(explicit)

        _, stderr = self.load()

        self.assertEqual(os.environ["NINOVA_STATE_DIR"], "/chosen/state")
        self.assertEqual(os.environ["NINOVA_OBS_REGISTRATION_WRITES"], "1")
        self.assertEqual(os.environ["NINOVA_REMOTE_HOST"], "0.0.0.0")
        self.assertEqual(stderr, "")

    def test_explicit_argument_may_set_security_relevant_keys(self) -> None:
        explicit = self.write_env(self.root / "conf", "NINOVA_STATE_DIR=/chosen/state")

        self.load(env_file=explicit)

        self.assertEqual(os.environ["NINOVA_STATE_DIR"], "/chosen/state")

    def test_auto_discovered_env_may_only_tighten_the_switches(self) -> None:
        self.write_env(
            self.root,
            "NINOVA_ALLOW_UPLOADS=0",
            "NINOVA_REQUIRE_ELICITATION=1",
            "NINOVA_REMOTE_REQUIRE_API_KEY=1",
            "NINOVA_REMOTE_PROTECT_ALL=true",
        )
        os.chdir(self.root)

        _, stderr = self.load()

        self.assertEqual(os.environ["NINOVA_ALLOW_UPLOADS"], "0")
        self.assertEqual(os.environ["NINOVA_REQUIRE_ELICITATION"], "1")
        self.assertEqual(os.environ["NINOVA_REMOTE_REQUIRE_API_KEY"], "1")
        self.assertEqual(os.environ["NINOVA_REMOTE_PROTECT_ALL"], "true")
        self.assertEqual(stderr, "")

    def test_process_environment_keeps_precedence(self) -> None:
        self.write_env(self.root, "NINOVA_USERNAME=file_user", "NINOVA_ALLOW_UPLOADS=0")
        os.chdir(self.root)
        os.environ["NINOVA_USERNAME"] = "process_user"

        self.load()

        self.assertEqual(os.environ["NINOVA_USERNAME"], "process_user")
        self.assertEqual(os.environ["NINOVA_ALLOW_UPLOADS"], "0")

    def test_archive_and_crosscheck_hosts_are_never_loaded_from_a_file(self) -> None:
        lines = (
            "ITU_ARCHIVE_BASE_URL=https://attacker.example/data",
            "PREREQ_CROSSCHECK_BASE_URL=https://attacker.example/feed",
        )
        self.write_env(self.root, *lines)
        explicit = self.write_env(self.root / "conf", *lines)
        os.chdir(self.root)

        self.load()
        self.load(env_file=explicit)

        self.assertNotIn("ITU_ARCHIVE_BASE_URL", os.environ)
        self.assertNotIn("PREREQ_CROSSCHECK_BASE_URL", os.environ)


class EnvClassificationTests(unittest.TestCase):
    def test_every_variable_the_code_reads_is_classified(self) -> None:
        names: set[str] = set()
        for path in SRC.rglob("*.py"):
            names.update(ENV_NAME.findall(path.read_text(encoding="utf-8")))
        self.assertGreater(len(names), 40)
        classified = (
            env_module.AUTO_ENV_SAFE_KEYS
            | set(env_module.AUTO_ENV_RESTRICT_ONLY_KEYS)
            | env_module.AUTO_ENV_BLOCKED_KEYS
        )
        self.assertEqual(sorted(names - classified), [])

    def test_classes_do_not_overlap(self) -> None:
        safe = env_module.AUTO_ENV_SAFE_KEYS
        restrict = set(env_module.AUTO_ENV_RESTRICT_ONLY_KEYS)
        blocked = env_module.AUTO_ENV_BLOCKED_KEYS
        self.assertEqual(safe & restrict, set())
        self.assertEqual(safe & blocked, set())
        self.assertEqual(restrict & blocked, set())

    def test_host_and_base_url_overrides_are_blocked(self) -> None:
        honoured = env_module.AUTO_ENV_SAFE_KEYS | set(env_module.AUTO_ENV_RESTRICT_ONLY_KEYS)
        for name in honoured:
            self.assertFalse(
                name.endswith(("_HOST", "_BASE_URL", "_HOSTS", "_ORIGINS", "_PORT")), name
            )
        self.assertIn("NINOVA_STATE_DIR", env_module.AUTO_ENV_BLOCKED_KEYS)


class PlantedEnvMailExfiltrationTests(_EnvCase):
    """The reported scenario end to end, against a fake IMAP factory."""

    def test_planted_env_cannot_redirect_the_mail_login(self) -> None:
        self.write_env(self.root, "NINOVA_MAIL_HOST=imap.attacker.example")
        clone = self.root / "cloned-repo" / "src"
        self.write_env(clone, "NINOVA_MAIL_HOST=imap.attacker.example")
        os.chdir(clone)
        # The real credentials come from the MCP client's own env block.
        os.environ["NINOVA_USERNAME"] = "student@itu.edu.tr"
        os.environ["NINOVA_PASSWORD"] = "not-a-real-secret"

        self.load()
        factory = _Factory()
        ItuMailClient(imap_factory=factory).status()

        self.assertEqual(factory.calls, [("imap.itu.edu.tr", 993)])

    def test_mail_host_from_process_environment_is_refused_before_connecting(self) -> None:
        os.environ["NINOVA_MAIL_HOST"] = "imap.attacker.example"
        os.environ["NINOVA_USERNAME"] = "student@itu.edu.tr"
        os.environ["NINOVA_PASSWORD"] = "not-a-real-secret"
        factory = _Factory()

        with self.assertRaises(ItuMailError) as caught:
            ItuMailClient(imap_factory=factory).status()

        self.assertEqual(factory.calls, [])
        self.assertIsNone(factory.connection.login_args)
        self.assertNotIn("not-a-real-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()


class RemoteKeyTests(unittest.TestCase):
    def test_a_discovered_file_cannot_choose_the_remote_api_key(self) -> None:
        self.assertIn("NINOVA_REMOTE_API_KEY", env_module.AUTO_ENV_BLOCKED_KEYS)
        self.assertNotIn("NINOVA_REMOTE_API_KEY", env_module.AUTO_ENV_SAFE_KEYS)
