"""Tests for the .mcpb bundle's Python-version bootstrap shim."""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = ROOT / "mcpb" / "server" / "main.py"


def _load_bundle_main():
    # ninova_mcp must be importable because main.py imports it at module load.
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    spec = importlib.util.spec_from_file_location("_ninova_bundle_main", MAIN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class McpbBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_bundle_main()

    def test_min_version_is_311(self) -> None:
        self.assertEqual(self.mod.MIN_VERSION, (3, 11))

    def test_current_interpreter_detected_as_modern(self) -> None:
        # The test runner itself is >= 3.11, so this must be True.
        self.assertTrue(self.mod._interpreter_is_modern(sys.executable))

    def test_missing_interpreter_is_not_modern(self) -> None:
        self.assertFalse(
            self.mod._interpreter_is_modern("/nonexistent/python-does-not-exist")
        )

    def test_candidates_all_exist_and_deduped(self) -> None:
        candidates = self.mod._candidate_interpreters()
        self.assertIsInstance(candidates, list)
        self.assertEqual(len(candidates), len(set(candidates)))
        for candidate in candidates:
            self.assertTrue(os.path.exists(candidate), candidate)

    def test_importing_main_did_not_reexec_on_modern_python(self) -> None:
        # If the bootstrap had re-exec'd, this module would never have imported.
        self.assertTrue(hasattr(self.mod, "main"))


if __name__ == "__main__":
    unittest.main()
