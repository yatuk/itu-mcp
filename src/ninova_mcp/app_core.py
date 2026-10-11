"""Session tools: login status, forced relogin and the OBS token check."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .client import NinovaAuthError, NinovaClient
from .credentials import credentials_available
from .obs_client import ObsClient, ObsError


class SessionToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies the clients and caches."""

    client: NinovaClient
    obs: ObsClient
    state_dir: Path

    def auth_status(self) -> dict[str, Any]:
        credentials_present = credentials_available()
        status: dict[str, Any] = {
            "credentials_present": credentials_present,
            "state_dir": str(self.state_dir),
            "course_cache_ttl_seconds": self._course_cache_ttl_seconds,
        }
        if not credentials_present:
            status["authenticated"] = False
            status["message"] = (
                "Set NINOVA_USERNAME and NINOVA_PASSWORD to enable login, "
                "or run `itu-mcp setup`."
            )
            return status

        try:
            session = self.client.ensure_logged_in(verify=True)
            status["authenticated"] = True
            status["session"] = session
        except NinovaAuthError as exc:
            status["authenticated"] = False
            status["message"] = str(exc)
            return status

        try:
            status["obs"] = self.obs.ensure_ready()
        except Exception as exc:  # pragma: no cover - optional subsystem
            status["obs"] = {"jwt_present": False, "error": str(exc)}
        return status

    def refresh_session(self) -> dict[str, Any]:
        self.invalidate_caches()
        session = self.client.login(force=True)
        if self._obs is not None:
            # A forced Ninova relogin does nothing for OBS-backed tools on
            # its own: ObsClient caches its JWT independently and, without
            # this, would keep reusing the old token (tied to the now-dead
            # session) until its TTL happens to expire on its own.
            self._obs._jwt = None
            self._obs._jwt_obtained_at = None
        return {
            "authenticated": True,
            "session": session,
        }

    def obs_auth_status(self) -> dict[str, Any]:
        try:
            ready = self.obs.ensure_ready()
            return {"ok": True, **ready}
        except (NinovaAuthError, ObsError) as exc:
            return {"ok": False, "error": str(exc)}
