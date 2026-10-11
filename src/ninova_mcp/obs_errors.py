"""Error type shared by the OBS clients and the modules built on them."""

from __future__ import annotations

from .client import NinovaError


class ObsError(NinovaError):
    """OBS-specific error."""
