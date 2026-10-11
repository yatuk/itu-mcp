"""Keychain credentials and the setup / connect / doctor / logout commands.

Everything is offline: ``keyring`` is a fake module in ``sys.modules``, the
home directory is ``tmp_path`` and the login check is replaced.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from ninova_mcp import cli, cli_setup, client_config, credentials, diagnostics
from ninova_mcp import env as env_module
from ninova_mcp.client import NinovaAuthError, NinovaCredentials

USERNAME = "student21@itu.edu.tr"
SECRET = "S3cret-fake-pass!"  # a made-up value that must never be printed
REAL_BACKEND = "keyring.backends.Windows"


def fake_keyring(backend_module: str = REAL_BACKEND, members: list[str] | None = None):
    module = types.ModuleType("keyring")
    store: dict[tuple[str, str], str] = {}

    def make(name: str):
        kind = type("FakeBackend", (), {})
        kind.__module__ = name
        return kind()

    backend = make(backend_module)
    if members is not None:
        backend.backends = [make(name) for name in members]

    def delete_password(service: str, key: str) -> None:
        del store[(service, key)]

    module.store = store
    module.get_keyring = lambda: backend
    module.get_password = lambda service, key: store.get((service, key))
    module.set_password = lambda service, key, value: store.__setitem__((service, key), value)
    module.delete_password = delete_password
    return module


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An empty profile: no credentials, no client, no keyring package."""
    for key in ("NINOVA_USERNAME", "NINOVA_PASSWORD", "NINOVA_MAIL_USERNAME", "NINOVA_MAIL_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("NINOVA_ENV_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("NINOVA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NINOVA_KEYRING", "1")
    monkeypatch.setitem(sys.modules, "keyring", None)  # "not installed"
    monkeypatch.setattr(client_config.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    env_module.reset_env_notices()
    yield tmp_path
    env_module.reset_env_notices()


@pytest.fixture
def keychain(home, monkeypatch):
    module = fake_keyring()
    monkeypatch.setitem(sys.modules, "keyring", module)
    return module


def run(argv: list[str]) -> int:
    try:
        cli.main(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def output(capsys) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


# --- credential seam -------------------------------------------------------


def test_environment_wins_over_keychain(keychain, monkeypatch):
    credentials.store_credentials(USERNAME, SECRET)
    monkeypatch.setenv("NINOVA_USERNAME", "env-user")
    monkeypatch.setenv("NINOVA_PASSWORD", "env-pass")
    resolved = credentials.resolve_credentials()
    assert (resolved.username, resolved.password, resolved.source) == (
        "env-user",
        "env-pass",
        "environment",
    )


def test_explicit_env_file_is_reported_as_env_file(home, keychain, monkeypatch):
    path = home / "explicit.env"
    path.write_text(f"NINOVA_USERNAME={USERNAME}\nNINOVA_PASSWORD={SECRET}\n", encoding="utf-8")
    monkeypatch.setenv("NINOVA_ENV_FILE", str(path))
    try:
        env_module.load_ninova_env()
        assert credentials.credential_source() == "env_file"
    finally:
        monkeypatch.delenv("NINOVA_USERNAME", raising=False)
        monkeypatch.delenv("NINOVA_PASSWORD", raising=False)


def test_keychain_only_setup_feeds_the_clients(keychain):
    backend = credentials.store_credentials(USERNAME, SECRET)
    assert backend.startswith(REAL_BACKEND)
    assert credentials.credential_source() == "keychain"
    loaded = NinovaCredentials.from_env()
    assert (loaded.username, loaded.password) == (USERNAME, SECRET)

    from ninova_mcp.mail_client import ItuMailClient

    mail = ItuMailClient()  # builds only; nothing connects
    assert (mail.username, mail.password) == (USERNAME, SECRET)


def test_missing_package_behaves_as_before_and_names_setup(home):
    assert credentials.credential_source() == "none"
    assert credentials.keychain_status()["reason"] == "not_installed"
    with pytest.raises(NinovaAuthError) as raised:
        NinovaCredentials.from_env()
    message = str(raised.value)
    assert "NINOVA_USERNAME and NINOVA_PASSWORD must both be set" in message
    assert "itu-mcp setup" in message


@pytest.mark.parametrize(
    ("backend", "members"),
    [
        ("keyrings.alt.file", None),
        ("keyring.backends.fail", None),
        ("keyring.backends.null", None),
        ("keyring.backends.chainer", [REAL_BACKEND, "keyrings.alt.file"]),
        ("keyring.backends.chainer", []),
    ],
)
def test_plaintext_and_null_backends_are_refused(home, monkeypatch, backend, members):
    module = fake_keyring(backend, members)
    module.store[(credentials.KEYRING_SERVICE, credentials.KEYRING_USERNAME_KEY)] = USERNAME
    module.store[(credentials.KEYRING_SERVICE, USERNAME)] = SECRET
    monkeypatch.setitem(sys.modules, "keyring", module)

    with pytest.raises(credentials.KeychainUnavailable) as raised:
        credentials.store_credentials(USERNAME, SECRET)
    assert raised.value.reason == "insecure_backend"
    assert credentials.credential_source() == "none"  # stored values are not read either


def test_chainer_of_real_backends_and_the_off_switch(home, monkeypatch):
    module = fake_keyring("keyring.backends.chainer", ["keyring.backends.SecretService"])
    monkeypatch.setitem(sys.modules, "keyring", module)
    credentials.store_credentials(USERNAME, SECRET)
    assert credentials.credential_source() == "keychain"
    monkeypatch.setenv("NINOVA_KEYRING", "0")
    assert credentials.credential_source() == "none"
    assert credentials.keychain_status()["reason"] == "disabled"


def test_values_never_appear_in_reprs_or_status(keychain):
    credentials.store_credentials("old@itu.edu.tr", "old-pass")
    credentials.store_credentials(USERNAME, SECRET)  # the old account's entry is dropped
    assert (credentials.KEYRING_SERVICE, "old@itu.edu.tr") not in keychain.store
    shown = repr(credentials.resolve_credentials()) + json.dumps(credentials.keychain_status())
    assert SECRET not in shown
    assert USERNAME not in shown


# --- connect ---------------------------------------------------------------


def test_connect_creates_a_missing_file_without_secrets(home, capsys):
    path = client_config.cursor_config_path()
    assert run(["connect", "cursor"]) == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    command, args = client_config.server_command()
    assert data == {"mcpServers": {"itu": {"command": command, "args": args}}}
    assert Path(command).is_absolute()
    assert path.read_text(encoding="utf-8").endswith("}\n")
    assert list(path.parent.glob("*.bak-*")) == []
    assert "NINOVA_PASSWORD" not in path.read_text(encoding="utf-8")


def test_connect_preserves_other_content_backs_up_and_is_idempotent(home, capsys):
    path = client_config.desktop_config_path()
    path.parent.mkdir(parents=True)
    original = {
        "globalShortcut": "Ctrl+Space",
        "mcpServers": {"other": {"command": "x", "env": {"TOKEN": "tok-123"}}},
        "şehir": "İstanbul",
    }
    raw = json.dumps(original, indent=4, ensure_ascii=False)
    path.write_text(raw, encoding="utf-8")

    assert run(["connect", "claude-desktop"]) == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data) == ["globalShortcut", "mcpServers", "şehir"]
    assert data["mcpServers"]["other"] == original["mcpServers"]["other"]
    assert list(data["mcpServers"]) == ["other", "itu"]
    assert "İstanbul" in path.read_text(encoding="utf-8")
    backups = list(path.parent.glob("claude_desktop_config.json.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == raw
    assert "tok-123" not in output(capsys)

    after_first = path.read_bytes()
    assert run(["connect", "claude-desktop"]) == 0
    assert path.read_bytes() == after_first
    assert len(list(path.parent.glob("*.bak-*"))) == 1
    assert "değişiklik yok" in output(capsys)


@pytest.mark.parametrize("content", ['{"mcpServers": {', "[1, 2]", '{"mcpServers": []}'])
def test_connect_leaves_an_unusable_file_untouched(home, capsys, content):
    path = client_config.cursor_config_path()
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    assert run(["connect", "cursor"]) == 1
    assert path.read_text(encoding="utf-8") == content
    assert list(path.parent.glob("*.bak-*")) == []
    assert '"itu"' in output(capsys)  # the block to add by hand


def test_connect_dry_run_prints_a_diff_and_writes_nothing(home, capsys):
    path = client_config.cursor_config_path()
    path.parent.mkdir(parents=True)
    raw = json.dumps({"mcpServers": {"other": {"command": "x", "env": {"K": "tok-123"}}}})
    path.write_text(raw, encoding="utf-8")
    assert run(["connect", "cursor", "--dry-run"]) == 0
    assert path.read_text(encoding="utf-8") == raw
    assert list(path.parent.glob("*.bak-*")) == []
    text = output(capsys)
    assert '+    "itu": {' in text
    assert "tok-123" not in text


def _config_with_password(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(
        {
            "mcpServers": {
                "itu": {
                    "command": "itu-mcp",
                    "env": {
                        "NINOVA_USERNAME": USERNAME,
                        "NINOVA_PASSWORD": SECRET,
                        "NINOVA_TOOLSETS": "ninova,obs",
                    },
                }
            }
        },
        indent=2,
    )
    path.write_text(raw, encoding="utf-8")
    return raw


def test_connect_yes_keeps_an_entry_that_holds_a_password(keychain, capsys):
    credentials.store_credentials(USERNAME, SECRET)
    path = client_config.cursor_config_path()
    raw = _config_with_password(path)
    assert run(["connect", "cursor", "--yes"]) == 0
    assert path.read_text(encoding="utf-8") == raw
    text = output(capsys)
    assert "düz metin" in text
    assert SECRET not in text


def test_connect_replaces_a_password_entry_only_after_a_yes(keychain, capsys, monkeypatch):
    credentials.store_credentials(USERNAME, SECRET)
    path = client_config.cursor_config_path()
    raw = _config_with_password(path)
    monkeypatch.setattr(cli_setup, "is_interactive", lambda: True)

    assert run(["connect", "cursor", "--dry-run"]) == 0
    assert path.read_text(encoding="utf-8") == raw
    assert SECRET not in output(capsys)

    monkeypatch.setattr("builtins.input", lambda prompt="": "h")
    assert run(["connect", "cursor"]) == 0
    assert path.read_text(encoding="utf-8") == raw

    monkeypatch.setattr("builtins.input", lambda prompt="": "e")
    assert run(["connect", "cursor"]) == 0
    entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["itu"]
    assert entry["env"] == {"NINOVA_TOOLSETS": "ninova,obs"}
    assert SECRET not in path.read_text(encoding="utf-8")
    text = output(capsys)
    assert SECRET not in text
    assert "yedek" in text  # the backup still holds the old password, and says so


def test_connect_does_not_strip_the_password_when_the_keychain_is_empty(keychain, capsys):
    path = client_config.cursor_config_path()
    raw = _config_with_password(path)
    assert run(["connect", "cursor"]) == 0
    assert path.read_text(encoding="utf-8") == raw
    assert "itu-mcp setup" in output(capsys)


def test_connect_claude_code_runs_a_list_or_prints_the_command(home, capsys, monkeypatch):
    assert run(["connect", "claude-code"]) == 0
    assert "claude mcp add itu --scope user --" in output(capsys)

    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(argv)
        assert "shell" not in kwargs
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        client_config.shutil, "which", lambda name: "/bin/claude" if name == "claude" else None
    )
    monkeypatch.setattr(client_config.subprocess, "run", runner)
    assert run(["connect", "claude-code"]) == 0
    command, args = client_config.server_command()
    assert calls == [
        ["/bin/claude", "mcp", "add", "itu", "--scope", "user", "--", command, *args]
    ]


# --- doctor ----------------------------------------------------------------


def test_doctor_healthy_environment_exits_zero(keychain, capsys):
    credentials.store_credentials(USERNAME, SECRET)
    assert run(["connect", "cursor"]) == 0
    capsys.readouterr()
    assert run(["doctor"]) == 0
    text = output(capsys)
    assert "HATA" not in text
    assert "anahtar zinciri" in text
    assert "Cursor" in text
    assert SECRET not in text
    assert USERNAME not in text


def test_doctor_unhealthy_environment_names_the_fixes(home, capsys, monkeypatch):
    _config_with_password(client_config.cursor_config_path())
    monkeypatch.setenv("NINOVA_ENV_FILE", str(home / "missing.env"))
    monkeypatch.setattr(diagnostics, "_python_version", lambda: (3, 10, 4))

    assert run(["doctor"]) == 1
    text = output(capsys)
    assert "[HATA] Python 3.10.4" in text
    assert "itu-mcp setup" in text
    assert "missing.env" in text
    assert "düz metin" in text
    assert "[UYARI] Anahtar zinciri" in text
    assert SECRET not in text

    assert run(["doctor", "--lang", "en"]) == 1
    english = output(capsys)
    assert "[ERROR] Python 3.10.4" in english
    assert SECRET not in english


def test_doctor_reports_ignored_env_keys_and_online_checks(home, keychain, capsys, monkeypatch):
    credentials.store_credentials(USERNAME, SECRET)
    (home / ".env").write_text("NINOVA_STATE_DIR=/somewhere\n", encoding="utf-8")
    monkeypatch.delenv("NINOVA_STATE_DIR")
    asked: list[tuple] = []
    monkeypatch.setattr(diagnostics, "verify_login", lambda *a: (asked.append(a), (True, ""))[1])
    monkeypatch.setattr(diagnostics, "latest_pypi_version", lambda: "99.0.0")

    assert run(["doctor"]) == 0
    assert asked == []  # no network check unless asked for
    assert "NINOVA_STATE_DIR" in output(capsys)

    assert run(["doctor", "--online"]) == 0
    text = output(capsys)
    assert asked == [()]
    assert "99.0.0" in text
    assert "pipx upgrade itu-mcp" in text


# --- setup and logout ------------------------------------------------------


def _answers(monkeypatch, username: str = USERNAME, password: str = SECRET) -> list[str]:
    prompts: list[str] = []
    monkeypatch.setattr(cli_setup, "is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": (prompts.append(prompt), username)[1])
    monkeypatch.setattr(cli_setup.getpass, "getpass", lambda prompt="": password)
    return prompts


def test_setup_stores_in_the_keychain_verifies_and_connects(home, keychain, capsys, monkeypatch):
    _answers(monkeypatch)
    (home / ".cursor").mkdir()
    checked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        diagnostics, "verify_login", lambda u, p: (checked.append((u, p)), (True, ""))[1]
    )

    assert run(["setup", "--yes"]) == 0
    assert keychain.store[(credentials.KEYRING_SERVICE, USERNAME)] == SECRET
    assert checked == [(USERNAME, SECRET)]
    config = client_config.cursor_config_path().read_text(encoding="utf-8")
    assert '"itu"' in config
    assert SECRET not in config
    assert SECRET not in output(capsys)


def test_setup_without_a_terminal_does_not_ask(keychain, capsys, monkeypatch):
    monkeypatch.setattr(cli_setup, "is_interactive", lambda: False)

    def fail(*args, **kwargs):
        raise AssertionError("must not prompt")

    monkeypatch.setattr("builtins.input", fail)
    monkeypatch.setattr(cli_setup.getpass, "getpass", fail)
    assert run(["setup"]) == 2
    assert "itu-mcp setup" in output(capsys)
    assert keychain.store == {}


def test_setup_without_a_keychain_prints_the_placeholder_snippet(home, capsys, monkeypatch):
    _answers(monkeypatch)
    monkeypatch.setattr(diagnostics, "verify_login", lambda u, p: pytest.fail("no verify asked"))
    assert run(["setup", "--no-verify", "--yes"]) == 0
    text = output(capsys)
    assert "<ITU_SIFRENIZ>" in text
    assert "keyring" in text
    assert SECRET not in text
    assert not client_config.cursor_config_path().exists()


def test_setup_reports_a_failed_login_without_the_password(keychain, capsys, monkeypatch):
    _answers(monkeypatch)

    class Refusing:
        def __init__(self) -> None:
            raise NinovaAuthError(f"rejected {SECRET}")

    monkeypatch.setattr("ninova_mcp.client.NinovaClient", Refusing)
    monkeypatch.setenv("NINOVA_USERNAME", "kept")
    assert run(["setup", "--yes", "--lang", "en"]) == 1
    text = output(capsys)
    assert "Login failed: rejected ***" in text
    assert SECRET not in text
    import os

    assert os.environ["NINOVA_USERNAME"] == "kept"  # the environment is put back
    assert "NINOVA_PASSWORD" not in os.environ


def test_logout_deletes_the_session_and_optionally_the_password(home, keychain, capsys):
    credentials.store_credentials(USERNAME, SECRET)
    session = home / "state" / "session.json"
    session.parent.mkdir()
    session.write_text("{}", encoding="utf-8")

    assert run(["logout"]) == 0
    assert not session.exists()
    assert credentials.keychain_has_credentials()
    assert "--forget-password" in output(capsys)

    assert run(["logout", "--forget-password"]) == 0
    assert keychain.store == {}
    assert SECRET not in output(capsys)


def test_no_arguments_still_starts_the_stdio_server(monkeypatch):
    started: list[str] = []
    server = types.SimpleNamespace(run=lambda: started.append("run"))
    monkeypatch.setattr(cli, "build_stdio_server", lambda: server)
    assert run([]) == 0
    assert started == ["run"]
