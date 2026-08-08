from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ninova_mcp.server import NinovaMcpApp


class ServerCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.env_patch = patch.dict(
            os.environ,
            {
                "NINOVA_USERNAME": "dummy",
                "NINOVA_PASSWORD": "dummy",
                "NINOVA_STATE_DIR": str(self.state_dir),
                "NINOVA_COURSE_CACHE_TTL_SECONDS": "120",
            },
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def test_list_courses_uses_cache_on_second_call(self) -> None:
        app = NinovaMcpApp()
        courses = [
            {
                "code": "BLG 101",
                "title": "Intro",
                "url": "https://ninova.itu.edu.tr/Sinif/1.2",
                "context": "BLG 101",
            }
        ]
        with patch.object(
            app,
            "get_dashboard",
            return_value={"courses": courses},
        ) as get_dashboard:
            first = app.list_courses()
            second = app.list_courses()

        self.assertEqual(first["source"], "live")
        self.assertEqual(second["source"], "cache")
        self.assertEqual(second["count"], 1)
        get_dashboard.assert_called_once()

    def test_list_courses_refresh_bypasses_cache(self) -> None:
        app = NinovaMcpApp()
        courses = [
            {
                "code": "BLG 101",
                "title": "Intro",
                "url": "https://ninova.itu.edu.tr/Sinif/1.2",
                "context": "BLG 101",
            }
        ]
        with patch.object(
            app,
            "get_dashboard",
            return_value={"courses": courses},
        ) as get_dashboard:
            app.list_courses()
            app.list_courses(refresh=True)

        self.assertEqual(get_dashboard.call_count, 2)

    def test_refresh_session_invalidates_cache(self) -> None:
        app = NinovaMcpApp()
        app._course_cache.set(
            "courses",
            [
                {
                    "code": "X",
                    "title": "Y",
                    "url": "https://ninova.itu.edu.tr/Sinif/1.2",
                    "context": "X",
                }
            ],
        )
        with patch.object(
            app.client,
            "login",
            return_value={"login_method": "mock"},
        ):
            # Avoid real client construction network: client property creates NinovaClient
            # which only reads env, not network until login.
            app.refresh_session()
        self.assertIsNone(app._course_cache.get("courses"))

    def test_resolve_course_uses_cached_list(self) -> None:
        app = NinovaMcpApp()
        with patch.object(
            app,
            "list_courses",
            return_value={
                "count": 1,
                "courses": [
                    {
                        "code": "BLG 101",
                        "title": "Intro",
                        "url": "https://ninova.itu.edu.tr/Sinif/1.2",
                        "context": "BLG 101 Intro",
                    }
                ],
                "source": "cache",
            },
        ) as list_courses:
            resolved = app._resolve_course("BLG 101")

        self.assertEqual(resolved["code"], "BLG 101")
        list_courses.assert_called_once()


class LazyClientConcurrencyTests(unittest.TestCase):
    """The remote transport dispatches sync tool methods to a thread pool,
    so two requests can race to construct the same lazy client. Without a
    lock, both could see None and each build (and independently log in
    with) their own NinovaClient — see NinovaMcpApp._client_lock.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_patch = patch.dict(
            os.environ,
            {
                "NINOVA_USERNAME": "dummy",
                "NINOVA_PASSWORD": "dummy",
                "NINOVA_STATE_DIR": self.temp_dir.name,
            },
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def test_concurrent_property_access_builds_exactly_one_client(self) -> None:
        import threading

        app = NinovaMcpApp()
        seen_ids: list[int] = []
        start = threading.Barrier(8)

        def touch() -> None:
            start.wait()
            seen_ids.append(id(app.client))

        threads = [threading.Thread(target=touch) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(set(seen_ids)), 1)

    def test_obs_property_does_not_deadlock_on_first_access(self) -> None:
        """obs's property reads self.client while still holding the shared
        lock (to build ObsClient(ninova_client=self.client)) — this only
        works because the lock is reentrant. A plain, non-reentrant Lock
        here would deadlock every single first call to `app.obs`.
        """
        app = NinovaMcpApp()
        obs = app.obs
        self.assertIsNotNone(obs)
        self.assertIs(obs.ninova, app.client)


if __name__ == "__main__":
    unittest.main()
