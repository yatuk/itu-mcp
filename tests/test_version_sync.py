"""The version is written in three files. A release must not ship with them apart."""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

from ninova_mcp.server_info import SERVER_VERSION

ROOT = Path(__file__).resolve().parents[1]


class VersionSyncTests(unittest.TestCase):
    def test_server_version_pyproject_and_server_json_agree(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        server_json = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        found = {
            "server_info.SERVER_VERSION": SERVER_VERSION,
            "pyproject.toml [project].version": pyproject["project"]["version"],
            "server.json version": server_json["version"],
        }
        for index, package in enumerate(server_json["packages"]):
            found[f"server.json packages[{index}].version"] = package["version"]
        self.assertEqual(
            len(set(found.values())),
            1,
            f"version strings disagree, update them together: {found}",
        )


if __name__ == "__main__":
    unittest.main()
