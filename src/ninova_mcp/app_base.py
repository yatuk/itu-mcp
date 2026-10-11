"""Shared state of ``NinovaMcpApp``: state paths, caches and the lazy clients.

The tool methods live in the ``app_*`` mixin modules and reach everything
here through ``self``.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .cache import TtlCache, parse_ttl_seconds
from .compact import maybe_compact

if TYPE_CHECKING:
    from .archive_client import ItuArchiveClient
    from .client import NinovaClient
    from .community_data import CrossCheckDataClient
    from .library_client import LibraryClient
    from .mail_client import ItuMailClient
    from .obs_client import ObsClient, ObsPublicClient
    from .public_client import ItuPublicClient

DEFAULT_COURSE_CACHE_TTL_SECONDS = 60.0
COURSES_CACHE_KEY = "courses"


def server_namespace() -> Any:
    """Return ``ninova_mcp.server``, looked up when called.

    ``ninova_mcp.server`` is the public surface of this package, and the tests
    replace names on it: ``load_ninova_env`` so no ``.env`` file is read, a
    client class so a test can prove no client gets built, and
    ``match_upload_slot``. Code that moved out of ``server.py`` reaches those
    names through this function, so such a patch still takes effect.

    The import is inside the function on purpose. ``server`` imports this
    module, so a module-level import would be circular, and the names have to
    be read at call time anyway for a patch to be seen.
    """
    from . import server

    return server


class AppBase:
    """State and clients shared by every tool mixin.

    Clients are built on first use. Their classes and ``load_ninova_env`` are
    read from ``ninova_mcp.server`` (see ``server_namespace``).
    """

    def __init__(self) -> None:
        server_namespace().load_ninova_env()
        self._client: NinovaClient | None = None
        self._obs: ObsClient | None = None
        self._obs_public: ObsPublicClient | None = None
        self._itu_public: ItuPublicClient | None = None
        self._library: LibraryClient | None = None
        self._mail: ItuMailClient | None = None
        self._archive: ItuArchiveClient | None = None
        self._prereq_crosscheck: CrossCheckDataClient | None = None
        # One lock for every lazy client property below. The remote HTTP
        # transport dispatches these synchronous tool methods to a thread
        # pool, so two concurrent requests can otherwise both observe a
        # None client and each construct their own — each running its own
        # independent login, with whichever assignment lands last silently
        # orphaning the other's session (a leak) while callers still holding
        # the orphaned reference continue against a disconnected cookie jar.
        # Contention is negligible since this only runs once per client.
        # RLock, not Lock: obs's property constructs its client while still
        # holding the lock, and does so by reading self.client — which would
        # otherwise try to re-acquire the same lock on the same thread.
        self._client_lock = threading.RLock()
        state_root = os.getenv("NINOVA_STATE_DIR") or str(Path.home() / ".ninova_state")
        self.state_dir = Path(state_root)
        self.snapshot_dir = self.state_dir / "snapshots"
        self.tracking_state_path = self.state_dir / "tracking-state.json"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        course_ttl = parse_ttl_seconds(
            os.getenv("NINOVA_COURSE_CACHE_TTL_SECONDS"),
            DEFAULT_COURSE_CACHE_TTL_SECONDS,
        )
        self._course_cache: TtlCache[list[dict[str, Any]]] = TtlCache(course_ttl)
        self._course_cache_ttl_seconds = course_ttl

    @property
    def client(self) -> NinovaClient:
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = server_namespace().NinovaClient()
        return self._client

    @property
    def obs(self) -> ObsClient:
        if self._obs is None:
            with self._client_lock:
                if self._obs is None:
                    self._obs = server_namespace().ObsClient(ninova_client=self.client)
        return self._obs

    @property
    def obs_public(self) -> ObsPublicClient:
        if self._obs_public is None:
            with self._client_lock:
                if self._obs_public is None:
                    # Public OBS tools must work without credentials and must
                    # not send authenticated SSO cookies to no-auth endpoints.
                    self._obs_public = server_namespace().ObsPublicClient()
        return self._obs_public

    @property
    def itu_public(self) -> ItuPublicClient:
        if self._itu_public is None:
            with self._client_lock:
                if self._itu_public is None:
                    self._itu_public = server_namespace().ItuPublicClient()
        return self._itu_public

    @property
    def library(self) -> LibraryClient:
        if self._library is None:
            with self._client_lock:
                if self._library is None:
                    self._library = server_namespace().LibraryClient()
        return self._library

    @property
    def mail(self) -> ItuMailClient:
        if self._mail is None:
            with self._client_lock:
                if self._mail is None:
                    self._mail = server_namespace().ItuMailClient()
        return self._mail

    @property
    def archive(self) -> ItuArchiveClient:
        if self._archive is None:
            with self._client_lock:
                if self._archive is None:
                    self._archive = server_namespace().ItuArchiveClient()
        return self._archive

    @property
    def prereq_crosscheck(self) -> CrossCheckDataClient:
        if self._prereq_crosscheck is None:
            with self._client_lock:
                if self._prereq_crosscheck is None:
                    self._prereq_crosscheck = server_namespace().CrossCheckDataClient()
        return self._prereq_crosscheck

    def invalidate_caches(self) -> None:
        self._course_cache.clear()
        if self._obs_public is not None:
            self._obs_public._cache.clear()
        if self._itu_public is not None:
            self._itu_public._cache.clear()
        if self._library is not None:
            self._library._cache.clear()
        if self._archive is not None:
            self._archive._cache.clear()
        if self._prereq_crosscheck is not None:
            self._prereq_crosscheck._cache.clear()

    def _out(self, payload: dict[str, Any], compact: bool = False) -> dict[str, Any]:
        # Per-call compact=True wins; otherwise honor NINOVA_COMPACT_DEFAULT.
        from .compact import compact_default_enabled

        enabled = True if compact else compact_default_enabled()
        return maybe_compact(payload, compact=enabled)
