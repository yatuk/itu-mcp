"""Write private state files atomically and readable by the owner only.

The state directory holds session cookies, tracking state and the seat-watch
list. On POSIX the directory is created ``0700`` and every file ``0600``,
starting with the temporary file, so there is no moment at which the content
is readable by another local user.

Windows has no such permission bits: ``os.chmod`` there only toggles the
read-only flag. Protection on Windows comes from where the directory lives,
the user's profile, whose ACL keeps other standard users out. A state
directory moved elsewhere with ``NINOVA_STATE_DIR`` gets whatever ACL that
location has.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import stat
from pathlib import Path

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def ensure_private_dir(path: str | Path) -> Path:
    """Create ``path`` owner-only, or tighten it if it already exists wider."""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    if os.name == "posix":
        # A failure means the directory is not ours to change (a shared
        # mount, for example). The files inside are still created 0600.
        with contextlib.suppress(OSError):
            info = directory.stat()
            wider = stat.S_IMODE(info.st_mode) & ~PRIVATE_DIR_MODE
            if wider and info.st_uid == os.geteuid():
                os.chmod(directory, PRIVATE_DIR_MODE)
    return directory


def write_private_bytes(path: str | Path, data: bytes, *, private_dir: bool = True) -> None:
    """Replace ``path`` with ``data`` in one step.

    The content goes to a new owner-only file in the same directory and is
    then renamed over the target, so a reader sees the old file or the new
    one and a crash in between leaves the old file as it was. Pass
    ``private_dir=False`` when the directory was chosen by the caller and its
    permissions are not ours to change.
    """
    target = Path(path)
    if private_dir:
        ensure_private_dir(target.parent)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
    # A fresh name each time: O_EXCL then never meets a leftover from a
    # crashed run, and two processes cannot share a temporary file.
    temp = target.with_name(f"{target.name}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(temp, flags, PRIVATE_FILE_MODE)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def write_private_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    private_dir: bool = True,
) -> None:
    write_private_bytes(path, text.encode(encoding), private_dir=private_dir)
