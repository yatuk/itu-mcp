"""Steps of the assignment upload that touch neither the network nor the user.

``NinovaCourseToolsMixin.submit_assignment`` runs the upload as a fixed
sequence: switch, local file, upload page, slot, preview, confirmation, POST.
The steps here are the ones that only look at values already in hand: the
local file, the parsed upload form, the assignment list. Everything that
requests a page, asks the user or posts stays in the mixin, in that order.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from .client import NinovaError
from .parsing import extension_allowed, normalize_lookup_text

DRY_RUN_MESSAGE = (
    "Dry run only. Re-call submit_assignment with the same arguments "
    "and confirm=true to actually upload this file to Ninova."
)
SUCCESS_WORDS = ("yuklediniz", "gonderdiniz", "basari", "success")


class LocalUpload(NamedTuple):
    """The file as it was read, once. The preview, the dialog and the POST all use these bytes."""

    path: Path
    content: bytes
    sha256: str

    @property
    def size(self) -> int:
        return len(self.content)


class UploadPage(NamedTuple):
    """An upload page that passed the page checks, and where its form posts."""

    url: str
    form: dict[str, Any]
    post_url: str


def uploads_disabled() -> bool:
    return os.getenv("NINOVA_ALLOW_UPLOADS", "1").strip().lower() in {"0", "false", "no", "off"}


def read_upload_once(path: Path, max_bytes: int) -> LocalUpload:
    """Read a file that may be uploaded, refusing a missing, empty or oversized one."""
    if not path.is_file():
        raise NinovaError(f"File not found: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise NinovaError(f"File is empty: {path}")
    if size > max_bytes:
        raise NinovaError(f"File too large ({size} bytes). Max is {max_bytes} bytes.")
    # Read once, here. The size and hash shown to the user and the bytes
    # that are sent all come from this one read, so a file that changes on
    # disk after the user approved cannot change what is uploaded.
    with path.open("rb") as handle:
        content = handle.read(max_bytes + 1)
    # The file may have changed between the size check and the read.
    if len(content) <= 0:
        raise NinovaError(f"File is empty: {path}")
    if len(content) > max_bytes:
        raise NinovaError(f"File too large (more than {max_bytes} bytes). Max is {max_bytes} bytes.")
    return LocalUpload(path, content, hashlib.sha256(content).hexdigest())


def choose_upload_slot(
    form: dict[str, Any],
    file_name: str,
    *,
    slot_index: int | None,
    slot_description: str | None,
    allow_replace: bool,
    match_slot: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Return the slot of the form the file goes into, or raise if it may not go there."""
    try:
        slot = match_slot(form.get("slots") or [], slot_index=slot_index, slot_description=slot_description)
    except ValueError as exc:
        raise NinovaError(str(exc)) from exc
    if not slot.get("field_name"):
        raise NinovaError(f"Slot {slot.get('index')} has no file input field ({slot.get('description')}).")
    if slot.get("uploaded") and not allow_replace:
        raise NinovaError(
            f"Slot {slot.get('index')} ({slot.get('description')}) already has an "
            "uploaded file. Pass allow_replace=true to overwrite, or pick another slot."
        )
    if not extension_allowed(file_name, slot.get("allowed_extensions") or []):
        raise NinovaError(
            f"File extension of {file_name!r} is not allowed for this slot. "
            f"Allowed: {slot.get('extensions') or 'any'}"
        )
    return slot


