"""Ninova MCP server."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .server import main

__all__ = ["main"]


def __getattr__(name: str) -> Any:
    # ``main`` is loaded on first use instead of at package import. An eager
    # ``from .server import main`` here put ``ninova_mcp.server`` in
    # ``sys.modules`` before ``python -m ninova_mcp.server`` ran it, which made
    # runpy print a RuntimeWarning.
    if name == "main":
        from .server import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
