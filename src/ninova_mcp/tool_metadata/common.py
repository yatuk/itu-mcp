"""Parameter metadata shared by tools of more than one domain.

``include_raw`` means the same thing on every tool that has it: the default
result is the reduced answer, and ``include_raw=true`` adds the upstream
payload it was reduced from. It is not ``compact``, which shortens long texts
and lists of whatever the result holds; a tool with both applies
``include_raw`` first and ``compact`` to the result of that.
"""

from __future__ import annotations

from typing import Any

_UNREDUCED = (
    "Return the OBS payload unreduced: with its statusCode/resultCode/resultMessage envelope "
    "and the English twin of every label. Default false: the envelope of a successful answer "
    "and repeated English labels are left out."
)

INCLUDE_RAW_DESCRIPTIONS: dict[str, str] = {
    "obs_get_profile": _UNREDUCED + " Redaction is applied either way.",
    "obs_list_programs": _UNREDUCED,
    "obs_list_semesters": _UNREDUCED,
    "obs_get_advisor": _UNREDUCED,
    "obs_get_internships": _UNREDUCED,
    "obs_get_contacts": _UNREDUCED + " Redaction is applied either way.",
    "obs_list_registered_courses": (
        "Return every row as OBS sends it, with the term repeated on each row in both languages. "
        "Default false: one language per label and the term stated once in semester."
    ),
    "obs_get_course_grades": (
        "Return the class row and the midterm payload as OBS sends them. Default false: "
        "no success envelope, one language per label, the term stated once."
    ),
    "obs_get_attendance": (
        "Return the class row and the attendance payload as OBS sends them. Default false: "
        "no success envelope, one language per label, the term stated once."
    ),
    "obs_get_schedule": (
        "Also return schedule, the OBS class rows weekly_schedule was built from (times as minutes "
        "after midnight), and the final calendar unreduced. Default false."
    ),
    "obs_get_graduation_remaining": (
        "Also return graduation, the whole MezuniyetimeNeKaldi payload (every plan row, about "
        "25 KB) that summary was built from. Default false: summary answers what is left."
    ),
    "get_personal_exam_calendar": (
        "Return matched_courses as full OBS rows and the final calendar unreduced. Default false: "
        "a matched course is named by code, CRN and title only."
    ),
    "obs_get_registration_status": (
        "Also return kayit_durumu and ders_kayit_durumu, the two OBS payloads, and the summary with "
        "its fixed notices. Default false: the summary with term_history is enough for status, "
        "class level, GPA of any term and the registration window."
    ),
    "explain_course_eligibility": (
        "Also return the whole completed-course history (completed_courses, completed_grades), "
        "requirement_tree, minimum_grades and explanation_parts. Default false: the verdict, the "
        "requirement as text and the grades of the prerequisite courses."
    ),
    "get_academic_calendar": (
        "Return every event with all its fields, including empty ones, the site's row id (uid) and "
        "the timezone, source_url and academic_year repeated on each event. Default false."
    ),
    "get_dashboard": (
        "Also return links (every link of the page, mostly site navigation) and page.headings. "
        "Default false: link_count only. With compact=true the list is still shortened."
    ),
}


def include_raw_property(tool: str) -> dict[str, Any]:
    """The ``include_raw`` entry of a tool's input schema."""
    return {"type": "boolean", "default": False, "description": INCLUDE_RAW_DESCRIPTIONS[tool]}
