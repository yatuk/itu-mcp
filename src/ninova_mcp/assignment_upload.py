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
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from .client import NinovaError
from .parsing import MONTH_NAMES, clean_text, extension_allowed, make_soup, normalize_lookup_text

DRY_RUN_MESSAGE = (
    "Dry run only. Re-call submit_assignment with the same arguments "
    "and confirm=true to actually upload this file to Ninova."
)
# The three answers of an upload that was posted. Only the first is a success.
UPLOADED = "uploaded"
NOT_CONFIRMED = "not_confirmed"
FAILED = "failed"

# Words of a success sentence. They are looked for only in text the POST
# added to the page, and are quoted as evidence, never taken as the verdict.
SUCCESS_WORDS = ("yuklediniz", "gonderdiniz", "basari", "success")
_ERROR_RE = re.compile(
    r"\b(?:hata(?:si|li|lar\w*|yla)?|yuklenemedi|gonderilemedi|basarisiz\w*|gecersiz\w*|reddedildi|"
    r"kabul edilmedi|izin verilm\w*|error|failed|invalid|not allowed)\b"
)
# "24 Haziran 2026 13:35", "24 Haziran 13:35" and "24.06.2026 13:35" inside a slot's status line.
_UPLOAD_DATE_RE = re.compile(
    r"\b\d{1,2}\s+[^\W\d_]+\s+(?:\d{4}\s+)?\d{1,2}:\d{2}(?::\d{2})?\b"
    r"|\b\d{1,2}[./]\d{1,2}[./]\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?\b"
)
_INLINE_TAGS = {"strong", "b", "em", "i", "span", "a", "font", "u"}
_QUOTE_LIMIT = 200
CHECK_NINOVA = (
    "Check the assignment on Ninova in a browser before relying on this submission; "
    "do not treat it as submitted."
)
LOGIN_PAGE_AFTER_POST = (
    "Upload failed. Ninova answered the upload with its login page (the session expired during "
    "the upload), so the file is not confirmed as uploaded. Sign in again (refresh_session), read "
    "the slot with get_assignment_upload_slots and repeat the upload if it is still empty."
)


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
    # Text pieces the page showed before anything was posted. A message that
    # was already there is not an answer to the POST.
    texts: frozenset[str] = frozenset()


class UploadVerdict(NamedTuple):
    """What the answer page proves about the chosen slot, and the sentence that says so."""

    status: str
    message: str


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


def page_texts(html: str | None) -> frozenset[str]:
    """The visible text pieces of a page, normalized, for telling what a later page added."""
    return frozenset(normalized for normalized, _ in _visible_pieces(html))


def _visible_pieces(html: str | None) -> list[tuple[str, str]]:
    """``(normalized piece, text of the element holding it)`` for every visible text piece."""
    soup = make_soup(html or "")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    pieces: list[tuple[str, str]] = []
    for piece in soup.find_all(string=True):
        normalized = normalize_lookup_text(str(piece))
        if not normalized:
            continue
        pieces.append((normalized, _sentence_around(piece)))
    return pieces


def _sentence_around(piece: Any) -> str:
    """The text of the element a piece stands in; a sentence broken up by <strong> or <span> is kept whole."""
    holder = piece.parent
    if holder is None:
        return clean_text(str(piece))
    while holder.name in _INLINE_TAGS and holder.parent is not None:
        around = clean_text(holder.parent.get_text(" ", strip=True))
        if len(around) > _QUOTE_LIMIT:
            break
        holder = holder.parent
    return clean_text(holder.get_text(" ", strip=True))


def _shorten(text: str | None) -> str:
    text = clean_text(text)
    return text if len(text) <= _QUOTE_LIMIT else text[: _QUOTE_LIMIT - 3] + "..."


def _new_message(html: str, before: frozenset[str], matches: Callable[[str], bool]) -> str | None:
    """The first text the POST added to the page that ``matches``, quoted with its sentence."""
    for normalized, quoted in _visible_pieces(html):
        if normalized not in before and matches(normalized):
            return _shorten(quoted)
    return None


def page_error_message(html: str, before: frozenset[str] = frozenset()) -> str | None:
    """An explicit error sentence the answer page carries and the upload page did not."""
    return _new_message(html, before, lambda text: bool(_ERROR_RE.search(text)))


def page_success_phrase(html: str, before: frozenset[str] = frozenset()) -> str | None:
    """A success sentence the answer page added. Evidence to quote; it never decides the verdict."""
    return _new_message(html, before, lambda text: any(word in text for word in SUCCESS_WORDS))


def slot_upload_dates(slot: dict[str, Any] | None) -> tuple[str, ...]:
    """The upload dates a slot's status line shows, as written."""
    text = clean_text((slot or {}).get("status_text"))
    found = (clean_text(match.group(0)) for match in _UPLOAD_DATE_RE.finditer(text))
    return tuple(date for date in found if _is_date(date))


def _is_date(text: str) -> bool:
    """A numeric date, or a day followed by a month name ("2 Rapor 10:00" is not one)."""
    word = text.split()[1]
    return ":" in word or normalize_lookup_text(word) in MONTH_NAMES


def _name_parts(name: str | None) -> tuple[str, str]:
    """``(stem, extension)`` of a file name, reduced to letters and digits for comparison."""
    path = Path(normalize_lookup_text(name))
    return re.sub(r"[\W_]+", "", path.stem), path.suffix


def file_names_match(posted: str | None, shown: str | None) -> bool:
    """Whether the name a slot shows is the posted file's name.

    Case, accents, spaces and punctuation are ignored. A name the server
    extended (a prefix or a suffix around the posted name) still matches, as
    long as the extension is the same.
    """
    if not posted or not shown:
        return False
    posted_stem, posted_suffix = _name_parts(posted)
    shown_stem, shown_suffix = _name_parts(shown)
    if not posted_stem or posted_suffix != shown_suffix:
        return False
    return posted_stem == shown_stem or (len(posted_stem) >= 3 and posted_stem in shown_stem)


