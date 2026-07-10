#!/usr/bin/env python3
"""Build a Claude Desktop extension (.mcpb) for itu-mcp.

The bundle is fully self-contained: it ships its own CPython runtime
(python-build-standalone) plus every dependency, so it works on a machine that
has no Python at all, and — crucially — never depends on the user's Python
minor version matching the compiled wheels (lxml, pydantic-core, etc.).

What it does, per platform:
  1. Downloads a relocatable CPython into ``server/runtime``.
  2. Uses THAT runtime's pip to vendor ``ninova_mcp`` + deps into ``server/lib``
     (so the compiled .so/.pyd files match the bundled interpreter exactly).
  3. Regenerates the manifest ``version`` and ``tools`` from the source of
     truth so they never drift.
  4. Packs everything into ``dist/itu-mcp-<version>-<platform>.mcpb``.

Usage:
    python scripts/build_mcpb.py
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MCPB_SRC = ROOT / "mcpb"
BUILD = ROOT / "build" / "mcpb"
DIST = ROOT / "dist"

# Pinned relocatable CPython (https://github.com/astral-sh/python-build-standalone).
RUNTIME_RELEASE = "20260623"
RUNTIME_PYTHON = "3.12.13"
RUNTIME_TRIPLES = {
    ("darwin", "arm64"): "aarch64-apple-darwin",
    ("darwin", "x86_64"): "x86_64-apple-darwin",
    ("windows", "amd64"): "x86_64-pc-windows-msvc",
    ("windows", "x86_64"): "x86_64-pc-windows-msvc",
    ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("linux", "aarch64"): "aarch64-unknown-linux-gnu",
}


def _read_version() -> str:
    """Read the version straight from pyproject.toml (no dependencies needed)."""
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def _read_tools() -> list[dict[str, str]] | None:
    """Fresh tool list from source, or None if runtime deps aren't importable.

    On a clean CI runner ``ninova_mcp`` cannot be imported until its
    dependencies exist, so we fall back to the tool list already committed in
    ``mcpb/manifest.json`` instead of failing the build.
    """
    try:
        if str(ROOT / "src") not in sys.path:
            sys.path.insert(0, str(ROOT / "src"))
        from ninova_mcp.server import TOOLS  # noqa: E402

        return [{"name": t["name"], "description": t["description"]} for t in TOOLS]
    except Exception as exc:  # deps not installed in this environment
        print(f"[build] note: keeping committed manifest tools ({exc})")
        return None


def _platform_tag() -> str:
    system = platform.system().lower()  # darwin / windows / linux
    machine = platform.machine().lower()  # arm64 / x86_64 / amd64
    return f"{system}-{machine}"


def _download_runtime(server_dir: Path) -> Path:
    """Download + extract a relocatable CPython into ``server_dir/runtime``."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    triple = RUNTIME_TRIPLES.get((system, machine))
    if triple is None:
        raise SystemExit(f"No standalone CPython mapping for {system}/{machine}")

    asset = f"cpython-{RUNTIME_PYTHON}+{RUNTIME_RELEASE}-{triple}-install_only.tar.gz"
    url = (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        f"{RUNTIME_RELEASE}/{asset}"
    )
    print(f"[build] downloading runtime: {asset}")
    tarball = server_dir / "runtime.tar.gz"
    urllib.request.urlretrieve(url, tarball)
    with tarfile.open(tarball) as tf:
        tf.extractall(server_dir, filter="fully_trusted")
    tarball.unlink()
    (server_dir / "python").rename(server_dir / "runtime")
    return _runtime_python(server_dir / "runtime")


def _runtime_python(runtime_dir: Path) -> Path:
    windows = runtime_dir / "python.exe"
    return windows if windows.exists() else runtime_dir / "bin" / "python3"


def _vendor_dependencies(runtime_python: Path, lib_dir: Path) -> None:
    lib_dir.mkdir(parents=True, exist_ok=True)
    print(f"[build] vendoring itu-mcp + deps into {lib_dir} ...")
    subprocess.run(
        [
            str(runtime_python),
            "-m",
            "pip",
            "install",
            "--target",
            str(lib_dir),
            "--no-compile",
            str(ROOT),
        ],
        check=True,
    )


def _write_manifest(
    version: str, tools: list[dict[str, str]] | None, dest: Path
) -> None:
    manifest = json.loads((MCPB_SRC / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = version
    if tools is not None:
        manifest["tools"] = tools
    dest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _pack(build_dir: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    mcpb = shutil.which("mcpb")
    npx = shutil.which("npx")
    cmd = None
    if mcpb:
        cmd = [mcpb, "pack", str(build_dir), str(output)]
    elif npx:
        cmd = [npx, "--yes", "@anthropic-ai/mcpb", "pack", str(build_dir), str(output)]

    if cmd:
        print(f"[build] packing with: {' '.join(cmd)}")
        result = subprocess.run(cmd)
        if result.returncode == 0 and output.exists():
            return
        print("[build] mcpb CLI pack failed; falling back to zip")

    print("[build] packing with zipfile fallback")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(build_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(build_dir))


def main() -> int:
    version = _read_version()
    tools = _read_tools()
    tag = _platform_tag()
    print(f"[build] itu-mcp {version} for {tag} (bundling CPython {RUNTIME_PYTHON})")

    if BUILD.exists():
        shutil.rmtree(BUILD)
    server_dir = BUILD / "server"
    server_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(MCPB_SRC / "server" / "main.py", server_dir / "main.py")
    _write_manifest(version, tools, BUILD / "manifest.json")
    runtime_python = _download_runtime(server_dir)
    _vendor_dependencies(runtime_python, server_dir / "lib")

    output = DIST / f"itu-mcp-{version}-{tag}.mcpb"
    if output.exists():
        output.unlink()
    _pack(BUILD, output)

    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"\n[build] done: {output}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
