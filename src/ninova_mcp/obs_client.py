"""Client for İTÜ OBS (obs.itu.edu.tr) student portal JSON APIs.

OBS is a Vue SPA. Auth reuses the same İTÜ SSO cookies as Ninova, then
exchanges them for a short-lived JWT via ``/ogrenci/auth/jwt``.

The clients are assembled here from their parts; every name that was ever
importable from this module still is:

- ``obs_session``: SSO session, JWT and the request core
- ``obs_student_api`` and ``obs_registration_api``: the student endpoints
- ``obs_public_transport`` and the ``obs_public_*`` modules: the public pages
- ``obs_semesters``, ``obs_redaction``, ``obs_errors``: plain helpers
"""

from __future__ import annotations

# Tests patch the clock as ``ninova_mcp.obs_client.time``; keep the name here.
import time  # noqa: F401

from .obs_errors import ObsError
from .obs_public_calendar import ObsPublicCalendar
from .obs_public_degree_plans import ObsPublicDegreePlans
from .obs_public_prerequisites import ObsPublicPrerequisites
from .obs_public_schedule import ObsPublicSchedule
from .obs_redaction import (
    _EMAIL as _EMAIL,
    _REDACTION_NOTE as _REDACTION_NOTE,
    _SENSITIVE_CONTACT_TYPE as _SENSITIVE_CONTACT_TYPE,
    _SENSITIVE_KEY as _SENSITIVE_KEY,
    REDACTED,
    redact_obs_profile,
)
from .obs_registration_api import ObsRegistrationApi
from .obs_semesters import (
    _SEASON_WORDS as _SEASON_WORDS,
    _TERM_FILLER_WORDS as _TERM_FILLER_WORDS,
    _resolve_semester_by_name as _resolve_semester_by_name,
    _semester_lookup_text as _semester_lookup_text,
    _semester_recency as _semester_recency,
    _semester_tokens as _semester_tokens,
    describe_semester,
)
from .obs_session import DEFAULT_OBS_BASE_URL, JWT_PATH, STUDENT_HOME
from .obs_student_api import ObsStudentRecordsApi

__all__ = [
    "DEFAULT_OBS_BASE_URL",
    "JWT_PATH",
    "ObsClient",
    "ObsError",
    "ObsPublicClient",
    "REDACTED",
    "STUDENT_HOME",
    "describe_semester",
    "redact_obs_profile",
]


class ObsClient(ObsStudentRecordsApi, ObsRegistrationApi):
    """Authenticated client for the OBS student API.

    The session and request core is ``ObsSessionCore``; the endpoint groups
    are ``ObsStudentRecordsApi`` and ``ObsRegistrationApi``.
    """


class ObsPublicClient(ObsPublicPrerequisites, ObsPublicSchedule, ObsPublicCalendar, ObsPublicDegreePlans):
    """Client for OBS **public** (no-auth) endpoints: course catalog and prerequisites.

    These endpoints return HTML, not JSON, so parsing is handled by
    ``parsing.py`` extractors.  No JWT or login is needed.
    """
