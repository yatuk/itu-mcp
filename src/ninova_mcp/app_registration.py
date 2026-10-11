"""OBS course registration tools: status, saved draft, elective groups, plan check.

``obs_save_registration_draft`` is the only tool that changes the student's
account. Its strict argument types and the operator opt-in live here too.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from pydantic import Field, StrictBool

from .client import NinovaError
from .obs_client import ObsClient, ObsError, ObsPublicClient
from .tool_params import publish_include_raw

RegistrationDraftCrns = Annotated[
    list[Annotated[str, Field(strict=True, pattern=r"^[0-9]{4,5}$")]],
    Field(min_length=1, max_length=12, json_schema_extra={"uniqueItems": True}),
]


def obs_registration_writes_enabled() -> bool:
    """Account writes require the operator's explicit, exact opt-in."""
    return os.getenv("NINOVA_OBS_REGISTRATION_WRITES") == "1"


def _confirm_draft_write(plan: dict[str, Any]) -> dict[str, Any] | None:
    """Ask the user about the exact draft change that is about to be sent."""
    from .interaction import confirm_write, draft_confirmation_message

    return confirm_write(draft_confirmation_message(
        plan["term_code"], plan["requested_crns"], plan["previous_crns"],
        plan["added_crns"], plan["removed_crns"],
    ))


class RegistrationToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``obs`` and ``obs_public``."""

    obs: ObsClient
    obs_public: ObsPublicClient

    def obs_get_registration_status(self, include_raw: bool = False) -> dict[str, Any]:
        from requests import RequestException
        from .output_shape import RAW_AVAILABLE_KEY, raw_hint
        from .registration_status import compact_registration_summary, summarize_registration_status

        registration = self.obs.get_registration_status()
        lesson_registration = self.obs.get_lesson_registration_status()
        calendar = None
        calendar_error = None
        try:
            calendar = self.obs_public.get_academic_calendar()
        except (NinovaError, RequestException, ValueError):
            calendar_error = "The public academic calendar is unavailable. Official OBS status is retained."
        summary = summarize_registration_status(
            registration, lesson_registration, calendar, calendar_error=calendar_error,
        )
        if include_raw:
            return {
                "kayit_durumu": registration,
                "ders_kayit_durumu": lesson_registration,
                "summary": summary,
            }
        return {
            "summary": compact_registration_summary(summary, registration),
            RAW_AVAILABLE_KEY: raw_hint("kayit_durumu", "ders_kayit_durumu", "the unreduced summary"),
        }

    def obs_get_registration_draft(self) -> dict[str, Any]:
        return self.obs.get_registration_draft()

    def obs_save_registration_draft(
        self, crns: RegistrationDraftCrns, confirm: StrictBool, allow_replace: StrictBool = False,
    ) -> dict[str, Any]:
        """Replace a saved draft only with explicitly confirmed CRNs."""
        from .registration_draft import normalize_crns

        if confirm is not True:
            raise ObsError("Saving the OBS draft requires explicit confirmation (confirm=true).")
        if not obs_registration_writes_enabled():
            raise ObsError("OBS registration writes are disabled (NINOVA_OBS_REGISTRATION_WRITES=1 is required).")
        selected = normalize_crns(crns)
        # Draft reads are uncached. The client reconciles the attempted write
        # against a fresh readback; retain its saved/rejected/uncertain result.
        # With an MCP request (or NINOVA_REQUIRE_ELICITATION=1) the client asks
        # the user through confirm_write just before the single POST.
        from .interaction import current, elicitation_required

        gate: dict[str, Any] = {}
        if current() is not None or elicitation_required():
            gate["confirm_write"] = _confirm_draft_write
        return self.obs.save_registration_draft(selected, allow_replace=allow_replace is True, **gate)

    def obs_get_elective_group(self, group_id: int) -> dict[str, Any]:
        from .registration_tools import get_elective_group
        return get_elective_group(self, group_id)

    def obs_validate_registration_plan(self, crns: list[str]) -> dict[str, Any]:
        from .registration_tools import validate_plan
        return validate_plan(self, crns)


publish_include_raw(RegistrationToolsMixin, "obs_get_registration_status")
