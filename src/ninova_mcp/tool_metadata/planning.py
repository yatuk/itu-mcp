"""Tool metadata: GPA and degree planning."""

from __future__ import annotations

from typing import Any

# Parameter descriptions are shared with the published signature in
# ``app_planning`` so the schema a client sees and this entry say the same.
TARGET_CURRENT_GPA_DESCRIPTION = "Current cumulative GPA (GANO) on the 4.00 scale, e.g. 2.35."
TARGET_CURRENT_CREDITS_DESCRIPTION = (
    "Credits that count in the current GPA, e.g. 101. Not ECTS and not the credits completed."
)
TARGET_TARGET_GPA_DESCRIPTION = "Cumulative GPA to reach, e.g. 2.5."
TARGET_FUTURE_CREDITS_DESCRIPTION = (
    "Total credits of the courses still to be taken, retakes included, e.g. 18."
)
TARGET_RETAKES_DESCRIPTION = (
    "The part of future_credits that repeats a course already counted in the current GPA, "
    'e.g. [{"credit": 3, "old_grade": "FF"}, {"credit": 4, "old_grade": "DD"}]. A retake '
    "replaces its old grade and adds no credit. Omit it only when no course is a retake."
)
TARGET_RETAKE_CREDIT_DESCRIPTION = "Credit of the retaken course, e.g. 3 or 3.5."
TARGET_RETAKE_OLD_GRADE_DESCRIPTION = (
    'Grade of the course that counts in the GPA now, e.g. "FF". One of: AA, BA+, BA, BB+, BB, '
    "CB+, CB, CC+, CC, DC+, DC, DD+, DD, FD, FF, VF."
)

