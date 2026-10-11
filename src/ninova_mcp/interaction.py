"""User confirmation and progress for a running tool call, with no MCP imports.

Tool methods are plain synchronous functions that tests also call directly.
The transport layer (see ``mcp_interaction``) binds a ``ToolInteraction`` for
the duration of one call; everything here is a no-op, or falls back to the
argument-only guards, when nothing is bound.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
from dataclasses import dataclass
from typing import Any, Callable, Iterator

# (done, total or None, short message)
ProgressCallback = Callable[[float, "float | None", "str | None"], None]

REQUIRE_ELICITATION_ENV = "NINOVA_REQUIRE_ELICITATION"

# Tools that the transport runs in a worker thread with an interaction bound:
# the two account writes (confirmation) and the slow multi-request tools
# (progress). Every other tool keeps running inline exactly as before.
INTERACTIVE_TOOL_NAMES = frozenset({
    "submit_assignment",
    "obs_save_registration_draft",
    "sync_all_courses",
    "crawl_course",
    "obs_get_course_prerequisites",
    "obs_get_elective_group",
})


@dataclass(frozen=True)
class ToolInteraction:
    """What the connected client can do for the tool call in progress."""

    # True only when the client declared form elicitation at initialize.
    elicitation_supported: bool = False
    # Blocks until the user answers. Returns "accept", "decline", "cancel",
    # or "error" when no valid answer was obtained.
    ask: Callable[[str], str] | None = None
    # None when the client sent no progress token.
    progress: ProgressCallback | None = None


_CURRENT: contextvars.ContextVar[ToolInteraction | None] = contextvars.ContextVar(
    "ninova_tool_interaction", default=None,
)


def current() -> ToolInteraction | None:
    return _CURRENT.get()


@contextlib.contextmanager
def bind(interaction: ToolInteraction | None) -> Iterator[None]:
    token = _CURRENT.set(interaction)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def elicitation_required() -> bool:
    """The operator's exact opt-in to refuse writes without a user prompt."""
    return os.getenv(REQUIRE_ELICITATION_ENV) == "1"


# -- confirmation -----------------------------------------------------------

_REFUSAL_NOTES = {
    "declined_by_user": (
        "The user declined this write in the client's confirmation dialog. Nothing was sent. "
        "This is the user's decision, not an error: do not call the tool again unless the user asks for it."
    ),
    "cancelled_by_user": (
        "The user dismissed the client's confirmation dialog without accepting. Nothing was sent. "
        "This is the user's decision, not an error: do not call the tool again unless the user asks for it."
    ),
    "confirmation_unavailable": (
        "The client did not return a valid answer to the confirmation dialog. Nothing was sent. "
        "Tell the user the confirmation could not be shown; do not retry on your own."
    ),
    "elicitation_required": (
        f"The operator set {REQUIRE_ELICITATION_ENV}=1 and the connected client cannot show a "
        "confirmation dialog (no MCP elicitation support). Nothing was sent. The confirm argument "
        "alone is not accepted here; the user needs a client that supports elicitation."
    ),
}


def confirm_write(message: str) -> dict[str, Any] | None:
    """Last gate before an account write. Returns None when no gate applies.

    Otherwise the returned record always carries ``proceed``; the caller must
    send nothing unless it is exactly True. A model-supplied ``confirm`` never
    reaches this function, so it cannot stand in for the user's answer.
    """
    interaction = current()
    if interaction is not None and interaction.elicitation_supported and interaction.ask is not None:
        try:
            action = interaction.ask(message)
        except Exception:
            action = "error"
        if action == "accept":
            return {
                "proceed": True, "method": "elicitation", "elicitation_supported": True,
                "user_action": "accept",
                "note": "The user accepted this write in the client's confirmation dialog.",
            }
        status = {"decline": "declined_by_user", "cancel": "cancelled_by_user"}.get(action, "confirmation_unavailable")
        return {
            "proceed": False, "status": status, "method": "elicitation", "elicitation_supported": True,
            "user_action": action if action in ("decline", "cancel") else None,
            "note": _REFUSAL_NOTES[status],
        }
    if elicitation_required():
        return {
            "proceed": False, "status": "elicitation_required", "method": "none",
            "elicitation_supported": False, "user_action": None,
            "note": _REFUSAL_NOTES["elicitation_required"],
        }
    if interaction is None:
        # Direct Python call with no MCP request: the argument guards are the
        # whole contract, exactly as before.
        return None
    return {
        "proceed": True, "method": "argument_only", "elicitation_supported": False,
        "user_action": None,
        "note": "Confirmed by the confirm argument only: the connected client has no MCP elicitation "
                "support, so the server could not ask the user directly.",
    }


def public_confirmation(record: dict[str, Any]) -> dict[str, Any]:
    """The part of a confirmation record that belongs in a tool result."""
    return {key: value for key, value in record.items() if key not in ("proceed", "status")}


