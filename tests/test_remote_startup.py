"""Startup rules and unauthenticated surface of the remote HTTP server.

Everything runs offline against the app built by ``remote.build_app`` with a
fake app object: no credentials are read and no university system is called.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

from ninova_mcp import remote
from ninova_mcp.remote_security import RemoteConfigError, validate_mcp_mount_path
from ninova_mcp.server import NinovaMcpApp

ROOT = Path(__file__).resolve().parents[1]
KEY = "k" * 40
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


class _FakeApp(NinovaMcpApp):
    """Real tool methods (for registration), fake login check."""

    def __init__(self) -> None:
        super().__init__()
        self.auth_calls = 0
        self.auth_threads: set[int] = set()
        self.authenticated = True

    def auth_status(self) -> dict[str, Any]:
        self.auth_calls += 1
        self.auth_threads.add(threading.get_ident())
        return {
            "authenticated": self.authenticated,
            "credentials_present": True,
            "state_dir": "/secret/state/dir",
        }


class _RemoteCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base_env = {
            # An explicit file that does not exist: no .env is ever loaded.
            "NINOVA_ENV_FILE": os.path.join(tmp.name, "missing.env"),
            "NINOVA_STATE_DIR": tmp.name,
        }

    def build(self, env: dict[str, str]) -> tuple[Any, _FakeApp, str]:
        """Build the app under exactly ``env``; return (app, fake, stderr)."""
        stderr = io.StringIO()
        with patch.dict(os.environ, {**self.base_env, **env}, clear=True):
            fake = _FakeApp()
            with contextlib.redirect_stderr(stderr):
                app = remote.build_app(fake)
        return app, fake, stderr.getvalue()

    def refuse(self, env: dict[str, str]) -> str:
        with patch.dict(os.environ, {**self.base_env, **env}, clear=True):
            with self.assertRaises(RemoteConfigError) as raised:
                with contextlib.redirect_stderr(io.StringIO()):
                    remote.build_app(_FakeApp())
        return str(raised.exception)


def _raw_status(app: Any, method: str, path: str, headers: dict[str, str] | None = None) -> int:
    """Call the ASGI app with an exact path, as the server would hand it over.

    An HTTP client library would tidy up ``..`` and doubled slashes first.
    """
    sent: list[dict[str, Any]] = []
    raw_headers = [(b"host", b"127.0.0.1:8000")]
    raw_headers += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


class StartupPolicyTests(_RemoteCase):
    """H2: an exposed server must not start open by default."""

    def test_non_loopback_without_key_refuses(self) -> None:
        message = self.refuse({"NINOVA_REMOTE_HOST": "0.0.0.0", "NINOVA_ALLOWED_HOSTS": "mcp.example.com"})
        self.assertIn("NINOVA_REMOTE_API_KEY", message)
        self.assertIn("NINOVA_REMOTE_ALLOW_UNAUTHENTICATED=1", message)

    def test_docker_image_defaults_refuse(self) -> None:
        # The image sets only the bind host.
        self.refuse({"NINOVA_REMOTE_HOST": "0.0.0.0"})

    def test_require_api_key_zero_is_not_an_opt_out(self) -> None:
        self.refuse({
            "NINOVA_REMOTE_HOST": "0.0.0.0",
            "NINOVA_ALLOWED_HOSTS": "mcp.example.com",
            "NINOVA_REMOTE_REQUIRE_API_KEY": "0",
        })

    def test_non_loopback_without_allowed_hosts_refuses(self) -> None:
        message = self.refuse({"NINOVA_REMOTE_HOST": "0.0.0.0", "NINOVA_REMOTE_API_KEY": KEY})
        self.assertIn("NINOVA_ALLOWED_HOSTS", message)
        self.assertIn("NINOVA_PUBLIC_BASE_URL", message)
        self.assertIn("NINOVA_DISABLE_DNS_REBINDING_PROTECTION=1", message)

    def test_other_non_loopback_spellings_refuse(self) -> None:
        for host in ("::", "192.168.1.5", "example.com", "[::]"):
            with self.subTest(host=host):
                self.refuse({"NINOVA_REMOTE_HOST": host})

    def test_non_loopback_with_key_and_hosts_validates_host(self) -> None:
        for env in (
            {"NINOVA_ALLOWED_HOSTS": "mcp.example.com"},
            {"NINOVA_PUBLIC_BASE_URL": "https://mcp.example.com"},
        ):
            with self.subTest(env=env):
                app, _, stderr = self.build({"NINOVA_REMOTE_HOST": "0.0.0.0", "NINOVA_REMOTE_API_KEY": KEY, **env})
                self.assertNotIn("WARNING", stderr)
                auth = {"Authorization": f"Bearer {KEY}"}
                with TestClient(app) as client:
                    evil = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, **auth, "Host": "evil.example:8000"})
                    self.assertEqual(evil.status_code, 421)
                    no_key = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Host": "mcp.example.com"})
                    self.assertEqual(no_key.status_code, 401)
                    bad_origin = client.post(
                        "/mcp", json=INIT,
                        headers={**MCP_HEADERS, **auth, "Host": "mcp.example.com", "Origin": "https://evil.example"},
                    )
                    self.assertEqual(bad_origin.status_code, 403)
                    good = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, **auth, "Host": "mcp.example.com"})
                    self.assertEqual(good.status_code, 200)
                    self.assertEqual(good.json()["result"]["serverInfo"]["name"], "itu-mcp")

    def test_unauthenticated_opt_out_starts_with_loud_warning(self) -> None:
        app, _, stderr = self.build({
            "NINOVA_REMOTE_HOST": "0.0.0.0",
            "NINOVA_ALLOWED_HOSTS": "mcp.example.com",
            "NINOVA_REMOTE_ALLOW_UNAUTHENTICATED": "1",
        })
        self.assertIn("WARNING", stderr)
        self.assertIn("NINOVA_REMOTE_ALLOW_UNAUTHENTICATED=1", stderr)
        self.assertIn("WITHOUT a key", stderr)
        with TestClient(app) as client:
            open_call = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Host": "mcp.example.com"})
            self.assertEqual(open_call.status_code, 200)
            # The Host check is a separate switch and stays on.
            evil = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Host": "evil.example"})
            self.assertEqual(evil.status_code, 421)

    def test_rebinding_opt_out_starts_with_loud_warning(self) -> None:
        app, _, stderr = self.build({
            "NINOVA_REMOTE_HOST": "0.0.0.0",
            "NINOVA_REMOTE_API_KEY": KEY,
            "NINOVA_DISABLE_DNS_REBINDING_PROTECTION": "1",
        })
        self.assertIn("WARNING", stderr)
        self.assertIn("NINOVA_DISABLE_DNS_REBINDING_PROTECTION=1", stderr)
        with TestClient(app) as client:
            headers = {**MCP_HEADERS, "Host": "anything.example"}
            self.assertEqual(client.post("/mcp", json=INIT, headers=headers).status_code, 401)
            headers["Authorization"] = f"Bearer {KEY}"
            self.assertEqual(client.post("/mcp", json=INIT, headers=headers).status_code, 200)

    def test_require_api_key_refuses_everywhere_and_beats_the_opt_out(self) -> None:
        for extra in ({}, {"NINOVA_REMOTE_ALLOW_UNAUTHENTICATED": "1"}):
            with self.subTest(extra=extra):
                message = self.refuse({"NINOVA_REMOTE_REQUIRE_API_KEY": "1", **extra})
                self.assertIn("NINOVA_REMOTE_API_KEY", message)

    def test_loopback_without_key_generates_one_and_prints_it_once(self) -> None:
        app, _, stderr = self.build({})
        lines = [line for line in stderr.splitlines() if line.startswith("[itu-mcp-remote]     ")]
        self.assertEqual(len(lines), 1)
        generated = lines[0].split()[-1]
        self.assertGreaterEqual(len(generated), 32)
        self.assertIn("NINOVA_REMOTE_API_KEY", stderr)
        with TestClient(app) as client:
            headers = {**MCP_HEADERS, "Host": "127.0.0.1:8000"}
            self.assertEqual(client.post("/mcp", json=INIT, headers=headers).status_code, 401)
            ok = client.post("/mcp", json=INIT, headers={**headers, "Authorization": f"Bearer {generated}"})
            self.assertEqual(ok.status_code, 200)
        _, _, second = self.build({})
        self.assertNotIn(generated, second)

    def test_loopback_always_validates_host_and_origin(self) -> None:
        for env in ({}, {"NINOVA_REMOTE_HOST": "localhost"}, {"NINOVA_REMOTE_HOST": "127.0.0.2"}):
            with self.subTest(env=env):
                app, _, _ = self.build({"NINOVA_REMOTE_API_KEY": KEY, **env})
                auth = {**MCP_HEADERS, "Authorization": f"Bearer {KEY}"}
                with TestClient(app) as client:
                    evil = client.post("/mcp", json=INIT, headers={**auth, "Host": "evil.example:8000"})
                    self.assertEqual(evil.status_code, 421)
                    origin = client.post(
                        "/mcp", json=INIT,
                        headers={**auth, "Host": "127.0.0.1:8000", "Origin": "https://evil.example"},
                    )
                    self.assertEqual(origin.status_code, 403)
                    good = client.post("/mcp", json=INIT, headers={**auth, "Host": "127.0.0.1:8000"})
                    self.assertEqual(good.status_code, 200)

    def test_loopback_opt_out_runs_open_but_still_checks_host(self) -> None:
        app, _, stderr = self.build({"NINOVA_REMOTE_ALLOW_UNAUTHENTICATED": "1"})
        self.assertIn("WARNING", stderr)
        with TestClient(app) as client:
            ok = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Host": "localhost:8000"})
            self.assertEqual(ok.status_code, 200)
            evil = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Host": "evil.example:8000"})
            self.assertEqual(evil.status_code, 421)

    def test_transport_security_is_never_none(self) -> None:
        for env in ({}, {"NINOVA_DISABLE_DNS_REBINDING_PROTECTION": "1"}):
            with self.subTest(env=env), patch.dict(os.environ, {**self.base_env, **env}, clear=True):
                settings = remote._build_transport_security("127.0.0.1", [])
                self.assertIsNotNone(settings)
                self.assertEqual(settings.enable_dns_rebinding_protection, not env)

    def test_short_key_warns(self) -> None:
        _, _, stderr = self.build({"NINOVA_REMOTE_API_KEY": "short"})
        self.assertIn("shorter than 16", stderr)

    def test_bad_numbers_name_the_variable(self) -> None:
        self.assertIn("PORT", self.refuse({"PORT": "abc"}))
        self.assertIn("NINOVA_REMOTE_RATE_LIMIT", self.refuse({"NINOVA_REMOTE_RATE_LIMIT": "x"}))
        self.assertIn(
            "NINOVA_REMOTE_TRUSTED_PROXY_HOPS",
            self.refuse({"NINOVA_REMOTE_TRUST_PROXY_HEADERS": "1", "NINOVA_REMOTE_TRUSTED_PROXY_HOPS": "0"}),
        )

    def test_importing_the_module_does_not_build_the_app(self) -> None:
        self.assertNotIn("app", vars(remote))

    def test_main_exits_with_the_variable_name(self) -> None:
        stderr = io.StringIO()
        env = {**self.base_env, "NINOVA_REMOTE_HOST": "0.0.0.0"}
        with patch.dict(os.environ, env, clear=True), patch.object(remote.uvicorn, "run") as run:
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                remote.main()
        self.assertEqual(raised.exception.code, 2)
        run.assert_not_called()
        self.assertIn("error: ", stderr.getvalue())
        self.assertIn("NINOVA_REMOTE_API_KEY", stderr.getvalue())

    def test_main_does_not_let_uvicorn_read_forwarded_headers(self) -> None:
        env = {**self.base_env, "NINOVA_REMOTE_API_KEY": KEY, "NINOVA_USERNAME": "", "NINOVA_PASSWORD": ""}
        with patch.dict(os.environ, env, clear=True), patch.object(remote.uvicorn, "run") as run:
            with contextlib.redirect_stderr(io.StringIO()):
                remote.main()
        self.assertIs(run.call_args.kwargs["proxy_headers"], False)
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")


class MountPathTests(_RemoteCase):
    """M1: the MCP app can never share a path with a public route."""

    def test_root_empty_and_status_paths_are_refused(self) -> None:
        for value in ("/", "", "  ", "//", "/healthz", "healthz", "/HEALTHZ/", "/healthz/mcp"):
            with self.subTest(value=value):
                message = self.refuse({"NINOVA_REMOTE_MCP_PATH": value, "NINOVA_REMOTE_API_KEY": KEY})
                self.assertIn("NINOVA_REMOTE_MCP_PATH", message)

    def test_ambiguous_paths_are_refused(self) -> None:
        for value in ("/mcp//x", "/a/../b", "/mcp%2f", "/mcp x", "/{path}", "/mcp?x=1", "/.", "/m\\cp"):
            with self.subTest(value=value):
                with self.assertRaises(RemoteConfigError):
                    validate_mcp_mount_path(value)

    def test_accepted_paths(self) -> None:
        self.assertEqual(validate_mcp_mount_path(None), "/mcp")
        self.assertEqual(validate_mcp_mount_path("mcp-Abc_123.~x/"), "/mcp-Abc_123.~x")
        self.assertEqual(validate_mcp_mount_path("/a/b"), "/a/b")

    def test_key_and_rate_limit_hold_on_the_mcp_path(self) -> None:
        for path in ("/mcp", "/mcp-long-secret", "/a/b"):
            with self.subTest(path=path):
                app, _, _ = self.build({
                    "NINOVA_REMOTE_MCP_PATH": path,
                    "NINOVA_REMOTE_API_KEY": KEY,
                    "NINOVA_REMOTE_REQUIRE_API_KEY": "1",
                    "NINOVA_REMOTE_RATE_LIMIT": "2",
                })
                with TestClient(app) as client:
                    headers = {**MCP_HEADERS, "Host": "127.0.0.1:8000"}
                    codes = [client.post(path, json=INIT, headers=headers).status_code for _ in range(4)]
                self.assertEqual(codes, [401, 401, 429, 429])

    def test_mcp_path_answers_without_a_redirect(self) -> None:
        # A redirect would be built from the request's scheme and Host, and
        # clients drop the Authorization header when it changes origin.
        app, _, _ = self.build({"NINOVA_REMOTE_API_KEY": KEY, "NINOVA_REMOTE_MCP_PATH": "/mcp-x"})
        headers = {**MCP_HEADERS, "Host": "127.0.0.1:8000", "Authorization": f"Bearer {KEY}"}
        with TestClient(app) as client:
            for path in ("/mcp-x", "/mcp-x/"):
                with self.subTest(path=path):
                    response = client.post(path, json=INIT, headers=headers, follow_redirects=False)
                    self.assertEqual(response.status_code, 200)

    def test_no_path_variant_is_served_without_the_key(self) -> None:
        app, _, _ = self.build({"NINOVA_REMOTE_API_KEY": KEY, "NINOVA_REMOTE_DISABLE_RATE_LIMIT": "1"})
        variants = [
            "/mcp", "/mcp/", "//mcp", "/mcp//", "/MCP", "/Mcp/", "/mcp/x", "/mcp/../mcp",
            "/./mcp", "/healthz/../mcp", "/healthz/", "//healthz", "/HEALTHZ", "/healthz/x",
            "//", "/%6dcp", "/mcp%2f", "/mcp;x", "/unknown", "",
        ]
        for method in ("GET", "POST", "DELETE", "OPTIONS", "PUT"):
            for path in variants:
                with self.subTest(method=method, path=path):
                    self.assertEqual(_raw_status(app, method, path, MCP_HEADERS), 401)
        # Only GET/HEAD on the two exact status paths are public.
        for path in ("/", "/healthz"):
            self.assertEqual(_raw_status(app, "GET", path), 200)
            self.assertEqual(_raw_status(app, "HEAD", path), 200)
            for method in ("POST", "DELETE", "OPTIONS", "PUT"):
                with self.subTest(method=method, path=path):
                    self.assertEqual(_raw_status(app, method, path), 401)

    def test_every_non_status_path_is_rate_limited(self) -> None:
        app, _, _ = self.build({"NINOVA_REMOTE_API_KEY": KEY, "NINOVA_REMOTE_RATE_LIMIT": "2"})
        codes = [_raw_status(app, "POST", path, MCP_HEADERS) for path in ("//mcp", "/MCP", "/unknown", "/mcp/")]
        self.assertEqual(codes, [401, 401, 429, 429])


class StatusEndpointTests(_RemoteCase):
    """L1: unauthenticated routes say nothing about the configuration."""

    SECRET_PATH = "/mcp-very-secret-path"

    def test_status_routes_carry_no_configuration(self) -> None:
        for check_auth in ("0", "1"):
            with self.subTest(check_auth=check_auth):
                app, _, _ = self.build({
                    "NINOVA_REMOTE_API_KEY": KEY,
                    "NINOVA_REMOTE_MCP_PATH": self.SECRET_PATH,
                    "NINOVA_HEALTHZ_CHECK_AUTH": check_auth,
                })
                with TestClient(app) as client:
                    root = client.get("/")
                    health = client.get("/healthz")
                self.assertEqual(root.json(), {"name": "itu-mcp", "status": "ok"})
                expected = {"ok": True, "authenticated": True} if check_auth == "1" else {"ok": True}
                self.assertEqual(health.json(), expected)
                for text in (root.text, health.text):
                    for leaked in ("secret", "state", "version", "api_key", "rate_limit", "credentials"):
                        self.assertNotIn(leaked, text)

    def test_auth_check_is_cached_and_runs_off_the_event_loop(self) -> None:
        app, fake, _ = self.build({"NINOVA_REMOTE_API_KEY": KEY, "NINOVA_HEALTHZ_CHECK_AUTH": "1"})
        with TestClient(app) as client:
            loop_thread = client.portal.call(threading.get_ident)
            codes = [client.get("/healthz").status_code for _ in range(10)]
        self.assertEqual(codes, [200] * 10)
        self.assertEqual(fake.auth_calls, 1)
        self.assertNotIn(loop_thread, fake.auth_threads)

    def test_auth_check_is_off_by_default(self) -> None:
        app, fake, _ = self.build({"NINOVA_REMOTE_API_KEY": KEY})
        with TestClient(app) as client:
            self.assertEqual(client.get("/healthz").status_code, 200)
        self.assertEqual(fake.auth_calls, 0)

    def test_failed_auth_check_is_cached_too_and_expires(self) -> None:
        from ninova_mcp.server_build import ToolCallGate

        now = [1000.0]
        answers = [False, True]
        calls: list[int] = []

        def probe() -> dict[str, Any]:
            calls.append(1)
            return {"authenticated": answers[len(calls) - 1]}

        check = remote.AuthHealthCheck(probe, gate=ToolCallGate(), ttl_seconds=60, clock=lambda: now[0])

        async def run() -> list[bool]:
            seen = [await check.authenticated(), await check.authenticated()]
            now[0] = 1059.0
            seen.append(await check.authenticated())
            now[0] = 1061.0
            seen.append(await check.authenticated())
            return seen

        self.assertEqual(asyncio.run(run()), [False, False, False, True])
        self.assertEqual(len(calls), 2)

    def test_auth_check_serves_last_result_while_a_tool_call_holds_the_lock(self) -> None:
        from ninova_mcp.server_build import ToolCallGate

        now = [1000.0]
        calls: list[int] = []
        gate = ToolCallGate()

        def probe() -> dict[str, Any]:
            calls.append(1)
            return {"authenticated": True}

        check = remote.AuthHealthCheck(probe, gate=gate, ttl_seconds=60, clock=lambda: now[0])

        async def run() -> bool:
            await check.authenticated()
            now[0] = 2000.0
            async with gate.lock():  # a tool call is running
                return await check.authenticated()

        self.assertTrue(asyncio.run(run()))
        self.assertEqual(len(calls), 1)

    def test_auth_check_error_is_a_plain_failure(self) -> None:
        def boom(self: Any) -> dict[str, Any]:
            raise RuntimeError("secret-path password=abc")

        with patch.object(_FakeApp, "auth_status", boom):
            app, _, _ = self.build({"NINOVA_REMOTE_API_KEY": KEY, "NINOVA_HEALTHZ_CHECK_AUTH": "1"})
            with TestClient(app) as client:
                response = client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"ok": False, "authenticated": False})

    def test_status_routes_are_rate_limited_in_their_own_bucket(self) -> None:
        app, _, _ = self.build({"NINOVA_REMOTE_API_KEY": KEY, "NINOVA_REMOTE_RATE_LIMIT": "2"})
        with TestClient(app) as client:
            codes = [client.get("/healthz").status_code for _ in range(2)] + [client.get("/").status_code]
            self.assertEqual(codes, [200, 200, 429])
            # Probes did not use up the MCP budget.
            headers = {**MCP_HEADERS, "Host": "127.0.0.1:8000", "Authorization": f"Bearer {KEY}"}
            self.assertEqual(client.post("/mcp", json=INIT, headers=headers).status_code, 200)


class DockerfileTests(unittest.TestCase):
    """The image must not be able to start an open server by default."""

    def setUp(self) -> None:
        self.text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.lines = [line.strip() for line in self.text.splitlines()]

    def test_runs_as_a_non_root_user(self) -> None:
        users = [line.split(None, 1)[1] for line in self.lines if line.startswith("USER ")]
        self.assertTrue(users)
        self.assertNotIn(users[-1].split(":")[0], {"root", "0"})

    def test_state_lives_in_a_declared_volume(self) -> None:
        self.assertIn("NINOVA_STATE_DIR=/data", self.text)
        self.assertTrue(any(line.startswith("VOLUME") and "/data" in line for line in self.lines))

    def test_sets_no_key_and_no_opt_out(self) -> None:
        for name in (
            "NINOVA_REMOTE_API_KEY",
            "NINOVA_REMOTE_ALLOW_UNAUTHENTICATED",
            "NINOVA_DISABLE_DNS_REBINDING_PROTECTION",
            "NINOVA_ALLOWED_HOSTS",
        ):
            self.assertFalse(
                any(f"{name}=" in line for line in self.lines if not line.startswith("#")), name
            )

    def test_does_not_upgrade_pip_unpinned(self) -> None:
        self.assertNotIn("--upgrade pip", self.text)


if __name__ == "__main__":
    unittest.main()


class IgnoredEnvFileSettingsTests(unittest.TestCase):
    def test_ignored_remote_setting_refuses_to_start(self) -> None:
        with patch.object(
            remote, "ignored_env_keys", return_value=("NINOVA_ALLOWED_HOSTS", "NINOVA_BASE_URL")
        ):
            with self.assertRaises(RemoteConfigError) as caught:
                remote._refuse_ignored_remote_settings()
        self.assertIn("NINOVA_ALLOWED_HOSTS", str(caught.exception))
        self.assertNotIn("NINOVA_BASE_URL", str(caught.exception))

    def test_unrelated_ignored_keys_do_not_block_startup(self) -> None:
        with patch.object(remote, "ignored_env_keys", return_value=("NINOVA_BASE_URL",)):
            remote._refuse_ignored_remote_settings()
