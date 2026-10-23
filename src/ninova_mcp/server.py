"""Public surface of the İTÜ MCP server.

Nothing is implemented here. This module puts the pieces together and
re-exports the names other code relies on: the ``itu-mcp`` entry point
(``ninova_mcp.server:main``), ``remote.py``, ``scripts/build_mcpb.py`` and the
tests all import from ``ninova_mcp.server``.

Where things live:

- ``server_info``: server name, version and the instructions text
- ``tool_metadata``: name, title, description and input schema of each tool
- ``tool_registry``: the full ``TOOLS`` list and the tool name sets
- ``app_base``: shared state and the lazy clients
- ``app_*``: one mixin of tool methods per domain
- ``app``: ``NinovaMcpApp``, composed from the mixins
- ``server_build``: registration on FastMCP and the stdio server builder
- ``cli``: argument parser and ``main``

A new feature should follow ``archive_tools``, ``seat_watch`` and
``calendar_export``: its own module with a mixin and a metadata list, wired in
through ``app`` and ``tool_registry``.

The client classes, ``load_ninova_env`` and ``match_upload_slot`` are imported
here because the code that uses them reads them from this module when it runs
(see ``app_base.server_namespace``). Patching one of them on
``ninova_mcp.server`` therefore still changes what the app does. The other
names below are plain re-exports: patch those where they are defined.
"""

from __future__ import annotations

from .app import NinovaMcpApp
from .app_base import COURSES_CACHE_KEY, DEFAULT_COURSE_CACHE_TTL_SECONDS
from .app_ninova import MAX_UPLOAD_BYTES
from .app_registration import (
    RegistrationDraftCrns,
    _confirm_draft_write,  # noqa: F401
    obs_registration_writes_enabled,
)
from .archive_client import ItuArchiveClient
from .cli import build_arg_parser, main
from .client import NinovaClient
from .community_data import CrossCheckDataClient
from .env import load_ninova_env
from .library_client import LibraryClient
from .moodle_client import MoodleClient
from .pool_client import PoolSiteClient
from .mail_client import ItuMailClient
from .obs_client import ObsClient, ObsPublicClient
from .parsing import match_upload_slot
from .public_client import ItuPublicClient
from .server_build import (
    apply_server_version,
    build_stdio_server,
    register_prompts,
    register_resources,
    register_tools,
)
from .server_info import SERVER_INSTRUCTIONS, SERVER_NAME, SERVER_VERSION
from .tool_registry import (
    DESTRUCTIVE_TOOL_NAMES,
    LOCAL_TOOL_NAMES,
    MAIL_TOOL_NAMES,
    OBS_REGISTRATION_WRITE_TOOL_NAMES,
    REMOTE_EXCLUDED_TOOLS,
    REMOTE_TOOL_NAMES,
    STATEFUL_TOOL_NAMES,
    TOOLS,
)

__all__ = [
    "COURSES_CACHE_KEY",
    "CrossCheckDataClient",
    "DEFAULT_COURSE_CACHE_TTL_SECONDS",
    "DESTRUCTIVE_TOOL_NAMES",
    "ItuArchiveClient",
    "ItuMailClient",
    "ItuPublicClient",
    "LOCAL_TOOL_NAMES",
    "LibraryClient",
    "MoodleClient",
    "PoolSiteClient",
    "MAIL_TOOL_NAMES",
    "MAX_UPLOAD_BYTES",
    "NinovaClient",
    "NinovaMcpApp",
    "OBS_REGISTRATION_WRITE_TOOL_NAMES",
    "ObsClient",
    "ObsPublicClient",
    "REMOTE_EXCLUDED_TOOLS",
    "REMOTE_TOOL_NAMES",
    "RegistrationDraftCrns",
    "SERVER_INSTRUCTIONS",
    "SERVER_NAME",
    "SERVER_VERSION",
    "STATEFUL_TOOL_NAMES",
    "TOOLS",
    "apply_server_version",
    "build_arg_parser",
    "build_stdio_server",
    "load_ninova_env",
    "main",
    "match_upload_slot",
    "obs_registration_writes_enabled",
    "register_prompts",
    "register_resources",
    "register_tools",
]


if __name__ == "__main__":
    main()
