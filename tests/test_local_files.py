"""Where the file tools may read and write, and what an argument cannot switch off.

The paths and names in these tests are the kind an injected instruction would
ask for: the saved session cookies, a client config file, a start-up folder.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from ninova_mcp.client import NinovaError
from ninova_mcp.local_files import (
    DANGEROUS_EXTENSIONS,
    LocalFileError,
    create_new_file,
    resolve_output_dir,
    resolve_readable_file,
    safe_download_name,
)
from ninova_mcp.obs_client import ObsError
from ninova_mcp.operator_settings import OperatorSettingError, env_flag, extra_directories, max_download_bytes

from recording import RecordingAdapter, offline_app

NINOVA = "https://ninova.itu.edu.tr"
SECRET = "SECRET-SESSION-COOKIE"


def link_or_skip(test: unittest.TestCase, link: Path, target: Path) -> None:
    """Create a symbolic link, or skip where the OS account is not allowed to.

    Windows needs a privilege for symbolic links but not for directory
    junctions, so a directory falls back to a junction there.
    """
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        if sys.platform == "win32" and target.is_dir():
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return
        test.skipTest(f"symbolic links are not available here: {exc}")


class LocalFileCase(unittest.TestCase):
    def make(self, script: dict[str, Any] | None = None, **environment: str) -> tuple[Any, RecordingAdapter]:
        app, adapter = offline_app(self, script, **environment)
        self.state = app.state_dir.resolve()
        self.downloads = self.state / "downloads"
        self.downloads.mkdir(parents=True, exist_ok=True)
        (self.state / "session.json").write_text(json.dumps({"cookies": SECRET}), encoding="utf-8")
        (self.state / "tracking-state.json").write_text(json.dumps({"state": SECRET}), encoding="utf-8")
        self.outside = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="itu-outside-"))).resolve()
        (self.outside / "claude_desktop_config.json").write_text(
            json.dumps({"env": {"NINOVA_PASSWORD": SECRET}}), encoding="utf-8",
        )
        return app, adapter


class ReadConfinementTests(LocalFileCase):
    def assert_refused(self, app: Any, path: str) -> None:
        with self.assertRaises(NinovaError) as raised:
            app.read_resource_text(path=path)
        self.assertNotIn(SECRET, str(raised.exception))

    def test_download_then_read_by_returned_path(self) -> None:
        body = "Ödev 1: 12 Mart'a kadar teslim.".encode("utf-8")
        app, _ = self.make({NINOVA + "/f/odev.txt": (200, {"Content-Type": "text/plain"}, body)})
        saved = app.download_resource("/f/odev.txt")
        self.assertEqual(Path(saved["path"]).parent, self.downloads)
        result = app.read_resource_text(path=saved["path"])
        self.assertTrue(result["ok"])
        self.assertIn("12 Mart", result["text"])
        self.assertEqual(result["source"], "path")
        # A bare name is looked up in the downloads directory.
        self.assertIn("12 Mart", app.read_resource_text(path="odev.txt")["text"])
        nested = self.downloads / "hafta1" / "not.md"
        nested.parent.mkdir()
        nested.write_text("# Not", encoding="utf-8")
        self.assertEqual(app.read_resource_text(path=str(nested))["text"], "# Not")

    def test_state_files_are_never_readable(self) -> None:
        app, _ = self.make()
        for path in (
            str(self.state / "session.json"),
            str(self.state / "tracking-state.json"),
            str(self.downloads / ".." / "session.json"),
            "../session.json",
            "..\\session.json",
            "sub/../../session.json",
            str(self.state / "snapshots" / ".." / "session.json"),
        ):
            with self.subTest(path=path):
                self.assert_refused(app, path)

    def test_paths_outside_are_refused(self) -> None:
        app, _ = self.make()
        config = self.outside / "claude_desktop_config.json"
        candidates = [
            str(config),
            str(self.downloads / ".." / ".." / config.name),
            os.path.relpath(config, self.downloads),
            "~/.ninova_state/session.json",
            "~/.config/claude/claude_desktop_config.json",
            "\\\\attacker.example\\share\\notes.txt",
            "//attacker.example/share/notes.txt",
            "\\\\?\\C:\\Windows\\win.ini",
            "\\\\.\\C:\\Windows\\win.ini",
            "\\\\localhost\\C$\\Windows\\win.ini",
            "a\x00b.txt",
            "",
        ]
        if sys.platform == "win32":
            drive = self.state.drive
            candidates += [
                "C:\\Windows\\win.ini", "c:/windows/win.ini", f"{drive}session.json",
                f"{drive}..\\session.json", "Z:\\share\\notes.txt",
                str(self.state / "session.json").upper(), str(self.state / "session.json").swapcase(),
            ]
        else:
            candidates += ["/etc/passwd", "/etc/hosts"]
        for path in candidates:
            with self.subTest(path=path):
                if path == "":
                    with self.assertRaises(NinovaError):
                        app.read_resource_text(path=path)
                else:
                    self.assert_refused(app, path)

    def test_network_paths_are_refused_without_touching_the_filesystem(self) -> None:
        app, _ = self.make()
        with patch("pathlib.Path.resolve", side_effect=AssertionError("resolved")), \
                patch("pathlib.Path.is_file", side_effect=AssertionError("stat")), \
                patch("os.path.abspath", side_effect=AssertionError("abspath")):
            for path in ("\\\\attacker.example\\share\\x.txt", "//attacker.example/share/x.txt"):
                with self.subTest(path=path):
                    with self.assertRaises(LocalFileError):
                        resolve_readable_file(path, state_dir=self.state)

    def test_file_symlink_that_leaves_the_downloads_directory_is_refused(self) -> None:
        app, _ = self.make()
        file_link = self.downloads / "innocent.json"
        link_or_skip(self, file_link, self.state / "session.json")
        self.assert_refused(app, str(file_link))
        self.assert_refused(app, "innocent.json")

    def test_directory_link_or_junction_that_leaves_is_refused(self) -> None:
        app, _ = self.make()
        directory_link = self.downloads / "course"
        link_or_skip(self, directory_link, self.outside)
        self.assert_refused(app, str(directory_link / "claude_desktop_config.json"))
        self.assert_refused(app, "course/claude_desktop_config.json")
        state_link = self.downloads / "state"
        link_or_skip(self, state_link, self.state)
        self.assert_refused(app, "state/session.json")

    def test_symlink_that_stays_inside_is_allowed(self) -> None:
        app, _ = self.make()
        (self.downloads / "real.txt").write_text("inside", encoding="utf-8")
        link_or_skip(self, self.downloads / "alias.txt", self.downloads / "real.txt")
        self.assertEqual(app.read_resource_text(path="alias.txt")["text"], "inside")

    def test_operator_can_add_read_directories_but_never_the_state_files(self) -> None:
        app, _ = self.make()
        notes = self.outside / "notes.txt"
        notes.write_text("ders notu", encoding="utf-8")
        self.assert_refused(app, str(notes))
        roots = os.pathsep.join([str(self.outside), str(self.state), str(self.state.parent)])
        with patch.dict(os.environ, {"NINOVA_EXTRA_READ_DIRS": roots}):
            self.assertEqual(app.read_resource_text(path=str(notes))["text"], "ders notu")
            for path in (self.state / "session.json", self.state / "tracking-state.json"):
                with self.subTest(path=path):
                    self.assert_refused(app, str(path))
            (self.downloads / "ok.txt").write_text("ok", encoding="utf-8")
            self.assertEqual(app.read_resource_text(path=str(self.downloads / "ok.txt"))["text"], "ok")
        with patch.dict(os.environ, {"NINOVA_EXTRA_READ_DIRS": "relative/dir"}):
            with self.assertRaises(OperatorSettingError):
                app.read_resource_text(path=str(notes))

    def test_large_local_file_is_refused_with_a_reason(self) -> None:
        app, _ = self.make()
        big = self.downloads / "big.txt"
        big.write_bytes(b"x" * 2048)
        with patch("ninova_mcp.text_extract.MAX_EXTRACT_BYTES", 1024):
            result = app.read_resource_text(path=str(big))
        self.assertFalse(result["ok"])
        self.assertIn("extraction limit", result["error"])
        self.assertEqual(result["size_bytes"], 2048)
        self.assertIsNone(result["text"])

    def test_snapshot_path_is_confined_to_the_snapshot_directory(self) -> None:
        app, adapter = self.make()
        saved = app.snapshot_page("/Kampus1", label="kampus")
        self.assertEqual(app.diff_snapshot("/Kampus1", snapshot_path=saved["snapshot_path"])["snapshot_path"], saved["snapshot_path"])
        self.assertIn("snapshot_path", app.diff_snapshot("/Kampus1", snapshot_path=Path(saved["snapshot_path"]).name))
        outside_snapshot = self.outside / "snap.json"
        outside_snapshot.write_text(Path(saved["snapshot_path"]).read_text(encoding="utf-8"), encoding="utf-8")
        for path in (
            str(outside_snapshot), str(self.state / "session.json"), "../session.json",
            "\\\\attacker.example\\share\\snap.json", "//attacker.example/share/snap.json",
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(NinovaError, "inside the snapshot directory"):
                    app.diff_snapshot("/Kampus1", snapshot_path=path)
        self.assertEqual(set(adapter.hosts), {"ninova.itu.edu.tr"})


class DownloadConfinementTests(LocalFileCase):
    FILE = NINOVA + "/f/ders.pdf"

    def make_download(self, body: bytes = b"%PDF-1.4 ders", headers: dict[str, str] | None = None,
                      **environment: str) -> tuple[Any, RecordingAdapter]:
        reply = (200, {"Content-Type": "application/pdf", **(headers or {})}, body)
        return self.make({self.FILE: reply}, **environment)

    def files_outside_downloads(self) -> list[Path]:
        return [item for item in self.outside.rglob("*") if item.name != "claude_desktop_config.json"]

    def test_default_download_goes_to_the_downloads_directory(self) -> None:
        app, _ = self.make_download()
        result = app.download_resource("/f/ders.pdf")
        path = Path(result["path"])
        self.assertEqual((path.parent, path.name), (self.downloads, "ders.pdf"))
        self.assertEqual(path.read_bytes(), b"%PDF-1.4 ders")
        self.assertEqual(result["size_bytes"], 13)
        self.assertNotIn("filename_neutralised", result)
        sub = app.download_resource("/f/ders.pdf", output_dir="hafta1")
        self.assertEqual(Path(sub["path"]).parent, self.downloads / "hafta1")
        absolute = app.download_resource("/f/ders.pdf", output_dir=str(self.downloads / "hafta 2"))
        self.assertEqual(Path(absolute["path"]).parent, self.downloads / "hafta 2")

    def test_output_dir_outside_is_refused_and_nothing_is_requested(self) -> None:
        app, adapter = self.make_download()
        startup = self.outside / "Start Menu" / "Programs" / "Startup"
        candidates = [
            str(startup), str(self.outside), str(self.downloads / ".." / ".." / "x"), "../snapshots", "..",
            str(self.state), str(self.state / "snapshots"), "~/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup",
            "~", "\\\\attacker.example\\share", "//attacker.example/share", "\\\\?\\C:\\Temp", "a\x00b",
        ]
        if sys.platform == "win32":
            candidates += ["C:\\Windows\\Temp", f"{self.state.drive}..\\..\\evil", "Z:\\share"]
        else:
            candidates += ["/etc", "/var/tmp/evil"]
        for output_dir in candidates:
            with self.subTest(output_dir=output_dir):
                with self.assertRaises(NinovaError):
                    app.download_resource("/f/ders.pdf", output_dir=output_dir, filename="update.bat")
        self.assertEqual(adapter.hosts, [])
        self.assertFalse(startup.exists())
        self.assertEqual(self.files_outside_downloads(), [])
        self.assertEqual(sorted(item.name for item in self.state.iterdir()),
                         ["downloads", "session.json", "snapshots", "tracking-state.json"])

    def test_output_dir_through_a_link_that_leaves_is_refused(self) -> None:
        app, adapter = self.make_download()
        link_or_skip(self, self.downloads / "course", self.outside)
        for output_dir in ("course", "course/new", str(self.downloads / "course")):
            with self.subTest(output_dir=output_dir):
                with self.assertRaises(NinovaError):
                    app.download_resource("/f/ders.pdf", output_dir=output_dir)
        self.assertEqual(adapter.hosts, [])
        self.assertEqual(self.files_outside_downloads(), [])

    def test_operator_can_add_download_directories(self) -> None:
        app, _ = self.make_download()
        roots = os.pathsep.join([str(self.outside), str(self.state)])
        with patch.dict(os.environ, {"NINOVA_EXTRA_DOWNLOAD_DIRS": roots}):
            result = app.download_resource("/f/ders.pdf", output_dir=str(self.outside / "ders"))
            self.assertEqual(Path(result["path"]), self.outside / "ders" / "ders.pdf")
            # The state directory's own files stay out of reach even then.
            for output_dir in (str(self.state), str(self.state / "snapshots")):
                with self.subTest(output_dir=output_dir):
                    with self.assertRaises(NinovaError):
                        app.download_resource("/f/ders.pdf", output_dir=output_dir, filename="session.json")
        self.assertEqual(json.loads((self.state / "session.json").read_text(encoding="utf-8")), {"cookies": SECRET})
        # The read list does not grant writes.
        with patch.dict(os.environ, {"NINOVA_EXTRA_READ_DIRS": str(self.outside)}):
            with self.assertRaises(NinovaError):
                app.download_resource("/f/ders.pdf", output_dir=str(self.outside / "x"))

    def test_dangerous_file_types_are_neutralised(self) -> None:
        app, _ = self.make_download(body=b"@echo pwned")
        for name in ("update.bat", "x.lnk", "x.ps1", "Setup.EXE", "run.cmd", "a.vbs", "b.js", "c.hta", "d.scr",
                     "e.url", "f.msi", "g.jar", "h.desktop", "i.command", "j.pdf.exe", "k.exe.", "l.bat ", "m.lnk. ."):
            with self.subTest(name=name):
                result = app.download_resource("/f/ders.pdf", filename=name)
                saved = Path(result["path"])
                self.assertEqual(saved.suffix, ".download")
                self.assertTrue(result["filename_neutralised"])
                self.assertNotIn(saved.suffix.lower(), DANGEROUS_EXTENSIONS)
                self.assertEqual(saved.parent, self.downloads)
        for name in ("notes.pdf", "hw1.py", "lab.ipynb", "data.csv", "src.zip", "main.c", "rapor.docx", "evil.exe.pdf"):
            with self.subTest(name=name):
                result = app.download_resource("/f/ders.pdf", filename=name)
                self.assertEqual(Path(result["path"]).name, name)
                self.assertNotIn("filename_neutralised", result)

    def test_name_from_the_server_is_neutralised_too(self) -> None:
        for header, expected in (
            ('attachment; filename="update.bat"', "update.bat.download"),
            ("attachment; filename*=UTF-8''%2e%2e%2f%2e%2e%2fStartup%2fx.lnk", "-..-Startup-x.lnk.download"),
            ('attachment; filename="..\\\\..\\\\evil.ps1"', None),
        ):
            with self.subTest(header=header):
                app, _ = self.make_download(headers={"Content-Disposition": header})
                saved = Path(app.download_resource("/f/ders.pdf")["path"])
                self.assertEqual(saved.parent, self.downloads)
                self.assertEqual(saved.suffix, ".download")
                if expected:
                    self.assertEqual(saved.name, expected)

    def test_safe_download_name_rules(self) -> None:
        self.assertEqual(safe_download_name("ödev 1.pdf"), ("ödev 1.pdf", False))
        self.assertEqual(safe_download_name("rapor.pdf. . "), ("rapor.pdf", False))
        self.assertEqual(safe_download_name("  .gizli  "), ("gizli", False))
        self.assertEqual(safe_download_name("..."), ("download", False))
        self.assertEqual(safe_download_name(None), ("download", False))
        self.assertEqual(safe_download_name("a/b\\c:d.txt"), ("a-b-c-d.txt", False))
        self.assertEqual(safe_download_name("fdp.exe\u202egnp.txt")[0], "fdp.exegnp.txt")
        self.assertEqual(safe_download_name("x.LNK"), ("x.LNK.download", True))
        self.assertLessEqual(len(safe_download_name("a" * 500 + ".pdf")[0]), 180)
        self.assertTrue(safe_download_name("a" * 500 + ".pdf")[0].endswith(".pdf"))
        for reserved in ("CON", "con.txt", "NUL", "nul.tar.gz", "COM1", "com9.pdf", "LPT1.txt", "AUX", "PRN.pdf",
                         "CON .txt", "com\u00b9.txt", "CONIN$", "aux."):
            with self.subTest(reserved=reserved):
                with self.assertRaisesRegex(LocalFileError, "reserved Windows device name"):
                    safe_download_name(reserved)
        for fine in ("console.txt", "com10.txt", "nullable.pdf", "auxiliary.docx"):
            with self.subTest(fine=fine):
                self.assertEqual(safe_download_name(fine), (fine, False))

    def test_reserved_device_names_are_refused_before_the_request(self) -> None:
        app, adapter = self.make_download()
        for name in ("CON", "nul.txt", "COM1.pdf"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(NinovaError, "reserved Windows device name"):
                    app.download_resource("/f/ders.pdf", filename=name)
        self.assertEqual(adapter.hosts, [])
        app, _ = self.make_download(headers={"Content-Disposition": 'attachment; filename="NUL.pdf"'})
        with self.assertRaisesRegex(NinovaError, "reserved Windows device name"):
            app.download_resource("/f/ders.pdf")
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_existing_files_are_never_overwritten(self) -> None:
        app, _ = self.make_download()
        (self.downloads / "ders.pdf").write_bytes(b"mine")
        first = Path(app.download_resource("/f/ders.pdf")["path"])
        second = Path(app.download_resource("/f/ders.pdf", filename="ders.pdf")["path"])
        self.assertEqual((first.name, second.name), ("ders-2.pdf", "ders-3.pdf"))
        self.assertEqual((self.downloads / "ders.pdf").read_bytes(), b"mine")
        path, handle = create_new_file(self.downloads, "ders.pdf")
        handle.close()
        self.assertEqual(path.name, "ders-4.pdf")

    def test_size_cap_is_enforced_while_streaming(self) -> None:
        megabyte = 1024 * 1024
        app, _ = self.make_download(body=b"x" * (megabyte + 1), NINOVA_MAX_DOWNLOAD_MB="1")
        chunks_read: list[int] = []
        original = app.client.get

        def get(url: str, *, stream: bool = False) -> Any:
            response = original(url, stream=stream)
            self.assertTrue(stream)

            def iter_content(chunk_size: int = 1) -> Any:
                for _ in range(64):
                    chunks_read.append(chunk_size)
                    yield b"x" * chunk_size

            response.iter_content = iter_content
            return response

        with patch.object(app.client, "get", side_effect=get):
            with self.assertRaisesRegex(NinovaError, "larger than the 1 MiB limit"):
                app.download_resource("/f/ders.pdf")
        # Stopped as soon as the limit was crossed, not after reading everything.
        self.assertLess(len(chunks_read), 64)
        self.assertLessEqual(sum(chunks_read), megabyte + 64 * 1024)
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_declared_length_over_the_cap_is_refused_before_writing(self) -> None:
        app, _ = self.make_download(headers={"Content-Length": str(5 * 1024 * 1024)}, NINOVA_MAX_DOWNLOAD_MB="1")
        with self.assertRaisesRegex(NinovaError, "NINOVA_MAX_DOWNLOAD_MB"):
            app.download_resource("/f/ders.pdf")
        self.assertEqual(list(self.downloads.iterdir()), [])
        for value in ("0", "-1", "abc", "1.5"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"NINOVA_MAX_DOWNLOAD_MB": value}):
                    with self.assertRaises(OperatorSettingError):
                        max_download_bytes()
        with patch.dict(os.environ, {"NINOVA_MAX_DOWNLOAD_MB": ""}):
            self.assertEqual(max_download_bytes(), 200 * 1024 * 1024)

    def test_read_resource_text_save_download_uses_the_same_rules(self) -> None:
        body = "merhaba".encode()
        app, _ = self.make({NINOVA + "/f/a.txt": (200, {"Content-Type": "text/plain"}, body)})
        result = app.read_resource_text(url="/f/a.txt", save_download=True)
        self.assertEqual(Path(result["path"]), self.downloads / "a.txt")
        again = app.read_resource_text(url="/f/a.txt", save_download=True, filename="a.txt")
        self.assertEqual(Path(again["path"]).name, "a-2.txt")
        risky = app.read_resource_text(url="/f/a.txt", save_download=True, filename="../../run.bat")
        self.assertEqual(Path(risky["path"]).parent, self.downloads)
        self.assertEqual(Path(risky["path"]).suffix, ".download")
        with self.assertRaises(NinovaError):
            app.read_resource_text(url="/f/a.txt", save_download=True, filename="CON.txt")

    def test_resolve_output_dir_default_and_errors(self) -> None:
        self.make()
        self.assertEqual(resolve_output_dir(None, state_dir=self.state), self.downloads)
        self.assertEqual(resolve_output_dir("", state_dir=self.state), self.downloads)
        with self.assertRaises(LocalFileError):
            resolve_output_dir(str(self.outside), state_dir=self.state)


class TranscriptDirectoryTests(LocalFileCase):
    def make_obs(self) -> Any:
        app, _ = self.make()
        app._obs = MagicMock()
        app._obs.save_transcript_pdf.side_effect = lambda target, english=False: {"path": str(Path(target) / "transcript-tr.pdf")}
        return app

    def test_default_and_inside_directories_are_passed_on(self) -> None:
        app = self.make_obs()
        self.assertEqual(Path(app.obs_download_transcript()["path"]).parent, self.downloads / "obs")
        self.assertEqual(Path(app.obs_download_transcript(output_dir="belgeler")["path"]).parent, self.downloads / "belgeler")

    def test_outside_directory_is_refused_before_obs_is_contacted(self) -> None:
        app = self.make_obs()
        for output_dir in (str(self.outside), str(self.state), "..", "~/Desktop", "\\\\attacker.example\\share"):
            with self.subTest(output_dir=output_dir):
                with self.assertRaises(ObsError):
                    app.obs_download_transcript(output_dir=output_dir)
        app._obs.save_transcript_pdf.assert_not_called()
        app._obs.get_transcript_preview.assert_not_called()
        with patch.dict(os.environ, {"NINOVA_EXTRA_DOWNLOAD_DIRS": str(self.outside)}):
            result = app.obs_download_transcript(output_dir=str(self.outside / "belgeler"))
        self.assertEqual(Path(result["path"]).parent, self.outside / "belgeler")


PROFILE = {
    "kisiselBilgiler": {"adi": "Örnek", "kimlikNo": "12345678901", "pasaportNo": "U1234567"},
    "iletisimBilgiList": [{"iletisimBilgiList": [
        {"iletisimTipiAdiTR": "Cep Telefonu", "degeri": "+90 555 000 00 00"},
        {"iletisimTipiAdiTR": "Ev Adresi", "degeri": "Örnek Mah. No 1"},
        {"iletisimTipiAdiTR": "E-posta", "degeri": "ornek@itu.edu.tr"},
    ]}],
}
SENSITIVE_VALUES = ("12345678901", "U1234567", "+90 555 000 00 00", "Örnek Mah. No 1")


class SensitiveOutputTests(LocalFileCase):
    def make_obs(self, **environment: str) -> Any:
        app, _ = self.make(**environment)
        app._obs = MagicMock()
        app._obs.get_profile.return_value = PROFILE
        app._obs.get_contacts.return_value = PROFILE
        return app

    def test_argument_alone_does_not_switch_redaction_off(self) -> None:
        app = self.make_obs()
        for tool in (app.obs_get_profile, app.obs_get_contacts):
            with self.subTest(tool=tool.__name__):
                result = tool(include_sensitive=True)
                rendered = json.dumps(result, ensure_ascii=False)
                for value in SENSITIVE_VALUES:
                    self.assertNotIn(value, rendered)
                self.assertNotIn("ornek@itu.edu.tr", rendered)
                self.assertIn("iletisimBilgiList: E-posta", result["redacted_fields"])
                self.assertEqual(result["sensitive_output"], "disabled_by_operator")
                self.assertIn("NINOVA_ALLOW_SENSITIVE_OUTPUT=1", result["sensitive_output_note"])
                plain = tool()
                self.assertNotIn("sensitive_output", plain)
                self.assertNotIn("12345678901", json.dumps(plain, ensure_ascii=False))
        self.assertNotIn("sensitive_output", PROFILE)

    def test_operator_opt_in_is_required_and_the_argument_still_decides(self) -> None:
        for value in ("1", "true", "YES", "on"):
            with self.subTest(value=value):
                app = self.make_obs(NINOVA_ALLOW_SENSITIVE_OUTPUT=value)
                for tool in (app.obs_get_profile, app.obs_get_contacts):
                    full = json.dumps(tool(include_sensitive=True), ensure_ascii=False)
                    for item in SENSITIVE_VALUES:
                        self.assertIn(item, full)
                    self.assertNotIn("sensitive_output", full)
                    self.assertNotIn("12345678901", json.dumps(tool(), ensure_ascii=False))
        for value in ("0", "false", "no", "off", ""):
            with self.subTest(value=value):
                app = self.make_obs(NINOVA_ALLOW_SENSITIVE_OUTPUT=value)
                self.assertNotIn("12345678901", json.dumps(app.obs_get_profile(include_sensitive=True)))

    def test_unrecognised_switch_value_fails_clearly_and_reveals_nothing(self) -> None:
        app = self.make_obs(NINOVA_ALLOW_SENSITIVE_OUTPUT="maybe")
        with self.assertRaisesRegex(OperatorSettingError, "NINOVA_ALLOW_SENSITIVE_OUTPUT"):
            app.obs_get_profile(include_sensitive=True)
        self.assertNotIn("12345678901", json.dumps(app.obs_get_profile()))


class OperatorSettingTests(unittest.TestCase):
    def test_env_flag(self) -> None:
        with patch.dict(os.environ, {}):
            os.environ.pop("NINOVA_TEST_FLAG", None)
            self.assertFalse(env_flag("NINOVA_TEST_FLAG"))
            self.assertTrue(env_flag("NINOVA_TEST_FLAG", default=True))
            for value, expected in (("1", True), (" TRUE ", True), ("Yes", True), ("on", True),
                                    ("0", False), ("False", False), ("no", False), ("OFF", False), ("  ", False)):
                os.environ["NINOVA_TEST_FLAG"] = value
                self.assertIs(env_flag("NINOVA_TEST_FLAG"), expected, value)
            for value in ("2", "enable", "evet", "y"):
                os.environ["NINOVA_TEST_FLAG"] = value
                with self.assertRaisesRegex(OperatorSettingError, "NINOVA_TEST_FLAG"):
                    env_flag("NINOVA_TEST_FLAG")

    def test_extra_directories_must_be_absolute(self) -> None:
        with tempfile.TemporaryDirectory(prefix="itu-roots-") as first, tempfile.TemporaryDirectory(prefix="itu-roots-") as second:
            with patch.dict(os.environ, {"NINOVA_TEST_DIRS": os.pathsep.join([first, "", f" {second} "])}):
                self.assertEqual(extra_directories("NINOVA_TEST_DIRS"), [Path(first), Path(second)])
        with patch.dict(os.environ, {"NINOVA_TEST_DIRS": ""}):
            self.assertEqual(extra_directories("NINOVA_TEST_DIRS"), [])
        for value in ("downloads", "./x", "..", os.pathsep.join([tempfile.gettempdir(), "relative"])):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"NINOVA_TEST_DIRS": value}):
                    with self.assertRaises(OperatorSettingError):
                        extra_directories("NINOVA_TEST_DIRS")


if __name__ == "__main__":
    unittest.main()


class UploadSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="itu-upload-src-")))
        self.state = self.root / "state"
        (self.state / "downloads").mkdir(parents=True)
        (self.state / "session.json").write_text("{}", encoding="utf-8")

    def resolve(self, raw: str) -> Path:
        from ninova_mcp.local_files import resolve_upload_file

        return resolve_upload_file(raw, state_dir=self.state)

    def test_network_paths_are_refused_without_touching_them(self) -> None:
        from ninova_mcp.local_files import LocalFileError

        for raw in ("\\\\attacker-host\\share\\hw.pdf", "//attacker-host/share/hw.pdf", "\\\\?\\C:\\x"):
            with self.subTest(raw=raw), patch.object(Path, "resolve") as resolve:
                with self.assertRaises(LocalFileError):
                    self.resolve(raw)
                resolve.assert_not_called()

    def test_state_files_are_refused(self) -> None:
        from ninova_mcp.local_files import LocalFileError

        for raw in (
            str(self.state / "session.json"),
            str(self.state / "downloads" / ".." / "session.json"),
        ):
            with self.subTest(raw=raw), self.assertRaises(LocalFileError):
                self.resolve(raw)

    def test_homework_anywhere_else_and_downloads_are_accepted(self) -> None:
        homework = self.root / "hw.pdf"
        homework.write_bytes(b"x")
        downloaded = self.state / "downloads" / "sheet.pdf"
        downloaded.write_bytes(b"x")
        self.assertEqual(self.resolve(str(homework)), homework.resolve())
        self.assertEqual(self.resolve(str(downloaded)), downloaded.resolve())
