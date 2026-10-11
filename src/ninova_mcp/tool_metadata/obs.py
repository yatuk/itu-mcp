"""Tool metadata: OBS student record."""

from __future__ import annotations

from typing import Any

OBS_TOOLS: list[dict[str, Any]] = [
    {
        "name": "obs_get_profile",
        "title": "OBS Profile",
        "description": "Read OBS personal/program profile. Sensitive fields are redacted unless include_sensitive=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_sensitive": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include TCKN and similar sensitive fields.",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_list_programs",
        "title": "OBS Programs",
        "description": "List the student's OBS academic programs (e.g. undergraduate major).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "obs_list_semesters",
        "title": "OBS Semesters",
        "description": "List academic semesters available in OBS for the student.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "obs_get_grade_distribution",
        "title": "OBS Grade Distribution",
        "description": (
            "Read public OBS historical letter-grade counts and percentages for a course, by term. "
            "year is the academic ending year: 2026 means 2025-2026; defaults to the current calendar year. "
            "Optionally select a term_code returned in available_terms. Reports AA, BA, BB and every "
            "other published grade, including plus grades. OBS may combine Turkish and English course "
            "codes; reported_course_codes states the actual aggregate scope. No login required."
        ),
        "inputSchema": {"type": "object", "properties": {
            "course_code": {"type": "string", "description": "Full course code, such as UZB 438E."},
            "year": {"type": "integer", "minimum": 1900, "maximum": 2100, "description": "Academic ending year; 2026 selects 2025-2026."},
            "term_code": {"type": "string", "pattern": "^[0-9]{6}$", "description": "Optional official term code from available_terms; determines year when year is omitted."},
        }, "required": ["course_code"], "additionalProperties": False},
    },
    {
        "name": "obs_get_advisor",
        "title": "OBS Advisor",
        "description": "Read academic advisor information from OBS.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "obs_get_internships",
        "title": "OBS Internships",
        "description": "Read internship records from OBS.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "obs_get_contacts",
        "title": "OBS Contacts",
        "description": "Read contact records from OBS (phones/emails redacted by default).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_sensitive": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_list_registered_courses",
        "title": "OBS Registered Courses",
        "description": "List courses registered in OBS for a semester (default: latest).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "semester": {
                    "type": "string",
                    "description": "Semester id, code (e.g. 202620), or name fragment (e.g. '2025-2026 Bahar').",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_course_grades",
        "title": "OBS Course Grades",
        "description": (
            "Read the official midterm and/or letter grades for an OBS class (by sinifId or "
            "course code) — the authoritative record. For grades as entered in the Ninova "
            "LMS by the instructor, which can differ before the official record is updated, "
            "use get_course_grades instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "class_id": {"type": "integer", "description": "OBS sinifId."},
                "semester": {"type": "string", "description": "Semester when resolving by course name/code."},
                "course": {
                    "type": "string",
                    "description": "Course code/title/CRN to resolve (e.g. 'EHB 222E').",
                },
                "include_midterms": {"type": "boolean", "default": True},
                "include_letter": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_attendance",
        "title": "OBS Attendance",
        "description": (
            "Read the official attendance record for an OBS class, with a computed "
            "absence-risk summary by default. For attendance as recorded in the Ninova LMS "
            "by the instructor, use get_course_attendance instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "class_id": {
                    "type": "integer",
                    "description": "OBS sinifId.",
                },
                "semester": {
                    "type": "string",
                    "description": "Semester when resolving by course name/code.",
                },
                "course": {
                    "type": "string",
                    "description": "Course code/title/CRN to resolve (e.g. 'EHB 222E').",
                },
                "include_summary": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include computed absence-risk summary alongside raw attendance data.",
                },
                "max_absence_ratio": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.30,
                    "description": "Assumed maximum allowed absence ratio for risk calculation (default 0.30 = 30%).",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_schedule",
        "title": "OBS Schedule",
        "description": "Read weekly class schedule and final exam calendar for a semester.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "semester": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_graduation_remaining",
        "title": "OBS Graduation Remaining",
        "description": (
            "Read 'mezuniyetime ne kaldı', academic status, and debts for a program. "
            "The 'summary' field maps each filled elective slot to the real course that "
            "satisfies it (e.g. BLG 422E → '7th Sems. Elect. Course I (MT)'), lists open "
            "slots and remaining required courses, and tallies credits. Read 'summary' "
            "first; the raw 'graduation' payload is the unreduced source."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "program_id": {
                    "type": "integer",
                    "description": "OBS ogrenciProgramId (default: first program).",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_download_transcript",
        "title": "OBS Transcript PDF",
        "description": "Download OBS transcript preview PDF to the local state downloads folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "english": {"type": "boolean", "default": False},
                "output_dir": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_personal_exam_calendar",
        "title": "Personal Final Exam Calendar",
        "description": "Read the signed-in student's official OBS final calendar and registered courses.",
        "inputSchema": {
            "type": "object",
            "properties": {"semester": {"type": "string"}, "course": {"type": "string"}},
            "additionalProperties": False,
        },
    },
]
