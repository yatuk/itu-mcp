"""Release pipeline pins: workflow actions, downloaded tools and the bundled runtime.

These tests read files only. They never run a workflow and never download.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import re
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$", re.MULTILINE)
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")


def _load_build_script():
    spec = importlib.util.spec_from_file_location("build_mcpb", ROOT / "scripts" / "build_mcpb.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow_text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _top_level_block(text: str, key: str) -> str:
    match = re.search(rf"^{key}:\n((?:[ \t]+.*\n|\n)*)", text, re.MULTILINE)
    return match.group(1) if match else ""


class WorkflowPinTests(unittest.TestCase):
    def test_every_action_is_pinned_to_a_commit_with_a_version_comment(self) -> None:
        count = 0
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for reference, rest in USES.findall(path.read_text(encoding="utf-8")):
                count += 1
                with self.subTest(workflow=path.name, action=reference):
                    self.assertRegex(reference, PINNED)
                    self.assertRegex(rest, r"#\s*v\d+\.\d+")
        self.assertGreaterEqual(count, 16)

    def test_every_workflow_defaults_to_read_only(self) -> None:
        for path in sorted(WORKFLOWS.glob("*.yml")):
            with self.subTest(workflow=path.name):
                block = _top_level_block(path.read_text(encoding="utf-8"), "permissions")
                lines = [line.strip() for line in block.splitlines() if line.strip()]
                self.assertEqual(lines, ["contents: read"])

    def test_checkouts_do_not_keep_the_token(self) -> None:
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            checkouts = text.count("uses: actions/checkout@")
            with self.subTest(workflow=path.name):
                self.assertEqual(text.count("persist-credentials: false"), checkouts)

    def test_only_the_release_job_can_write_contents(self) -> None:
        text = _workflow_text("build-mcpb.yml")
        self.assertEqual(text.count("contents: write"), 1)
        release = text[text.index("\n  release:") :]
        self.assertIn("contents: write", release)

    def test_only_the_registry_job_gets_an_oidc_token(self) -> None:
        text = _workflow_text("publish.yml")
        active = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        self.assertEqual(active.count("id-token: write"), 1)
        registry = active[active.index("\n  registry:") :]
        self.assertIn("id-token: write", registry)

    def test_publish_tools_are_pinned(self) -> None:
        text = _workflow_text("publish.yml")
        self.assertRegex(text, r"pip install build==\d+\.\d+\.\d+ twine==\d+\.\d+\.\d+")
        self.assertNotIn("releases/latest/", text)
        self.assertRegex(text, r"MCP_PUBLISHER_VERSION: v\d+\.\d+\.\d+")
        self.assertRegex(text, r"MCP_PUBLISHER_SHA256: [0-9a-f]{64}\b")
        # The checksum is checked before the archive is unpacked.
        self.assertLess(text.index("sha256sum --check"), text.index("tar -xzf"))
        self.assertNotRegex(text, r"curl[^\n]*\|\s*tar")


class BundleRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = _load_build_script()

    def _archive(self, directory: Path, members: dict[str, bytes]) -> Path:
        path = directory / "runtime.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755
                archive.addfile(info, io.BytesIO(data))
        return path

    def test_every_platform_has_a_sha256(self) -> None:
        triples = set(self.build.RUNTIME_TRIPLES.values())
        self.assertEqual(set(self.build.RUNTIME_SHA256), triples)
        for triple, digest in self.build.RUNTIME_SHA256.items():
            with self.subTest(triple=triple):
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(len(set(self.build.RUNTIME_SHA256.values())), len(triples))

    def test_checksum_mismatch_stops_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            archive = self._archive(directory, {"python/bin/python3": b"#!/bin/sh\n"})
            with self.assertRaises(SystemExit) as caught:
                self.build._verify_sha256(archive, "0" * 64)
            self.assertIn("SHA-256", str(caught.exception))

    def test_matching_checksum_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive = self._archive(Path(temp), {"python/bin/python3": b"x"})
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.build._verify_sha256(archive, digest)

    def test_download_with_a_wrong_checksum_extracts_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = self._archive(directory, {"python/bin/python3": b"tampered"})
            server_dir = directory / "server"
            server_dir.mkdir()

            def fake_retrieve(url: str, target: Path) -> None:
                self.assertTrue(url.startswith("https://github.com/astral-sh/python-build-standalone/"))
                Path(target).write_bytes(source.read_bytes())

            with patch.object(self.build.urllib.request, "urlretrieve", side_effect=fake_retrieve):
                with self.assertRaises(SystemExit):
                    self.build._download_runtime(server_dir)

            self.assertEqual(list(server_dir.iterdir()), [])

    def test_extraction_refuses_members_that_leave_the_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            archive = self._archive(directory, {"../escaped.txt": b"x"})
            target = directory / "out"
            target.mkdir()
            with self.assertRaises(tarfile.TarError):
                self.build._extract_runtime(archive, target)
            self.assertFalse((directory / "escaped.txt").exists())

    def test_extraction_keeps_ordinary_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            archive = self._archive(directory, {"python/bin/python3": b"#!/bin/sh\n"})
            target = directory / "out"
            target.mkdir()
            self.build._extract_runtime(archive, target)
            self.assertEqual((target / "python" / "bin" / "python3").read_bytes(), b"#!/bin/sh\n")

    def test_the_packer_is_pinned_to_an_exact_version(self) -> None:
        self.assertRegex(self.build.MCPB_CLI_VERSION, r"^\d+\.\d+\.\d+$")
        source = (ROOT / "scripts" / "build_mcpb.py").read_text(encoding="utf-8")
        self.assertNotIn('"@anthropic-ai/mcpb"', source)
        self.assertNotIn("fully_trusted", source)


if __name__ == "__main__":
    unittest.main()
