"""Course registration endpoints of the OBS API: status, draft, CRN check."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .client import NinovaClient
from .obs_session import ObsSessionCore


class ObsRegistrationApi(ObsSessionCore):
    """Registration status, the saved draft and the independent CRN check."""

    def __init__(
        self,
        ninova_client: NinovaClient | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(ninova_client, base_url)
        # CRN checks through one client are serialised and spaced apart.
        self._registration_check_lock = threading.Lock()
        self._last_registration_check: float | None = None

    def get_registration_status(self) -> dict[str, Any]:
        return self.api_get("/api/ogrenci/KayitDurumu")

    def get_lesson_registration_status(self) -> dict[str, Any]:
        return self.api_get("/api/ogrenci/DersKayitDurumu")

    def get_registration_draft(self) -> dict[str, Any]:
        from .registration_draft import read_registration_draft
        return read_registration_draft(self)

    def save_registration_draft(
        self, crns: list[str], *, allow_replace: bool = False,
        confirm_write: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    ) -> dict[str, Any]:
        """Save a draft once and reconcile its contents; this does not enroll."""
        from .registration_write import save_registration_draft
        gate = {"confirm_write": confirm_write} if confirm_write is not None else {}
        return save_registration_draft(self, crns, allow_replace=allow_replace, **gate)

    def validate_registration_crns(self, crns: list[str]) -> dict[str, Any]:
        """Run OBS's independent CRN check without saving or registering courses."""
        from .registration_draft import VALIDATION_PATH, normalize_crns, normalize_validation
        selected = normalize_crns(crns)
        # OBS retains a short transaction lock after a check completes. Space
        # independent section checks and serialize calls sharing this client.
        with self._registration_check_lock:
            payload = self._spaced_crn_check(VALIDATION_PATH, selected)
            if _check_was_busy(payload):
                # One retry only; whatever the second answer is, it is reported.
                payload = self._spaced_crn_check(VALIDATION_PATH, selected)
            return normalize_validation(payload, selected)

    def _spaced_crn_check(self, path: str, selected: list[str]) -> Any:
        """Send one CRN check, at least two seconds after the previous one ended."""
        if self._last_registration_check is not None:
            delay = 2.0 - (time.monotonic() - self._last_registration_check)
            if delay > 0:
                time.sleep(delay)
        try:
            return self._api_request("POST", path, json_body={"ecrn": selected})
        finally:
            self._last_registration_check = time.monotonic()


def _check_was_busy(payload: Any) -> bool:
    """True when OBS refused the check because its transaction lock was still held."""
    return isinstance(payload, dict) and payload.get("resultCode") in ("VAL16", "ERRMaxIstekTaslakKontrol")