def _slot_label(slot: dict[str, Any]) -> str:
    description = _shorten(slot.get("description"))
    return f"slot {slot.get('index')} ({description})" if description else f"slot {slot.get('index')}"


def _is_other_slot(slot: dict[str, Any], after_slot: dict[str, Any]) -> bool:
    """True when the slot found at the same position posts to another file field."""
    before, after = slot.get("field_name"), after_slot.get("field_name")
    return bool(before and after and before != after)


def _replacement_evidence(before: dict[str, Any], after: dict[str, Any]) -> str | None:
    """What shows that a slot which already held a file now holds another one, if anything does."""
    dates_before, dates_after = slot_upload_dates(before), slot_upload_dates(after)
    if dates_after and dates_after != dates_before:
        was = f" (was {', '.join(dates_before)})" if dates_before else ""
        return f"its upload date is now {', '.join(dates_after)}{was}"
    name_before, name_after = before.get("file_name"), after.get("file_name")
    if name_after and normalize_lookup_text(name_after) != normalize_lookup_text(name_before):
        return f"its file is now {name_after!r}" + (f" (was {name_before!r})" if name_before else "")
    return None


def _confirmed_message(slot: dict[str, Any], after_slot: dict[str, Any], file_name: str) -> str | None:
    """The sentence confirming the upload, or None when the slot does not prove it."""
    if after_slot.get("uploaded") is not True:
        return None
    if slot.get("uploaded"):
        evidence = _replacement_evidence(slot, after_slot)
        if evidence is None:
            return None
        seen = f"it held a file before and {evidence}"
    else:
        dates = slot_upload_dates(after_slot)
        seen = "it was empty before and holds a file now" + (f", upload date {', '.join(dates)}" if dates else "")
    shown = after_slot.get("file_name")
    if shown and file_names_match(file_name, shown):
        seen += f"; the file shown is {shown!r}"
    elif shown:
        seen += f"; the file shown is {shown!r}, not the posted name {file_name!r}"
    return f"Upload confirmed for {_slot_label(slot)}: {seen}."


def _unconfirmed_reason(slot: dict[str, Any], after: dict[str, Any], after_slot: dict[str, Any] | None) -> str:
    """Why the answer page does not prove the upload: what was seen instead."""
    label = _slot_label(slot)
    slots = after.get("slots") or []
    if not slots:
        why = "it lists no upload slots" if after.get("ok") else after.get("error")
        return f"the answer page could not be read as the upload page ({why})"
    if after_slot is None:
        listed = ", ".join(str(item.get("index")) for item in slots)
        return f"the answer page lists slots {listed} but not {label}"
    if _is_other_slot(slot, after_slot):
        return f"slot {slot.get('index')} of the answer page is not the slot the file was posted to ({label})"
    if after_slot.get("uploaded") is not True:
        return f"{label} still shows no file ({_shorten(after_slot.get('status_text'))!r})"
    dates = ", ".join(slot_upload_dates(after_slot)) or "not shown"
    return (
        f"{label} already held a file before the upload and looks the same afterwards "
        f"(upload date {dates}, file name {after_slot.get('file_name') or 'not shown'}), "
        "so the new file cannot be told from the old one"
    )


def judge_upload(
    slot: dict[str, Any],
    after: dict[str, Any],
    after_slot: dict[str, Any] | None,
    *,
    file_name: str,
    error_message: str | None = None,
    success_phrase: str | None = None,
    answered_from: str | None = None,
) -> UploadVerdict:
    """Decide what the page answering the POST proves about the chosen slot.

    ``uploaded`` needs the slot itself: empty before and holding a file now,
    or, for a replacement, a changed upload date or file name. An error
    sentence on the page without that proof is ``failed``. Everything else is
    ``not_confirmed``. Words elsewhere on the page never make a success.
    ``answered_from`` is the address of an answer that is not the upload page
    of this assignment; the slots of such a page prove nothing.
    """
    if answered_from is None and after_slot is not None and not _is_other_slot(slot, after_slot):
        confirmed = _confirmed_message(slot, after_slot, file_name)
        if confirmed:
            return UploadVerdict(UPLOADED, confirmed)
    if answered_from is not None:
        reason = f"the answer came from {answered_from}, which is not the upload page of this assignment"
    else:
        reason = _unconfirmed_reason(slot, after, after_slot)
    if error_message:
        return UploadVerdict(
            FAILED, f"Upload failed. Ninova's answer page reports: {error_message!r}. Also, {reason}.",
        )
    quoted = (
        f" The page does say {success_phrase!r}, but that is not tied to this slot and is not taken as confirmation."
        if success_phrase else ""
    )
    return UploadVerdict(
        NOT_CONFIRMED, f"Upload NOT confirmed. The POST completed, but {reason}.{quoted} {CHECK_NINOVA}",
    )


def upload_result(
    preview: dict[str, Any],
    slot: dict[str, Any],
    after: dict[str, Any],
    after_slot: dict[str, Any] | None,
    *,
    verdict: UploadVerdict,
    response_url: str,
) -> dict[str, Any]:
    return {
        "dry_run": False,
        # True only when the chosen slot was seen to hold the new file.
        "ok": verdict.status == UPLOADED,
        "status": verdict.status,
        "message": verdict.message,
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
    if not target:
        # An empty reference names nothing; it must not match an item whose title or URL is empty.
        titles = ", ".join(item.get("title") or "?" for item in items[:12])
        raise NinovaError(f"Which assignment? The assignment reference is empty. Available: {titles}")
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