def upload_preview(
    resolved_upload: dict[str, Any],
    page: UploadPage,
    slot: dict[str, Any],
    upload: LocalUpload,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """What would be uploaded where: the dry-run result, and the base of every later result."""
    return {
        "dry_run": dry_run,
        "course": resolved_upload.get("course"),
        "assignment": resolved_upload.get("assignment"),
        "upload_url": page.url,
        "form_url": page.post_url,
        "submit_label": page.form.get("submit_label"),
        "slot": {
            "index": slot.get("index"),
            "description": slot.get("description"),
            "extensions": slot.get("extensions"),
            "field_name": slot.get("field_name"),
            "already_uploaded": slot.get("uploaded"),
        },
        "file": {
            "path": str(upload.path),
            "name": upload.path.name,
            "size_bytes": upload.size,
            "sha256": upload.sha256,
        },
    }


def multipart_fields(
    form: dict[str, Any], slot: dict[str, Any], upload: LocalUpload,
) -> tuple[dict[str, str], list[tuple[str, tuple[str, bytes, str]]]]:
    """The ASP.NET postback fields and the file part of the upload POST."""
    content_type = mimetypes.guess_type(upload.path.name)[0] or "application/octet-stream"
    data = dict(form.get("hidden_fields") or {})
    data["__EVENTTARGET"] = form["submit_event_target"]
    data["__EVENTARGUMENT"] = data.get("__EVENTARGUMENT", "")
    files = [(slot["field_name"], (upload.path.name, upload.content, content_type))]
    return data, files


def slot_after_upload(after: dict[str, Any], slot: dict[str, Any]) -> dict[str, Any] | None:
    """The chosen slot as the page answering the POST shows it, if it shows it."""
    return next((item for item in after.get("slots") or [] if item.get("index") == slot.get("index")), None)


def text_reports_success(page_text: str | None) -> bool:
    body = normalize_lookup_text(page_text or "")
    return any(word in body for word in SUCCESS_WORDS)


def upload_result(
    preview: dict[str, Any],
    slot: dict[str, Any],
    after: dict[str, Any],
    after_slot: dict[str, Any] | None,
    *,
    success: bool,
    response_url: str,
) -> dict[str, Any]:
    return {
        "dry_run": False,
        "ok": success,
        "message": (
            "Upload appears successful."
            if success
            else "Upload POST completed but the slot still looks empty; check Ninova in a browser."
        ),
        "course": preview["course"],
        "assignment": preview["assignment"],
        "upload_url": preview["upload_url"],
        "response_url": response_url,
        "slot_before": preview["slot"],
        "slot_after": {
            "index": after_slot.get("index") if after_slot else slot.get("index"),
            "description": (after_slot or slot).get("description"),
            "uploaded": after_slot.get("uploaded") if after_slot else None,
            "file_name": after_slot.get("file_name") if after_slot else None,
            "status_text": after_slot.get("status_text") if after_slot else None,
        },
        "file": preview["file"],
        "requested_file_count": after.get("requested_file_count"),
        "uploaded_file_count": after.get("uploaded_file_count"),
    }


def _refers_to_assignment(item: dict[str, Any], target: str) -> bool:
    candidates = {
        normalize_lookup_text(item.get("title")),
        normalize_lookup_text(item.get("url")),
    }
    return target in candidates or any(target and target in value for value in candidates if value)


def find_assignment(items: list[dict[str, Any]], assignment: str) -> dict[str, Any]:
    """Return the one assignment a title, URL or part of either refers to, or raise."""
    target = normalize_lookup_text(assignment)
    matches = [item for item in items if _refers_to_assignment(item, target)]
    if not matches:
        titles = ", ".join(item.get("title") or "?" for item in items[:12])
        raise NinovaError(f"Assignment not found: {assignment}. Available: {titles}")
    if len(matches) == 1:
        return matches[0]
    # Prefer exact title match.
    exact = [item for item in matches if normalize_lookup_text(item.get("title")) == target]
    if len(exact) == 1:
        return exact[0]
    options = ", ".join(item.get("title") or item.get("url") or "?" for item in matches[:8])
    raise NinovaError(f"Ambiguous assignment {assignment!r}. Matches: {options}")


def upload_link_of(assignment: dict[str, Any]) -> str:
    """The upload link an assignment carries, or the one its own URL implies."""
    link = assignment.get("upload_url")
    if not link and assignment.get("url"):
        link = assignment["url"].rstrip("/") + "/OdevGonder"
    if not link:
        raise NinovaError(
            f"Assignment {assignment.get('title')!r} has no upload URL "
            "(deadline may be closed or upload not enabled)."
        )
    return link
