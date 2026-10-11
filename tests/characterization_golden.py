"""Golden-master helper for characterization tests.

A scenario is a name and a zero-argument callable. Its outcome (the returned
value, or the type and text of the exception) is serialized to JSON with the
key order kept, and compared as text with the copy recorded from the code as
it was before a refactor. Key order is therefore part of what is pinned.

The recordings are gzip-compressed JSON; ``python -m gzip -d`` reads one. A
failing comparison prints both sides as indented text. A recorded file is
rewritten only when ``ITU_MCP_UPDATE_GOLDEN=1`` is set, which is meant for adding
scenarios, never for making a failing test pass.
"""

from __future__ import annotations

import gzip
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "characterization"


def outcome(call: Callable[[], Any]) -> dict[str, Any]:
    """What a call did: its value, or the exception type and message."""
    try:
        return {"returned": call()}
    except Exception as exc:  # noqa: BLE001 - the exact type and text are what gets pinned
        return {"raised": type(exc).__name__, "message": str(exc)}


def serialize(value: Any) -> str:
    """Readable text of a value, for comparing and for a failure diff."""
    return json.dumps(value, ensure_ascii=False, indent=1, sort_keys=False)


def load(name: str) -> dict[str, Any]:
    path = GOLDEN_DIR / name
    if not path.exists():
        return {}
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))


def updating() -> bool:
    return os.environ.get("ITU_MCP_UPDATE_GOLDEN") == "1"


def save(name: str, recorded: dict[str, Any]) -> None:
    # The recordings repeat the same long texts hundreds of times, so they are
    # stored compressed; ``mtime=0`` keeps the bytes the same for the same data.
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(recorded, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    (GOLDEN_DIR / name).write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
