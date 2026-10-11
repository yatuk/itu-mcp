"""Tool metadata: GPA and degree planning."""

from __future__ import annotations

from typing import Any

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
        "description": "Estimate the future average required to reach a target cumulative GPA without reading a transcript.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "current_gpa": {"type": "number", "minimum": 0, "maximum": 4},
                "current_credits": {"type": "number", "minimum": 0},
                "target_gpa": {"type": "number", "minimum": 0, "maximum": 4},
                "future_credits": {"type": "number", "exclusiveMinimum": 0},
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
            "Scores must be finite numbers from 0 to 100; anything else is rejected with the "
            "offending value. No letter is estimated when fewer than 10 scores are counted or "
            "when all scores are equal: estimate_available is false, no_estimate_reason says "
            "why, and the mean and standard deviation are still returned. my_score is added to "
            "the list unless the same value is already in it (reported as my_score_included). "
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
            "Uses the public course schedule to look up session day/time/room."
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
                    "default": "BLG",
                    "description": "Department code (default: 'BLG').",
                },
                "department_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 25,
                    "description": "Optional multi-department scan; overrides department_code.",
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
            "archive_course_history once per remaining course."
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