PLANNING_TOOLS: list[dict[str, Any]] = [
    {
        "name": "obs_calculate_gpa",
        "title": "Calculate GPA",
        "description": (
            "Calculate a term GPA estimate from OBS registered courses and compare it with "
            "the official term record. For factual average questions use preferred_term_gpa "
            "and preferred_term_gpa_source; the legacy gpa field remains the calculated estimate. "
            "This is a term average, not cumulative GANO; for cumulative what-if questions use obs_simulate_cumulative_gpa. "
            "Supports projected grades for what-if scenarios, keyed by course code or CRN; "
            "check projection_status and unmatched_projected_keys before reporting a what-if. "
            "Each course may carry attempts (other terms of the same course, and "
            "likely_equivalent failed attempts under another code, which are unconfirmed). "
            "credit_ambiguity names courses whose plan row carries two credit values. "
            "Uses İTÜ 4.00-scale letter grade conversion."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "semester": {
                    "type": ["string", "integer", "number"],
                    "description": (
                        "Term code as a number or string (202410), internal term id "
                        "(akademikDonemId), or term name (\"2024-2025 Güz\"). Default: latest."
                    ),
                },
                "projected_grades": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": (
                        "Optional what-if grades. Keys are course codes (any spacing or case) "
                        'or CRNs of the term, values are letter grades, '
                        'e.g. {"BLG 223E": "AA", "21547": "BB"}.'
                    ),
                },
                "include_official": {
                    "type": "boolean", "default": True,
                    "description": "Read the matching official OBS term average for comparison.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "calculate_target_gpa",
        "title": "Calculate Target GPA",
        "description": (
            "Quick arithmetic without the student's record and without login: the average needed "
            "over future credits to reach a target cumulative GPA, from four numbers the caller "
            "supplies. It cannot see retakes; a retake replaces its old grade and adds no credit, "
            "so state them in retakes or the result understates what is reachable (see the "
            "assumption field). When the student is logged in, prefer "
            "obs_simulate_cumulative_gpa(target_gpa=...), which works from the record and "
            "handles retakes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "current_gpa": {
                    "type": "number", "minimum": 0, "maximum": 4,
                    "description": TARGET_CURRENT_GPA_DESCRIPTION,
                },
                "current_credits": {
                    "type": "number", "minimum": 0,
                    "description": TARGET_CURRENT_CREDITS_DESCRIPTION,
                },
                "target_gpa": {
                    "type": "number", "minimum": 0, "maximum": 4,
                    "description": TARGET_TARGET_GPA_DESCRIPTION,
                },
                "future_credits": {
                    "type": "number", "exclusiveMinimum": 0,
                    "description": TARGET_FUTURE_CREDITS_DESCRIPTION,
                },
                "retakes": {
                    "type": "array",
                    "description": TARGET_RETAKES_DESCRIPTION,
                    "items": {
                        "type": "object",
                        "properties": {
                            "credit": {
                                "type": "number", "exclusiveMinimum": 0,
                                "description": TARGET_RETAKE_CREDIT_DESCRIPTION,
                            },
                            "old_grade": {
                                "type": "string",
                                "description": TARGET_RETAKE_OLD_GRADE_DESCRIPTION,
                            },
                        },
                        "required": ["credit", "old_grade"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["current_gpa", "current_credits", "target_gpa", "future_credits"],
            "additionalProperties": False,
        },
    },
    {
        "name": "estimate_relative_grade",
        "title": "Estimate Relative Grade",
        "description": (
            "Estimate a likely letter grade from raw class scores under İTÜ's relative-"
            "grading (bağıl değerlendirme) rules, using both official methods: Method 1 "
            "(T-score against Table 1, an EXAMPLE class-level table from the regulation — "
            "the actual instructor may use different T-score cutoffs) and Method 2 (mean ± "
            "standard-deviation multiples, Table 2, a fixed formula with no table lookup). "
            "This is an estimate for planning purposes only, not the official grade. "
            "class_scores must be every student's raw 0-100 score who counts toward the "
            "relative-grading average (VF students are excluded from that average per the "
            "regulation and must not be included here)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "class_scores": {
                    "type": "array",
                    "items": {"type": "number", "minimum": 0, "maximum": 100},
                    "minItems": 2,
                    "description": (
                        "Raw 0-100 scores of every student counted toward the class "
                        "average (exclude VF students)."
                    ),
                },
                "my_score": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Your own raw 0-100 score.",
                },
            },
            "required": ["class_scores", "my_score"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_course_conflicts",
        "title": "Check Course Conflicts",
        "description": (
            "Check for schedule time conflicts between courses by CRN. "
            "Uses the public course schedule to look up session day/time/room. "
            "Give only the CRNs: the department of each CRN is found from the course archive "
            "(no login) and, for what is left, from the student's registered courses when "
            "logged in. Read status: ok, partial (missing_crns were not checked), not_found, "
            "or departments_required (pass department_codes)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "crns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of CRNs to check (e.g. ['30334', '30473']).",
                },
                "program_type": {
                    "type": "string",
                    "default": "LS",
                    "description": "Program type (default: 'LS' / Lisans).",
                },
                "department_code": {
                    "type": "string",
                    "description": (
                        "Optional single department to scan, e.g. 'MAT'. Omit it to let the "
                        "tool find the departments of the CRNs."
                    ),
                },
                "department_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 25,
                    "description": (
                        "Optional departments to scan, e.g. ['MAT', 'FIZ']; overrides "
                        "department_code. Omit it to let the tool find them."
                    ),
                },
            },
            "required": ["crns"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_degree_faculties",
        "title": "List Degree Faculties",
        "description": "List official faculty/unit ids used by the public OBS degree-plan form.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_degree_programs",
        "title": "List Degree Programs",
        "description": "List official OBS degree-program codes for a faculty and plan type (public).",
        "inputSchema": {
            "type": "object",
            "properties": {"faculty_id": {"type": "integer", "minimum": 1}, "plan_type": {"type": "string", "default": "lisans"}},
            "required": ["faculty_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "build_degree_plan",
        "title": "Build Degree Plan",
        "description": "Read an official semester-by-semester OBS curriculum; latest plan is selected by default.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "faculty_id": {"type": "integer", "minimum": 1},
                "program_code": {"type": "string"},
                "plan_type": {"type": "string", "default": "lisans"},
                "plan_id": {"type": "integer", "minimum": 1},
                "latest": {"type": "boolean", "default": True},
            },
            "required": ["faculty_id", "program_code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "explain_course_eligibility",
        "title": "Explain Course Eligibility",
        "description": (
            "Explain whether a course's prerequisites are satisfied, using the official "
            "OBS branch prerequisite table (full Ve/Veya expression, per-course minimum "
            "grades, and credit requirement). Supports 3- and 4-digit codes including "
            "capstone courses like CEN 4901E. 'prerequisite_status' distinguishes "
            "'no_prerequisites' (proven absent from the official table) from 'unknown' "
            "(table unreadable) — never read an empty result as 'no prerequisites'. "
            "'cross_check' additionally diffs the result against an independent community "
            "dataset (third-party, not official); OBS stays authoritative and a disagreement "
            "is reported, never silently resolved either way. 'archive_seasonality', when "
            "the archive has the course, flags courses that only open in one term of the "
            "year (e.g. eligible now, but only ever offered in Güz) — check it even when "
            "eligibility alone looks fine."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_code": {
                    "type": "string",
                    "description": "Course code, e.g. 'BLG 223E' or 'CEN 4901E'.",
                },
                "completed_courses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                    "description": (
                        "Completed courses. Append a grade as 'CODE:GRADE' (e.g. "
                        "'CEN 4901E:BB') so minimum-grade rules can be checked; without "
                        "one, a course with a minimum-grade requirement cannot be proven."
                    ),
                },
                "use_obs_history": {
                    "type": "boolean",
                    "default": False,
                    "description": "Fill completed courses, grades, and credit total from OBS.",
                },
                "completed_credits": {"type": "number", "minimum": 0},
                "class_year": {"type": "integer", "minimum": 1},
                "program_type": {"type": "string", "default": "LS"},
            },
            "required": ["course_code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "plan_remaining_courses",
        "title": "Plan Remaining Courses",
        "description": (
            "Combine obs_get_graduation_remaining's remaining required courses with archive "
            "seasonality and who_taught history for each, producing a one-line scheduling "
            "recommendation per course (e.g. 'only offered in Güz, usually taught by X, "
            "average fill 0.95 — plan for Güz'). Replaces manually calling "
            "archive_course_history once per remaining course. The result keeps three things "
            "apart: in_progress (required courses the student is registered in this term, with "
            "CRN; do not present them as untaken), plans and unresolved_courses (still to plan), "
            "and open_elective_slots with their credits."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "program_id": {"type": "integer", "minimum": 1},
                "limit_terms": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                    "default": 6,
                    "description": "How many recent terms of instructor history to consider per course.",
                },
            },
            "additionalProperties": False,
        },
    },
]
