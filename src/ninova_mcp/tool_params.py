"""Give tool parameters a published type and description.

The server copies a tool's signature without evaluating its annotations, so an
``Annotated`` written in the ``def`` line of a module that postpones
annotations would reach FastMCP as text it cannot resolve. A stored signature
with real annotation objects is copied as is.
"""

from __future__ import annotations

import inspect
from typing import Annotated, Any

from .tool_metadata.common import INCLUDE_RAW_DESCRIPTIONS


def publish_parameter_schema(function: Any, **annotations: Any) -> None:
    """Replace the annotation of the named parameters in ``function``'s signature.

    The return annotation is left untouched, which keeps the output schema
    unchanged.
    """
    signature = inspect.signature(function)
    function.__signature__ = signature.replace(parameters=[
        parameter.replace(annotation=annotations.get(name, parameter.annotation))
        for name, parameter in signature.parameters.items()
    ])


def publish_include_raw(owner: type, *tool_names: str) -> None:
    """Publish ``include_raw`` of the named tool methods as a described boolean."""
    try:
        from pydantic import Field
    except ImportError:  # pragma: no cover - pydantic ships with the MCP SDK
        return
    for name in tool_names:
        publish_parameter_schema(
            getattr(owner, name),
            include_raw=Annotated[bool, Field(description=INCLUDE_RAW_DESCRIPTIONS[name])],
        )
