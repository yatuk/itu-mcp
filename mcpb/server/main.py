"""Entry point for the Ninova MCP Claude Desktop extension (.mcpb).

Claude Desktop launches this file with ``PYTHONPATH`` pointed at the bundled
``server/lib`` directory (see manifest.json), so ``ninova_mcp`` and all of its
dependencies import from inside the extension without any pip install.

Bootstrap problem this file solves:
Claude Desktop is a GUI app, so it does not inherit your shell ``PATH``. On
macOS it usually launches the *system* ``python3`` (``/usr/bin/python3``,
version 3.9), which is too old to run this server even when you have a newer
Python installed. So before importing anything, we make sure we are running on
Python 3.11+, and if not we look for a suitable interpreter in the usual
install locations and re-exec ourselves with it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MIN_VERSION = (3, 11)
_REEXEC_GUARD = "NINOVA_MCPB_BOOTSTRAPPED"


def _interpreter_is_modern(executable: str) -> bool:
    """Return True if ``executable`` is a working Python >= MIN_VERSION."""
    try:
        proc = subprocess.run(
            [
                executable,
                "-c",
                "import sys; sys.exit(0 if sys.version_info[:2] >= "
                f"{MIN_VERSION} else 1)",
            ],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        return False
    return proc.returncode == 0


def _candidate_interpreters() -> list[str]:
    """Ordered, de-duplicated list of interpreters worth trying (newest first)."""
    from shutil import which

    minors = (14, 13, 12, 11)
    names = [f"python3.{m}" for m in minors] + ["python3", "python"]

    candidates: list[str] = []
    for name in names:
        found = which(name)
        if found:
            candidates.append(found)

    prefixes = [
        "/opt/homebrew/bin",  # Apple Silicon Homebrew
        "/usr/local/bin",  # Intel Homebrew / python.org
    ]
    for prefix in prefixes:
        for name in names:
            candidates.append(str(Path(prefix) / name))
    for minor in minors:
        candidates.append(
            f"/Library/Frameworks/Python.framework/Versions/3.{minor}/bin/python3"
        )

    # De-duplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen and os.path.exists(candidate):
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _bootstrap_modern_python() -> None:
    """Re-exec on a Python 3.11+ interpreter if we were launched on an old one."""
    if sys.version_info[:2] >= MIN_VERSION:
        return
    if os.environ.get(_REEXEC_GUARD) == "1":
        # Already tried once; don't loop. Fall through and let the import fail
        # loudly rather than silently.
        return

    for executable in _candidate_interpreters():
        if os.path.realpath(executable) == os.path.realpath(sys.executable):
            continue
        if _interpreter_is_modern(executable):
            env = dict(os.environ)
            env[_REEXEC_GUARD] = "1"
            os.execve(executable, [executable, str(Path(__file__).resolve())], env)

    sys.stderr.write(
        "Ninova MCP needs Python 3.11 or newer, but Claude Desktop launched it "
        f"with Python {sys.version.split()[0]} and no newer interpreter was "
        "found. Install Python 3.11+ from https://www.python.org/downloads/ and "
        "restart Claude Desktop.\n"
    )


_bootstrap_modern_python()

# Make sure the vendored dependency directory is importable even if PYTHONPATH
# did not survive the launch environment.
_LIB = Path(__file__).resolve().parent / "lib"
if _LIB.is_dir() and str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# Keep tracking state and downloads inside the user's home, never inside the
# read-only extension directory.
os.environ.setdefault("NINOVA_STATE_DIR", str(Path.home() / ".ninova_state"))

from ninova_mcp.server import main  # noqa: E402  (import after bootstrap)

if __name__ == "__main__":
    main()
