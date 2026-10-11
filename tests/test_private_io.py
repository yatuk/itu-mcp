"""Private state files: owner-only modes on POSIX, atomic replacement everywhere."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from requests.cookies import RequestsCookieJar, create_cookie

from ninova_mcp import private_io
from ninova_mcp.private_io import ensure_private_dir, write_private_bytes, write_private_text
from ninova_mcp.seat_watch import save_seat_watch_state
from ninova_mcp.session_store import save_session
from ninova_mcp.tracking import save_tracking_state

POSIX = os.name == "posix"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _jar() -> RequestsCookieJar:
    jar = RequestsCookieJar()
    jar.set_cookie(create_cookie(name="sid", value="abc", domain="ninova.itu.edu.tr", path="/"))
    return jar


def _save_session(path: Path) -> None:
    save_session(
        path,
        username="user@itu.edu.tr",
        base_url="https://ninova.itu.edu.tr",
        jar=_jar(),
        login_method="requests",
    )


class _TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        if POSIX:
            # A permissive umask, so only an explicit mode can make a file private.
            previous = os.umask(0)
            self.addCleanup(os.umask, previous)
        env = patch.dict(os.environ, {"NINOVA_SESSION_PERSIST": "1"}, clear=False)
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def leftovers(self, directory: Path) -> list[str]:
        return sorted(p.name for p in directory.iterdir() if p.name.endswith(".tmp"))


class AtomicWriteTests(_TempCase):
    def test_write_replaces_the_previous_content(self) -> None:
        path = self.root / "state" / "file.json"

        write_private_text(path, "first")
        write_private_text(path, "second")

        self.assertEqual(path.read_text(encoding="utf-8"), "second")
        self.assertEqual(self.leftovers(path.parent), [])

    def test_bytes_are_written_unchanged(self) -> None:
        path = self.root / "file.bin"
        data = b"%PDF-1.7\r\n\x00\xff line\n"

        write_private_bytes(path, data)

        self.assertEqual(path.read_bytes(), data)

    def test_failure_between_write_and_replace_keeps_the_old_file(self) -> None:
        path = self.root / "file.json"
        write_private_text(path, "old")

        with patch.object(private_io.os, "replace", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                write_private_text(path, "new")

        self.assertEqual(path.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.leftovers(path.parent), [])

    def test_a_stale_temp_file_does_not_block_the_next_write(self) -> None:
        path = self.root / "file.json"
        (self.root / "file.json.tmp").write_text("stale", encoding="utf-8")

        write_private_text(path, "fresh")

        self.assertEqual(path.read_text(encoding="utf-8"), "fresh")

    def test_the_temp_file_lives_in_the_target_directory(self) -> None:
        path = self.root / "state" / "file.json"
        seen: list[tuple[Path, Path]] = []
        real_replace = os.replace

        def spy(src: str, dst: str) -> None:
            seen.append((Path(src), Path(dst)))
            real_replace(src, dst)

        with patch.object(private_io.os, "replace", side_effect=spy):
            write_private_text(path, "x")

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0].parent, path.parent)
        self.assertEqual(seen[0][1], path)


class StateWriterAtomicityTests(_TempCase):
    def writers(self):
        return (
            ("session", lambda p: _save_session(p)),
            ("tracking", lambda p: save_tracking_state(p, {"courses": {}, "updates": []})),
            ("seat_watch", lambda p: save_seat_watch_state(p, {"watches": {}})),
        )

    def test_each_state_writer_keeps_the_old_file_when_the_replace_fails(self) -> None:
        for name, write in self.writers():
            with self.subTest(writer=name):
                path = self.root / name / "state.json"
                path.parent.mkdir()
                path.write_text('{"old": true}', encoding="utf-8")

                with patch.object(private_io.os, "replace", side_effect=OSError("crash")):
                    with self.assertRaises(OSError):
                        write(path)

                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"old": True})
                self.assertEqual(self.leftovers(path.parent), [])

    def test_each_state_writer_writes_valid_json(self) -> None:
        for name, write in self.writers():
            with self.subTest(writer=name):
                path = self.root / name / "nested" / "state.json"

                write(path)

                self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)
                self.assertEqual(self.leftovers(path.parent), [])


@unittest.skipUnless(POSIX, "POSIX permission bits; Windows relies on the profile directory ACL")
class PosixModeTests(_TempCase):
    def test_file_is_owner_only_even_with_a_permissive_umask(self) -> None:
        path = self.root / "file.json"

        write_private_text(path, "secret")

        self.assertEqual(_mode(path), 0o600)

    def test_temp_file_is_owner_only_before_it_is_renamed(self) -> None:
        path = self.root / "file.json"
        modes: list[int] = []
        real_replace = os.replace

        def spy(src: str, dst: str) -> None:
            modes.append(_mode(Path(src)))
            real_replace(src, dst)

        with patch.object(private_io.os, "replace", side_effect=spy):
            write_private_text(path, "secret")

        self.assertEqual(modes, [0o600])

    def test_rewriting_a_world_readable_file_makes_it_private(self) -> None:
        path = self.root / "file.json"
        path.write_text("old", encoding="utf-8")
        os.chmod(path, 0o644)

        write_private_text(path, "new")

        self.assertEqual(_mode(path), 0o600)

    def test_new_directory_is_owner_only(self) -> None:
        directory = self.root / "state"

        ensure_private_dir(directory)

        self.assertEqual(_mode(directory), 0o700)

    def test_existing_wider_directory_is_tightened(self) -> None:
        directory = self.root / "state"
        directory.mkdir(mode=0o755)
        os.chmod(directory, 0o755)

        ensure_private_dir(directory)

        self.assertEqual(_mode(directory), 0o700)

    def test_session_tracking_and_seat_watch_files_are_owner_only(self) -> None:
        writers = (
            ("session.json", _save_session),
            ("tracking-state.json", lambda p: save_tracking_state(p, {"courses": {}})),
            ("seat-watch.json", lambda p: save_seat_watch_state(p, {"watches": {}})),
        )
        for name, write in writers:
            with self.subTest(file=name):
                path = self.root / "state" / name

                write(path)

                self.assertEqual(_mode(path), 0o600)
                self.assertEqual(_mode(path.parent), 0o700)

    def test_session_file_is_never_world_readable_while_being_written(self) -> None:
        path = self.root / "state" / "session.json"
        modes: list[int] = []
        real_replace = os.replace

        def spy(src: str, dst: str) -> None:
            modes.append(_mode(Path(src)))
            real_replace(src, dst)

        with patch("os.replace", side_effect=spy):
            _save_session(path)

        self.assertEqual(modes, [0o600])


if __name__ == "__main__":
    unittest.main()


class DownloadAndSnapshotFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="itu-private-files-")))

    def test_new_download_file_is_exclusive_and_numbered(self) -> None:
        from ninova_mcp.local_files import create_new_file

        first, handle = create_new_file(self.root, "notes.pdf")
        handle.write(b"one")
        handle.close()
        second, handle = create_new_file(self.root, "notes.pdf")
        handle.close()
        self.assertEqual(first.read_bytes(), b"one")
        self.assertEqual(second.name, "notes-2.pdf")

    @unittest.skipUnless(POSIX, "POSIX permission bits; Windows relies on the profile directory ACL")
    def test_download_file_and_directory_are_owner_only(self) -> None:
        from ninova_mcp.local_files import create_new_file, resolve_output_dir

        state = self.root / "state"
        previous = os.umask(0o022)
        try:
            target = resolve_output_dir("course", state_dir=state)
            path, handle = create_new_file(target, "notes.pdf")
            handle.close()
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((state / "downloads").stat().st_mode), 0o700)
