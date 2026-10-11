"""Where the file tools may read and write on the local disk.

The model chooses the ``path``, ``output_dir`` and ``filename`` arguments, and
the model reads untrusted text. So those arguments can only name something
inside the server's own downloads directory, or inside a directory the
operator listed in an environment variable. The state directory's own files
(saved session cookies, tracking state, snapshots) are never reachable.
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import BinaryIO

from .operator_settings import (
    EXTRA_DOWNLOAD_DIRS_ENV,
    EXTRA_READ_DIRS_ENV,
    extra_directories,
)
from .parsing import sanitize_filename
from .private_io import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE, ensure_private_dir

MAX_FILENAME_CHARS = 180
NEUTRAL_SUFFIX = ".download"

# File types that run code, or point at code, when opened from a file manager.
# A denylist rather than an allowlist of document types: course material is
# legitimately source code, archives, notebooks and data in formats nobody can
# enumerate, and refusing those would break the tool for programming courses.
# Writes are already confined to the downloads directory, which removes the
# autostart locations; this list is the second layer for a double-click there.
DANGEROUS_EXTENSIONS = frozenset({
    # Windows executables and installers
    ".exe", ".com", ".scr", ".pif", ".cpl", ".dll", ".sys", ".ocx", ".drv",
    ".msi", ".msp", ".mst", ".msix", ".msixbundle", ".appx", ".appxbundle", ".appinstaller",
    ".application", ".appref-ms", ".gadget", ".msc",
    # Windows scripts
    ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".ps1xml", ".psc1", ".vbs", ".vbe", ".vb",
    ".js", ".jse", ".wsf", ".wsh", ".wsc", ".sct", ".hta", ".reg", ".inf",
    # Windows shortcuts and shell link types
    ".lnk", ".url", ".scf", ".shb", ".shs", ".website", ".library-ms", ".search-ms",
    ".searchconnector-ms", ".settingcontent-ms", ".chm", ".hlp", ".xll", ".wll",
    # Java and cross-platform launchers
    ".jar", ".jnlp",
    # macOS and Linux launchers
    ".app", ".command", ".action", ".workflow", ".scpt", ".pkg", ".dmg", ".desktop",
    ".appimage", ".deb", ".rpm", ".terminal", ".inetloc", ".fileloc", ".mobileconfig",
    # Python launchers that run on a double-click (.py is left alone: it is course material)
    ".pyw", ".pyz", ".pyzw",
    # Disk images, which mount on a double-click and hide what is inside
    ".iso", ".img", ".vhd", ".vhdx",
    # Other Windows types that start a program or a connection when opened
    ".diagcab", ".xbap", ".wsb", ".rdp", ".msu", ".vsix", ".theme", ".themepack", ".iqy", ".slk",
})

_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"com{digit}" for digit in "0123456789\u00b9\u00b2\u00b3"}
    | {f"lpt{digit}" for digit in "0123456789\u00b9\u00b2\u00b3"}
)


class LocalFileError(ValueError):
    """A local path or file name was refused."""


def downloads_dir(state_dir: Path) -> Path:
    return Path(state_dir) / "downloads"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _root_forms(root: Path) -> tuple[Path, Path]:
    """The root as written and as the filesystem resolves it.

    Both are needed: on Windows a temporary directory can be spelled with a
    short 8.3 name while paths handed back to the caller use the long one.
    """
    return Path(os.path.abspath(root)), Path(root).resolve()


def _permitted(path: Path, *, base: Path, extras: list[Path], state: Path) -> bool:
    if _inside(path, base):
        return True
    if _inside(path, state):
        # Session cookies, tracking state and snapshots live here.
        return False
    return any(_inside(path, extra) for extra in extras)


def _confine(raw: str | os.PathLike[str], *, state_dir: Path, extras: list[Path], what: str) -> Path:
    text = os.fspath(raw)
    if not text or "\x00" in text:
        raise LocalFileError(f"{what} is empty or contains a NUL character.")
    if text.startswith(("\\\\", "//")):
        # UNC and device paths (\\server\share, \\?\, \\.\). Merely checking
        # whether such a path exists makes Windows contact the named server.
        raise LocalFileError(f"{what} must not be a network (UNC) or device path.")
    base_forms = _root_forms(downloads_dir(state_dir))
    state_forms = _root_forms(Path(state_dir))
    extra_forms = [_root_forms(extra) for extra in extras]

    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = base_forms[1] / candidate
    if not candidate.is_absolute():
        # A Windows drive-relative path such as "C:name" replaces the base.
        raise LocalFileError(f"{what} must be an absolute path or a name inside the downloads directory.")

    refusal = LocalFileError(
        f"{what} is outside the directories this server may use. Allowed: the downloads "
        f"directory ({base_forms[1]}) and any directory the operator listed in the server "
        "environment. The state directory's own files are never available."
    )
    # First without touching the filesystem, so an outside path is never opened or stat'ed.
    lexical = Path(os.path.abspath(candidate))
    if not any(
        _permitted(lexical, base=base_forms[form], extras=[item[form] for item in extra_forms],
                   state=state_forms[form])
        for form in (0, 1)
    ):
        raise refusal
    # Then with symbolic links and junctions followed.
    resolved = lexical.resolve()
    if not _permitted(resolved, base=base_forms[1], extras=[item[1] for item in extra_forms],
                      state=state_forms[1]):
        raise refusal
    return resolved


def resolve_upload_file(raw: str | os.PathLike[str], *, state_dir: Path) -> Path:
    """Return the real path of a file that may be uploaded, or raise.

    Homework lives wherever the user keeps it, so there is no directory
    allowlist here. Two things are refused before the filesystem is touched:
    network paths, and the state directory's own files (session cookies,
    tracking state). Downloads stay available.
    """
    text = os.fspath(raw)
    if not text or "\x00" in text:
        raise LocalFileError("file_path is empty or contains a NUL character.")
    if text.startswith(("\\\\", "//")):
        # Merely checking whether such a path exists makes Windows contact the named server.
        raise LocalFileError("file_path must not be a network (UNC) or device path.")
    state_forms = _root_forms(Path(state_dir))
    download_forms = _root_forms(downloads_dir(state_dir))
    refusal = LocalFileError("file_path is inside the server's state directory, which is never uploaded.")

    lexical = Path(os.path.abspath(Path(text).expanduser()))
    resolved = lexical.resolve()
    for path in (lexical, resolved):
        for form in (0, 1):
            if _inside(path, state_forms[form]) and not _inside(resolved, download_forms[1]):
                raise refusal
    return resolved


def resolve_readable_file(raw: str | os.PathLike[str], *, state_dir: Path) -> Path:
    """Return the real path of a file the text tools may read, or raise."""
    resolved = _confine(
        raw, state_dir=state_dir, extras=extra_directories(EXTRA_READ_DIRS_ENV), what="path",
    )
    if not resolved.is_file():
        raise LocalFileError(f"File not found: {resolved}")
    return resolved


def resolve_output_dir(
    raw: str | os.PathLike[str] | None,
    *,
    state_dir: Path,
    default: Path | None = None,
) -> Path:
    """Return (and create) the directory a download may be written into, or raise."""
    target = raw if raw else (default or downloads_dir(state_dir))
    extras = extra_directories(EXTRA_DOWNLOAD_DIRS_ENV)
    resolved = _confine(target, state_dir=state_dir, extras=extras, what="output_dir")
    own_downloads = downloads_dir(state_dir).resolve()
    if _inside(resolved, own_downloads):
        # Ours: owner-only. A directory the operator listed keeps its own mode.
        ensure_private_dir(own_downloads)
    resolved.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    # Once more now that it exists: a link created in between must not win.
    return _confine(resolved, state_dir=state_dir, extras=extras, what="output_dir")


def safe_download_name(raw: str | None) -> tuple[str, bool]:
    """Return a file name that is safe to create, and whether it was neutralised.

    Path separators and control characters are already replaced by
    ``sanitize_filename``. On top of that: invisible format characters are
    removed, trailing dots and spaces are stripped, Windows device names are
    refused, and a name whose type runs code when opened gets ``.download``
    appended so a double-click no longer executes it.
    """
    name = sanitize_filename(raw)
    name = "".join(char for char in name if unicodedata.category(char) not in {"Cf", "Cc", "Co", "Cs"})
    name = name.strip().rstrip(". ").lstrip(" ")
    if not name or set(name) <= {"."}:
        name = "download"
    if len(name) > MAX_FILENAME_CHARS:
        stem, suffix = os.path.splitext(name)
        suffix = suffix[:20]
        name = stem[: MAX_FILENAME_CHARS - len(suffix)].rstrip(". ") + suffix
    device = name.split(".", 1)[0].strip().lower()
    if device in _WINDOWS_RESERVED_NAMES:
        raise LocalFileError(
            f"File name {name!r} is a reserved Windows device name. Pass a different filename."
        )
    if os.path.splitext(name)[1].lower() in DANGEROUS_EXTENSIONS:
        return name + NEUTRAL_SUFFIX, True
    return name, False


def create_new_file(directory: Path, name: str) -> tuple[Path, BinaryIO]:
    """Create ``name`` in ``directory`` without ever replacing an existing file.

    An existing name gets ``-2``, ``-3`` and so on before the extension. The
    file is opened in exclusive mode, so a file that appears in between is not
    overwritten either. It is created owner-only: course files and transcripts
    are the user's, not every local account's.
    """
    stem, suffix = os.path.splitext(name)
    stem = stem or "download"
    counter = 1
    while True:
        candidate = directory / (name if counter == 1 else f"{stem}-{counter}{suffix}")
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            return candidate, os.fdopen(os.open(candidate, flags, PRIVATE_FILE_MODE), "wb")
        except FileExistsError:
            counter += 1
            if counter > 10_000:
                raise LocalFileError("Could not find a free file name in the output directory.") from None
