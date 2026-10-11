"""Course registration endpoints of the OBS API: status, draft, CRN check."""

from __future__ import annotations

import time
from typing import Any, Callable

from .obs_errors import ObsError
from .obs_session import ObsSessionCore


class ObsRegistrationApi(ObsSessionCore):
    """Registration status, the saved draft and the independent CRN check."""

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
            for attempt in range(2):
                if self._last_registration_check is not None:
                    delay = 2.0 - (time.monotonic() - self._last_registration_check)
                    if delay > 0:
                        time.sleep(delay)
                try:
                    payload = self._api_request("POST", VALIDATION_PATH, json_body={"ecrn": selected})
                finally:
                    self._last_registration_check = time.monotonic()
                if (attempt == 0 and isinstance(payload, dict)
                        and payload.get("resultCode") in ("VAL16", "ERRMaxIstekTaslakKontrol")):
                    continue
                return normalize_validation(payload, selected)
        # Not reached: the second attempt always returns or raises. Kept so the
        # function visibly ends in a return or a raise on every path.
        raise ObsError("OBS CRN validation remained busy.")
