"""Tool metadata: public OBS catalog and schedules."""

from __future__ import annotations

from typing import Any

PUBLIC_TOOLS: list[dict[str, Any]] = [
    {
        "name": "obs_search_courses",
        "title": "OBS Search Courses",
        "description": "Look up exact course codes in public OBS; name or partial-code searches use the labeled course archive. Results do not establish current-term offerings. No login required.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Course code or name fragment (e.g. 'BBF 201E' or 'veri yapilari').",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 15,
                    "description": "Maximum number of results to return.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "obs_get_course_prerequisites",
        "title": "OBS Course Prerequisites",
        "description": (
            "Public, no login required. Query prerequisite and postrequisite relationships "
            "for a course from the OBS public catalog. Supports chain queries up to 10 "
            "levels deep."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_code": {
                    "type": "string",
                    "description": "Course code (e.g. 'BBF 201E', 'MAT 261E').",
                },
                "direction": {
                    "type": "string",
                    "enum": ["prerequisites", "postrequisites", "both"],
                    "default": "prerequisites",
                    "description": (
                        "'prerequisites' = what you must take before; "
                        "'postrequisites' = what this course unlocks; "
                        "'both' = both directions."
                    ),
                },
                "max_depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 1,
                    "description": "Chain depth. 1 = direct only; higher = recursive.",
                },
            },
            "required": ["course_code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_academic_calendar",
        "title": "Academic Calendar",
        "description": (
            "Read the official İTÜ academic calendar (public, no login): term start and end, "
            "course registration, add-drop and withdrawal periods, final and make-up exams, "
            "breaks. Covers the whole current academic year, not only this month; with "
            "date_from/date_to the academic years those dates fall in are read (at most 2 per "
            "call, and only years the site still lists). Each event has an English 'description' "
            "and a Turkish 'description_tr'; query matches either language, ignoring case and "
            "Turkish accents (e.g. 'dersten çekilme', 'ekle bırak', 'final'). 'terms' gives the "
            "first and last day of classes per term. With no argument only recent and upcoming "
            "events are listed (see 'default_window'). Check 'coverage' and 'coverage_warning' "
            "before concluding that nothing is scheduled. Official public holidays are not on "
            "this calendar."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Inclusive ISO date, YYYY-MM-DD."},
                "date_to": {"type": "string", "description": "Inclusive ISO date, YYYY-MM-DD."},
                "category": {
                    "type": "string",
                    "enum": ["exam", "registration", "holiday", "semester", "other"],
                    "description": (
                        "exam: exams and grade entry deadlines. registration: course registration, "
                        "add-drop, withdrawal. holiday: breaks. semester: first and last day of a term."
                    ),
                },
                "query": {"type": "string", "description": "Words to look for, Turkish or English."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_public_course_schedule",
        "title": "Public Course Schedule",
        "description": (
            "Read the public OBS course schedule for a department. "
            "No login required — reads the open DersProgram page. "
            "Use query for a course name/code fragment, including full sections. "
            "Filtered/paged calls return at most 50 sections by default (limit up to 100). "
            "Follow next_offset with the same filters to read more. "
            "Returns structured course list with CRN, instructor, sessions "
            "(day/time/room), capacity, enrolled count, and prerequisite links."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "program_type": {
                    "type": "string",
                    "description": (
                        "Program level: 'LS'/'Lisans', 'LU'/'Lisansüstü', "
                        "'ÖL'/'Önlisans', or 'LUİ'."
                    ),
                },
                "department_code": {
                    "type": "string",
                    "description": "Department code, e.g. 'BLG', 'BBF', 'EHB'.",
                },
                "crn": {
                    "type": "string",
                    "description": "Optional CRN to filter a single course.",
                },
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Optional course name/code fragment, case/accent-insensitive. Combined with crn when both are supplied.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Page size. Defaults to 50 for filtered/paged calls; omitted unfiltered calls return the full schedule.",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "Offset into matching sections. Use next_offset from the previous page.",
                },
            },
            "required": ["program_type", "department_code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_public_course_prerequisites",
        "title": "Public Course Prerequisites",
        "description": (
            "Read prerequisite details from a course's public OBS DersBilgi page. "
            "No login required. Returns structured prerequisite list with codes, "
            "names, groups, and types."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "brans_kodu": {
                    "type": "string",
                    "description": "Department code, e.g. 'BLG'.",
                },
                "ders_no": {
                    "type": "string",
                    "description": "Course number, e.g. '223E'.",
                },
            },
            "required": ["brans_kodu", "ders_no"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_public_exam_schedule",
        "title": "Public Final Exam Schedule",
        "description": "Read the current official OBS final-exam schedule for a department code (public, no login).",
        "inputSchema": {
            "type": "object",
            "properties": {"department_code": {"type": "string", "description": "Course branch code, e.g. BLG."}},
            "required": ["department_code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_open_course_sections",
        "title": "Find Open Course Sections",
        "description": "Find sections with available seats across selected public OBS department schedules.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "department_codes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 25, "uniqueItems": True},
                "program_type": {"type": "string", "default": "LS"},
                "min_available_seats": {"type": "integer", "minimum": 1, "default": 1},
                "query": {"type": "string"},
            },
            "required": ["department_codes"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_empty_classrooms",
        "title": "Find Empty Classrooms",
        "description": "Estimate empty rooms at a weekday/time from selected public department schedules.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "department_codes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 25, "uniqueItems": True},
                "day": {"type": "string"},
                "time": {"type": "string", "pattern": "^[0-2][0-9]:[0-5][0-9]$"},
                "program_type": {"type": "string", "default": "LS"},
                "building": {"type": "string"},
            },
            "required": ["department_codes", "day", "time"],
            "additionalProperties": False,
        },
    },
]
