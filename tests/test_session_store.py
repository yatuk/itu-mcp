from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from requests.cookies import create_cookie

from ninova_mcp.session_store import (
    clear_session,
    cookie_jar_to_list,
    list_to_cookie_jar,
    load_session,
    save_session,
)


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "session.json"
        self.env = patch.dict(
            "os.environ",
            {"NINOVA_SESSION_PERSIST": "1", "NINOVA_SESSION_MAX_AGE_SECONDS": "3600"},
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_roundtrip_cookies(self) -> None:
        jar = list_to_cookie_jar([])
        jar.set_cookie(
            create_cookie(
                name="ASP.NET_SessionId",
                value="abc123",
                domain="ninova.itu.edu.tr",
                path="/",
            )
        )
        save_session(
            self.path,
            username="user@itu.edu.tr",
            base_url="https://ninova.itu.edu.tr",
            jar=jar,
            login_method="requests",
        )
        restored = load_session(
            self.path,
            username="user@itu.edu.tr",
            base_url="https://ninova.itu.edu.tr",
        )
        self.assertIsNotNone(restored)
        names = {c.name for c in restored["jar"]}
        self.assertIn("ASP.NET_SessionId", names)
        self.assertEqual(restored["login_method"], "requests")

    def test_rejects_other_user(self) -> None:
        jar = list_to_cookie_jar(
            [{"name": "x", "value": "1", "domain": "ninova.itu.edu.tr", "path": "/"}]
        )
        save_session(
            self.path,
            username="a@itu.edu.tr",
            base_url="https://ninova.itu.edu.tr",
            jar=jar,
            login_method="requests",
        )
        self.assertIsNone(
            load_session(
                self.path,
                username="b@itu.edu.tr",
                base_url="https://ninova.itu.edu.tr",
            )
        )

    def test_expired_session(self) -> None:
        jar = list_to_cookie_jar(
            [{"name": "x", "value": "1", "domain": "ninova.itu.edu.tr", "path": "/"}]
        )
        save_session(
            self.path,
            username="a@itu.edu.tr",
            base_url="https://ninova.itu.edu.tr",
            jar=jar,
            login_method="requests",
        )
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["saved_at"] = time.time() - 10_000
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        with patch.dict("os.environ", {"NINOVA_SESSION_MAX_AGE_SECONDS": "60"}):
            self.assertIsNone(
                load_session(
                    self.path,
                    username="a@itu.edu.tr",
                    base_url="https://ninova.itu.edu.tr",
                )
            )

    def test_clear(self) -> None:
        self.path.write_text("{}", encoding="utf-8")
        clear_session(self.path)
        self.assertFalse(self.path.exists())

    def test_cookie_jar_helpers(self) -> None:
        items = cookie_jar_to_list(
            list_to_cookie_jar(
                [{"name": "c", "value": "v", "domain": "example.com", "path": "/"}]
            )
        )
        self.assertEqual(items[0]["name"], "c")
        self.assertEqual(items[0]["value"], "v")


if __name__ == "__main__":
    unittest.main()
