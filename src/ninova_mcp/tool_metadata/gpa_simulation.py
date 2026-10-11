"""Tool metadata: cumulative GPA simulation."""

from __future__ import annotations

from typing import Any

_GRADES = "AA, BA+, BA, BB+, BB, CB+, CB, CC+, CC, DC+, DC, DD+, DD, FD, FF, VF"

# Parameter descriptions are shared with the method signature in
# ``app_gpa_simulation`` so the published schema and this entry say the same.
PROJECTED_GRADES_DESCRIPTION = (
    "Letter grade per currently registered course, keyed by course code or CRN, "
    'e.g. {"CEN 411E": "BB", "15570": "BA"}. '
    f"Grades: {_GRADES}. A key that matches no registered course is reported, not ignored."
)
DEFAULT_GRADE_DESCRIPTION = (
    "Letter grade for every registered course that projected_grades does not name, "
    'e.g. "BB" for "BB in everything". Without it, unnamed courses are left out and listed.'
)
EXTRA_COURSES_DESCRIPTION = (
    "Hypothetical courses that are not registered now, applied after the projected term, "
    'e.g. [{"code": "MAT 271E", "grade": "CC", "credit": 3}]. A code already in the record '
    "replaces its earlier grade."
)
SEMESTER_DESCRIPTION = (
    "Term whose registered courses are projected: term code, OBS term id or name, "
    'e.g. "202610", 202610 or "2025-2026 Bahar". Default: the latest term.'
)
TARGET_GPA_DESCRIPTION = (
    "Cumulative GPA to reach after this term, e.g. 2.5. Adds a target block: the lowest single "
    "letter grade that reaches it when given to every registered course not named in "
    "projected_grades, or that it is out of reach even with AA, with the maximum reachable."
)
EXTRA_CODE_DESCRIPTION = 'Course code, e.g. "MAT 271E".'
EXTRA_GRADE_DESCRIPTION = f'Letter grade, e.g. "CC". One of: {_GRADES}.'
EXTRA_CREDIT_DESCRIPTION = (
    "Credit of the course, e.g. 3 or 3.5. Needed only when the course is not in the student's record or plan."
)
EXTRA_REPLACES_DESCRIPTION = (
    'Code of an earlier course this one replaces under a different code, e.g. "BLG 335E".'
)

GPA_SIMULATION_TOOLS: list[dict[str, Any]] = [
    {
        "name": "obs_simulate_cumulative_gpa",
        "title": "Simulate Cumulative GPA",
        "description": (
            "Project the cumulative GPA (GANO) after a term in one call, for questions like "
            "'what will my GPA be if I get BB in every course this term?'. Rebuilds the current "
            "cumulative GPA from the student's own OBS record, checks it against the official GPA "
            "after every past term, then applies the projected grades to the registered courses. "
            "A retaken course replaces its earlier grade instead of adding credit, including a "
            "retake under a different course code. The result shows the work: per-course effect, "
            "credit and point deltas, courses still failing, equivalences used and warnings. "
            "For 'what do I need this term to reach X?' pass target_gpa: the answer comes from the "
            "record and counts retakes, which calculate_target_gpa cannot see. "
            "Read-only estimate, not an official result. For a single term average use "
            "obs_calculate_gpa."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "projected_grades": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": PROJECTED_GRADES_DESCRIPTION,
                },
                "default_grade": {
                    "type": "string",
                    "description": DEFAULT_GRADE_DESCRIPTION,
                },
                "extra_courses": {
                    "type": "array",
                    "description": EXTRA_COURSES_DESCRIPTION,
                    "items": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": EXTRA_CODE_DESCRIPTION},
                            "grade": {"type": "string", "description": EXTRA_GRADE_DESCRIPTION},
                            "credit": {"type": "number", "minimum": 0, "description": EXTRA_CREDIT_DESCRIPTION},
                            "replaces": {"type": "string", "description": EXTRA_REPLACES_DESCRIPTION},
                        },
                        "required": ["code", "grade"],
                        "additionalProperties": False,
                    },
                },
                "semester": {
                    "type": ["string", "integer"],
                    "description": SEMESTER_DESCRIPTION,
                },
                "target_gpa": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 4,
                    "description": TARGET_GPA_DESCRIPTION,
                },
            },
            "additionalProperties": False,
        },
    },
]