def _clean(value: Any, limit: int = 120) -> str:
    """One line of display text. Course and assignment names come from Ninova
    pages, so they must not be able to add lines of their own to the dialog."""
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in str(value)).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _crn_line(crns: list[str]) -> str:
    return ", ".join(crns) if crns else "yok"


def draft_confirmation_message(
    term_code: str, selected: list[str], previous: list[str] | None,
    added: list[str] | None, removed: list[str] | None,
) -> str:
    lines = [
        "İTÜ MCP, OBS'deki kayıtlı ders kayıt taslağınızı değiştirmek üzere.",
        "",
        f"Dönem kodu: {_clean(term_code)}",
        f"Kaydedilecek CRN'ler ({len(selected)}): {_crn_line(selected)}",
    ]
    if previous is None:
        lines.append(
            "DİKKAT: Mevcut taslak okunamadı. Kayıtlı taslağın tamamı yukarıdaki listeyle değiştirilecek; "
            "listede olmayan dersler taslaktan çıkar."
        )
    else:
        lines.append(f"Şu anki taslak ({len(previous)}): {_crn_line(previous)}")
        lines.append(f"Taslağa eklenecek CRN'ler ({len(added or [])}): {_crn_line(added or [])}")
        if removed:
            lines.append(f"DİKKAT: Taslaktan ÇIKARILACAK CRN'ler ({len(removed)}): {_crn_line(removed)}")
        else:
            lines.append("Taslaktan çıkarılacak CRN yok.")
    lines += [
        "",
        "Bu işlem yalnızca taslağı kaydeder; derse kayıt yapmaz, ders bırakmaz.",
        "Onaylıyor musunuz?",
    ]
    return "\n".join(lines)


def _size_text(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        human = f"{size_bytes / (1024 * 1024):.1f} MB"
    elif size_bytes >= 1024:
        human = f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes} bayt"
    return f"{human.replace('.', ',')} ({size_bytes} bayt)"


def upload_confirmation_message(
    course: dict[str, Any] | None, assignment: dict[str, Any] | None, upload_url: str,
    slot: dict[str, Any], file_name: str, file_path: str, size_bytes: int,
) -> str:
    course = course or {}
    assignment = assignment or {}
    course_text = " - ".join(
        _clean(part) for part in (course.get("code"), course.get("title")) if part
    ) or "belirtilmedi (yükleme adresi doğrudan verildi)"
    assignment_text = _clean(assignment.get("title")) if assignment.get("title") else "adı okunamadı"
    lines = [
        "İTÜ MCP, Ninova'ya sizin adınıza ödev dosyası yüklemek üzere.",
        "",
        f"Ders: {course_text}",
        f"Ödev: {assignment_text}",
        f"Yükleme adresi: {_clean(upload_url, 200)}",
        f"Yükleme alanı: {_clean(slot.get('index'))} ({_clean(slot.get('description') or 'açıklama yok')})",
        f"Dosya: {_clean(file_name)}, {_size_text(size_bytes)}",
        f"Dosyanın bilgisayarınızdaki yolu: {_clean(file_path, 260)}",
    ]
    if slot.get("uploaded"):
        lines.append("DİKKAT: Bu alanda daha önce yüklenmiş bir teslim var. Yeni dosya onun YERİNE geçecek.")
    else:
        lines.append("Bu alanda daha önce yüklenmiş bir teslim yok; mevcut bir dosyanın üzerine yazılmayacak.")
    lines += ["", "Onaylıyor musunuz?"]
    return "\n".join(lines)


# -- progress ---------------------------------------------------------------

def progress_callback() -> ProgressCallback | None:
    interaction = current()
    return interaction.progress if interaction is not None else None


def progress_kwargs() -> dict[str, ProgressCallback]:
    """``{"progress": callback}`` when someone is listening, otherwise nothing,
    so callees keep being called with their original arguments."""
    callback = progress_callback()
    return {"progress": callback} if callback is not None else {}


class ProgressSteps:
    """Counts completed steps for one tool call. Inert without a callback."""

    def __init__(self, total: int | None = None, callback: ProgressCallback | None = None) -> None:
        self._callback = callback
        self.total = total
        self.done = 0

    def advance(self, message: str) -> None:
        """Announce the step that is starting; earlier steps count as done."""
        self._report(message)
        self.done += 1

    def finish(self, message: str) -> None:
        if self.total is not None:
            self.done = max(self.done, self.total)
        self._report(message)

    def _report(self, message: str) -> None:
        if self._callback is None:
            return
        try:
            self._callback(self.done, self.total, _clean(message, 160))
        except Exception:
            # Progress is advisory; it must never change a tool's outcome.
            pass


def progress_steps(total: int | None = None) -> ProgressSteps:
    return ProgressSteps(total, progress_callback())
