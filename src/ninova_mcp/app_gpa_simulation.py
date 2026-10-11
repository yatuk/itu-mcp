"""Cumulative GPA simulation tool: ``obs_simulate_cumulative_gpa``.

The arithmetic lives in ``gpa_simulation``; this module reads the three OBS
payloads and hands them over.

This module does not use ``from __future__ import annotations``: the tool
signature is read when the tool is registered, and real ``Annotated`` types
are what give every parameter a JSON schema type and a description.
"""

from typing import Annotated, Any

from pydantic import ConfigDict, Field
from typing_extensions import NotRequired, TypedDict

from .client import NinovaError
from .obs_client import ObsClient
from .tool_metadata.gpa_simulation import (
    DEFAULT_GRADE_DESCRIPTION,
    EXTRA_CODE_DESCRIPTION,
    EXTRA_COURSES_DESCRIPTION,
    EXTRA_CREDIT_DESCRIPTION,
    EXTRA_GRADE_DESCRIPTION,
    EXTRA_REPLACES_DESCRIPTION,
    PROJECTED_GRADES_DESCRIPTION,
    SEMESTER_DESCRIPTION,
    TARGET_GPA_DESCRIPTION,
)


class ExtraCourse(TypedDict):
    """A hypothetical course for ``extra_courses``."""

    __pydantic_config__ = ConfigDict(extra="forbid")  # type: ignore[misc]

    code: Annotated[str, Field(description=EXTRA_CODE_DESCRIPTION)]
    grade: Annotated[str, Field(description=EXTRA_GRADE_DESCRIPTION)]
    credit: NotRequired[Annotated[float, Field(ge=0, description=EXTRA_CREDIT_DESCRIPTION)]]
    replaces: NotRequired[Annotated[str, Field(description=EXTRA_REPLACES_DESCRIPTION)]]


class GpaSimulationToolsMixin:
    """Tool method mixed into ``NinovaMcpApp``, which supplies ``obs``."""

    obs: ObsClient

    def obs_simulate_cumulative_gpa(
        self,
        projected_grades: Annotated[
            dict[str, str] | None, Field(description=PROJECTED_GRADES_DESCRIPTION),
        ] = None,
        default_grade: Annotated[str | None, Field(description=DEFAULT_GRADE_DESCRIPTION)] = None,
        extra_courses: Annotated[
            list[ExtraCourse] | None, Field(description=EXTRA_COURSES_DESCRIPTION),
        ] = None,
        semester: Annotated[str | int | None, Field(description=SEMESTER_DESCRIPTION)] = None,
        target_gpa: Annotated[float | None, Field(ge=0, le=4, description=TARGET_GPA_DESCRIPTION)] = None,
    ) -> dict[str, Any]:
        """Project the cumulative GPA after a term from the student's own OBS record.

        Reads the graduation plan (the transcript), the official per-term
        record and the registered courses of the projected term. Nothing is
        written anywhere.
        """
        from .gpa_simulation import normalize_arguments, simulate_cumulative_gpa

        # Reject a bad grade or a malformed argument before OBS is contacted.
        normalize_arguments(projected_grades, default_grade, extra_courses, target_gpa)
        if isinstance(semester, bool) or not isinstance(semester, (str, int, type(None))):
            raise ValueError('semester must be a term code, id or name, e.g. "202610".')
        wanted = None if semester is None else str(semester).strip() or None

        resolved = self.obs.resolve_semester(wanted)
        registered = self.obs.list_registered_courses(resolved["akademikDonemId"])
        if not isinstance(registered, dict) or (
            "statusCode" in registered
            and (type(registered["statusCode"]) is not int or registered["statusCode"] != 0)
        ):
            raise NinovaError("Registered courses could not be read successfully; nothing was simulated.")
        rows = registered.get("kayitSinifResultList")
        if rows is not None and (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)):
            raise NinovaError("Registered-course data has an unrecognized shape; nothing was simulated.")

        graduation = self.obs.get_graduation_remaining(self.obs.default_program_id())
        if not isinstance(graduation, dict) or not isinstance(graduation.get("mezuniyetimeNeKaldiBilgi"), dict):
            raise NinovaError("The graduation record could not be read; nothing was simulated.")

        status_error = False
        try:
            status = self.obs.get_registration_status()
        except (NinovaError, ValueError):
            status, status_error = None, True

        result = simulate_cumulative_gpa(
            graduation, status, registered,
            term_code=resolved.get("donemKodu"),
            projected_grades=projected_grades,
            default_grade=default_grade,
            extra_courses=extra_courses,
            target_gpa=target_gpa,
        )
        if status_error:
            result["warnings"].append(
                "official_series_unavailable: the official per-term record could not be read, "
                "so the model was checked against the current official GPA only."
            )
        return {
            "semester": {
                "id": resolved.get("akademikDonemId"),
                "code": resolved.get("donemKodu"),
                "name": resolved.get("akademikDonemAdi"),
            },
            **{key: value for key, value in result.items() if key != "term"},
        }
