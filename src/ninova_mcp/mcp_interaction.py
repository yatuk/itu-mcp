"""Bridge between FastMCP's request context and the synchronous tool methods.

FastMCP calls a synchronous tool function directly on the event loop. While
that function runs, the loop cannot send a progress notification or receive
the answer to an elicitation request. The tools listed in
``INTERACTIVE_TOOL_NAMES`` are therefore run in a worker thread, with a
``ToolInteraction`` bound whose callbacks hop back onto the loop.
"""
from __future__ import annotations

from typing import Any, Callable

import anyio
import anyio.from_thread
import anyio.to_thread
from pydantic import BaseModel, Field

from .interaction import ToolInteraction, bind


class WriteConfirmation(BaseModel):
    """The single answer the user gives in the client's confirmation form."""

    onay: bool = Field(
        title="Onaylıyorum",
        description="Yukarıda yazan işlemin hesabınızda yapılmasını onaylıyorsanız işaretleyin.",
    )


def client_supports_form_elicitation(session: Any) -> bool:
    """True when the connected client declared form-mode elicitation."""
    from mcp import types

    try:
        if not session.check_client_capability(
            types.ClientCapabilities(elicitation=types.ElicitationCapability())
        ):
            return False
        declared = session.client_params.capabilities.elicitation
    except Exception:
        return False
    if declared is None:
        return False
    # An empty elicitation object means form mode (the original capability
    # shape). A client that lists only URL mode cannot show this form.
    return declared.form is not None or declared.url is None


def build_interaction(mcp: Any) -> ToolInteraction | None:
    """Describe the current MCP request, or None outside of one."""
    get_context = getattr(mcp, "get_context", None)
    if get_context is None:
        return None
    try:
        context = get_context()
        request_context = context.request_context
    except (LookupError, ValueError):
        return None

    def ask(message: str) -> str:
        try:
            result = anyio.from_thread.run(context.elicit, message, WriteConfirmation)
        except Exception:
            # Includes a client error, a malformed answer, and a request that
            # was cancelled while the dialog was open. None of them is a yes.
            return "error"
        if result.action == "accept":
            return "accept" if result.data.onay is True else "decline"
        return result.action if result.action in ("decline", "cancel") else "error"

    progress = None
    meta = request_context.meta
    if meta is not None and meta.progressToken is not None:
        state = {"last": None, "closed": False}

        def progress(done: float, total: float | None, message: str | None) -> None:
            # MCP requires the progress value to increase on every notification.
            if state["closed"] or (state["last"] is not None and done <= state["last"]):
                return
            try:
                anyio.from_thread.run(context.report_progress, done, total, message)
            except Exception:
                # Cancelled or disconnected: stop reporting, let the tool finish.
                state["closed"] = True
                return
            state["last"] = done

    return ToolInteraction(
        elicitation_supported=client_supports_form_elicitation(request_context.session),
        ask=ask,
        progress=progress,
    )


class ToolRunner:
    """Runs every tool call of one server, one at a time.

    Synchronous tools used to block the event loop, which also meant that two
    calls never overlapped on the shared HTTP sessions and caches. The lock
    keeps that guarantee now that some tools leave the loop free.
    """

    def __init__(self, mcp: Any) -> None:
        self._mcp = mcp
        self._lock: anyio.Lock | None = None

    async def run(self, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any], *, interactive: bool) -> Any:
        if self._lock is None:
            self._lock = anyio.Lock(fast_acquire=True)
        async with self._lock:
            if not interactive:
                return fn(*args, **kwargs)
            interaction = build_interaction(self._mcp)

            def call() -> Any:
                with bind(interaction):
                    return fn(*args, **kwargs)

            return await anyio.to_thread.run_sync(call)
