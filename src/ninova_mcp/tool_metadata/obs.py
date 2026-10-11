"""Tool metadata: OBS student record."""

from __future__ import annotations

from typing import Any

from .common import include_raw_property

OBS_TOOLS: list[dict[str, Any]] = [
    {
        "name": "obs_get_profile",
        "title": "OBS Profile",
        "description": "Read OBS personal/program profile. The student's name and student number are shown. National ID and passport numbers, e-mail addresses, phone numbers, address fields, birth date and place, and parents' names are redacted, and redacted_fields names the hidden fields. include_sensitive=true has an effect only when the operator set NINOVA_ALLOW_SENSITIVE_OUTPUT.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_sensitive": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include the redacted fields (ID numbers, e-mail, phone, address, birth data). Ignored unless the operator set NINOVA_ALLOW_SENSITIVE_OUTPUT.",
                },
                "include_raw": include_raw_property("obs_get_profile"),
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_list_programs",
        "title": "OBS Programs",
        "description": "List the student's OBS academic programs (e.g. undergraduate major).",
        "inputSchema": {
            "type": "object",
            "properties": {"include_raw": include_raw_property("obs_list_programs")},
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_list_semesters",
        "title": "OBS Semesters",
        "description": "List academic semesters available in OBS for the student.",
        "inputSchema": {
            "type": "object",
            "properties": {"include_raw": include_raw_property("obs_list_semesters")},
            "additionalProperties": False,
        },
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
        "inputSchema": {
            "type": "object",
            "properties": {"include_raw": include_raw_property("obs_get_advisor")},
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_internships",
        "title": "OBS Internships",
        "description": "Read internship records from OBS.",
        "inputSchema": {
            "type": "object",
            "properties": {"include_raw": include_raw_property("obs_get_internships")},
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_contacts",
        "title": "OBS Contacts",
        "description": "Read contact records from OBS. Phone numbers, e-mail addresses and postal addresses are redacted by default, and redacted_fields names the hidden rows. include_sensitive=true has an effect only when the operator set NINOVA_ALLOW_SENSITIVE_OUTPUT.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_sensitive": {"type": "boolean", "default": False},
                "include_raw": include_raw_property("obs_get_contacts"),
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_list_registered_courses",
        "title": "OBS Registered Courses",
        "description": (
            "List courses registered in OBS for a semester (default: latest). The term is stated once "
            "in semester; a row repeats a term field only when it names another term."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "semester": {
                    "type": "string",
                    "description": "Semester id, code (e.g. 202620), or name fragment (e.g. '2025-2026 Bahar').",
                },
                "include_raw": include_raw_property("obs_list_registered_courses"),
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
                "semester": {"type": ["string", "integer"], "description": "Term code (202610), OBS term id, or term name; used when resolving by course name/code."},
                "course": {
                    "type": ["string", "integer"],
                    "description": "Course code, CRN or name to resolve (e.g. 'EHB 222E', 'ehb222e', 'fizik 2'). Must fit exactly one registered course; the returned class carries matched_by.",
                },
                "include_midterms": {"type": "boolean", "default": True},
                "include_letter": {"type": "boolean", "default": True},
                "include_raw": include_raw_property("obs_get_course_grades"),
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_attendance",
        "title": "OBS Attendance",
        "description": (
            "Read the official attendance record for an OBS class, with a computed "
            "absence-risk summary by default. summary.status is ok, no_attendance_recorded "
            "(the instructor has not entered any attendance for the class yet; this is not zero "
            "absences) or unavailable. For attendance as recorded in the Ninova LMS "
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
                    "type": ["string", "integer"],
                    "description": "Course code, CRN or name to resolve (e.g. 'EHB 222E', 'ehb222e', 'fizik 2'). Must fit exactly one registered course; the returned class carries matched_by.",
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
                "include_raw": include_raw_property("obs_get_attendance"),
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_schedule",
        "title": "OBS Schedule",
        "description": (
            "Read the weekly class schedule and the final exam calendar for a semester. "
            "weekly_schedule has one entry per meeting, sorted by day and start time: day, start and "
            "end as HH:MM, course_code, title, crn, building, room and campus. final_calendar is the "
            "OBS final calendar. If OBS sends the schedule in a shape that cannot be read into "
            "meetings, weekly_schedule_status says so and the source rows are returned in schedule."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "semester": {"type": ["string", "integer"], "description": "Term code (202610), OBS term id, or term name (\"2025-2026 Güz\"). Default: latest term."},
                "include_raw": include_raw_property("obs_get_schedule"),
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
            "slots and remaining required courses, and tallies credits. In the summary, "
            "credit and slot_credit are the credit of the plan row, counted_credit is what OBS "
            "counted for the course taken against it, and credit_differs is true when the two "
            "differ. The raw 'graduation' payload the summary was built from is returned only "
            "with include_raw=true."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "program_id": {
                    "type": "integer",
                    "description": "OBS ogrenciProgramId (default: first program).",
                },
                "include_raw": include_raw_property("obs_get_graduation_remaining"),
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
        "description": (
            "Read the signed-in student's official OBS final calendar. matched_courses names the "
            "registered courses the calendar was read for (all of them, or the ones matching course) "
            "by code, CRN and title."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "semester": {"type": ["string", "integer"], "description": "Term code (202610), OBS term id, or term name (\"2025-2026 Güz\"). Default: latest term."},
                "course": {"type": ["string", "integer"]},
                "include_raw": include_raw_property("get_personal_exam_calendar"),
            },
            "additionalProperties": False,
        },
    },
]
