"""Tool metadata: public OBS catalog and schedules."""

from __future__ import annotations

from typing import Any

from .common import include_raw_property

PUBLIC_TOOLS: list[dict[str, Any]] = [
    {
        "name": "obs_search_courses",
        "title": "OBS Search Courses",
        "description": (
            "Look up exact course codes in public OBS; name or partial-code searches use the labeled "
            "course archive. An exact code returns one entry per course code with its own name and "
            "language; a Turkish/English pair such as BLG 335 and BLG 335E lists the other one in "
            "paired_codes. Results do not establish current-term offerings. No login required."
        ),
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
                    "description": "Maximum number of results to return. Values outside 1-50 are changed to the nearest bound and reported in limit_note.",
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
            "this calendar. An event carries only what is its own: an empty field is left out "
            "(no start_at means the source gives no time of day), and timezone, source_url and "
            "academic_year are on an event only when they differ from the result's own timezone, "
            "url and academic_year."
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
                "include_raw": include_raw_property("get_academic_calendar"),
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
            "(day/time/room), capacity, enrolled count, and prerequisite links. "
            "Days are Turkish names (Pazartesi ... Pazar) and times are HH:MM/HH:MM ranges. "
            "Cells the page leaves empty are null: 'unscheduled' marks a section with no day "
            "and time, 'location_announced_later' one whose building or room is not announced. "
            "'reservations' splits the capacity into programme quotas (PROGRAM/quota/enrolled, "
            "'Diğer' is the general pool); use find_open_course_sections to know which free "
            "seats a student can take. section_kind 'extra_exam' marks 'Ek Sınav' rows, which "
            "are not sections to register for."
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
                    "type": ["string", "integer"],
                    "description": "Optional CRN to filter a single course, as text or number (\"12513\" or 12513).",
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
                    "type": ["string", "integer"],
                    "description": "Course number, e.g. '223E'. A number without a letter may be sent as a number (4901).",
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
        "description": (
            "Find sections with seats a student can actually take, across selected public OBS "
            "department schedules. Part of a section's capacity can be reserved for named "
            "programmes, so 'available_seats' counts only the general pool; pass program_code to "
            "add your own programme's remaining quota. 'total_free_seats' is capacity minus "
            "enrolled and 'reserved_free_seats' lists free seats held for other programmes. "
            "Sections without a day and time (project and graduation work) are listed last with "
            "unscheduled=true; 'Ek Sınav' rows are left out. An empty result carries "
            "'no_result_reason': the query matched no section, the matching sections are full, "
            "or their free seats are reserved for other programmes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "department_codes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 25, "uniqueItems": True, "description": "Course branch codes to scan, e.g. ['BLG', 'MAT']."},
                "program_type": {"type": "string", "default": "LS", "description": "Program level: 'LS'/'Lisans', 'LU'/'Lisansüstü', 'ÖL'/'Önlisans', or 'LUİ'."},
                "min_available_seats": {"type": "integer", "minimum": 1, "default": 1, "description": "Smallest number of seats available to the student."},
                "query": {"type": "string", "description": "Course code, name or instructor fragment; case, accents and spaces in the code are ignored ('mat210e' finds MAT 210E)."},
                "program_code": {"type": "string", "description": "The student's programme code as OBS writes it in reservations, e.g. 'CEN_LS' or just 'CEN'. Sections the programme is not allowed to take are left out."},
            },
            "required": ["department_codes"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_empty_classrooms",
        "title": "Find Empty Classrooms",
        "description": (
            "Estimate empty rooms at a weekday/time from selected public department schedules. "
            "Only rooms that appear in the scanned schedules are known; sessions without an "
            "announced building or room are ignored."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "department_codes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 25, "uniqueItems": True, "description": "Course branch codes to scan, e.g. ['BLG', 'MAT']."},
                "day": {"type": "string", "description": "Weekday in Turkish or English, full or abbreviated, case and accents ignored: Pazartesi/Pzt/Monday/Mon, Salı/Sal/Tuesday/Tue, Çarşamba/Çar/Wednesday/Wed, Perşembe/Per/Thursday/Thu, Cuma/Cum/Friday/Fri, Cumartesi/Cmt/Saturday/Sat, Pazar/Paz/Sunday/Sun."},
                "time": {"type": ["string", "integer"], "description": "Time of day: '10:00', '9:45', '10.00', '0945', or the hour alone (10)."},
                "program_type": {"type": "string", "default": "LS", "description": "Program level: 'LS'/'Lisans', 'LU'/'Lisansüstü', 'ÖL'/'Önlisans', or 'LUİ'."},
                "building": {"type": "string", "description": "Optional building code fragment, e.g. 'BBB'."},
            },
            "required": ["department_codes", "day", "time"],
            "additionalProperties": False,
        },
    },
]
