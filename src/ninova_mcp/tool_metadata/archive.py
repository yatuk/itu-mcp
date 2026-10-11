"""Tool metadata: course archive history."""

from __future__ import annotations

from typing import Any

# Same wording as ``archive_tools.TERM_ARGUMENT_HELP``; kept here so this
# metadata module imports nothing from the tool modules.
_TERM = (
    "Term: archive slug '2025-2026-guz', OBS term id 202610 (string or integer), or label "
    "'2025-2026 Güz Dönemi' / '2025-2026 Fall'."
)

# Said once per history tool instead of in a note on every result.
_CAPTURE = (
    "Placeholder counts: a term in enrolment_not_captured_terms has 0 enrolled in every "
    "section of the whole term (dump taken before registration opened), so its enrolled "
    "and fill_ratio are null: unknown, not zero. A term in capacity_not_captured_terms "
    "also has 0 capacity everywhere: its capacity is null too. Such terms are left out of "
    "every average and carry a one-line data_quality. A single 0 in a captured term is "
    "real. capture_basis is 'term' when the decision rests on term-wide totals, 'branch' "
    "when the totals of the terms in capture_basis_branch_terms could not be read and "
    "only the course's branch file decided. "
)

ARCHIVE_HISTORY_TOOLS: list[dict[str, Any]] = [
    {
        "name": "archive_list_terms",
        "title": "Archive: List Terms",
        "description": (
            "List every term held in the İTÜ ders arşivi (2016-2017 onward), including "
            "terms OBS no longer publishes and terms not yet active. Every archive tool "
            "that takes a term accepts the slug ('2025-2026-guz'), the OBS term id (202610) "
            "or the label printed here ('2025-2026 Güz Dönemi'). The archive exists because "
            "OBS publishes only the active term: the active term comes from OBS, past terms "
            "from historical dumps. Section counts of two terms "
            "are not comparable when 'coverage_may_differ' names one of them: the live term "
            "holds graduate sections that older dumps may lack. A term carries "
            "'enrolment_not_captured' / 'capacity_not_captured' only when the index itself "
            "holds that term's totals (today the live term); for another term call a term "
            "tool such as archive_term_sections, which reports both flags."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "archive_course_history",
        "title": "Archive: Course History",
        "description": (
            "Term-by-term offering history for a course from the archive: which terms it "
            "ran, how many sections, which instructors, and which season it opens in. "
            "Answers 'is this course ever offered in the spring?', which OBS cannot. "
            "Read seasonality.pattern before claiming a season: insufficient_data (fewer than 3 "
            "term records) means the archive cannot tell, not that the course runs in one term; "
            "summer school is counted apart from Güz and Bahar. " + _CAPTURE +
            "A course whose department code changed keeps its earlier terms under the old code "
            "(CEN 411E ran as BLG 411E): same_course_other_codes lists such codes, never merged; "
            "call again with that code for the earlier history."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_code": {
                    "type": "string",
                    "description": "Course code, e.g. 'BLG 102E' or 'CEN 4901E'.",
                },
                "limit_terms": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                    "default": 10,
                    "description": "How many recent terms to include.",
                },
            },
            "required": ["course_code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "archive_who_taught",
        "title": "Archive: Who Taught",
        "description": (
            "List every instructor who has taught a course across archived terms, ranked "
            "by how many terms they taught it, with their most recent term and average "
            "section fill rate. Sections with no announced instructor are counted in "
            "unannounced_section_count, never listed as a person. "
            "fill_ratio_section_count says how many sections an average rests on; "
            "average_fill_ratio is null when it rests on none. " + _CAPTURE +
            "A course whose department code changed keeps its earlier terms under the old code "
            "(CEN 411E ran as BLG 411E): same_course_other_codes lists such codes, never merged; "
            "call again with that code for the earlier history."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_code": {"type": "string", "description": "Course code, e.g. 'BLG 102E'."},
                "limit_terms": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                    "description": "Only consider this many recent terms (default: all).",
                },
            },
            "required": ["course_code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "archive_instructor_courses",
        "title": "Archive: Instructor Courses",
        "description": (
            "List the courses one instructor has taught across archived terms, newest "
            "first. Returns candidate matches when the name is ambiguous."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "instructor": {"type": "string", "description": "Instructor name or fragment."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 40},
            },
            "required": ["instructor"],
            "additionalProperties": False,
        },
    },
    {
        "name": "archive_term_sections",
        "title": "Archive: Term Sections",
        "description": (
            "List archived sections for a term, filtered by branch, course code, or "
            "instructor. Critically, the archive holds terms OBS has not published yet, "
            "so upcoming-term offerings can be checked instead of guessed. A null instructor "
            "means none was announced. enrolment_not_captured means no section of the whole "
            "term has an enrolled student in the dump (taken before registration opened): "
            "enrolled and fill_ratio are null, unknown and not zero. capacity_not_captured "
            "means no section of the term has a capacity either: capacity is null too. "
            "data_quality names what is missing in one line. A single 0 in a captured term "
            "is a real 0."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {"type": ["string", "integer"], "description": _TERM},
                "branch": {
                    "type": "string",
                    "description": (
                        "Branch code, e.g. 'BLG'; several separated by commas ('BLG, MAT', at most "
                        "10). Codes the term's dump does not hold are all named in unknown_branches."
                    ),
                },
                "course_code": {"type": "string", "description": "Course code; implies its branch."},
                "instructor": {"type": "string", "description": "Instructor name fragment."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 40},
            },
            "required": ["term"],
            "additionalProperties": False,
        },
    },
    {
        "name": "archive_fill_rate",
        "title": "Archive: Fill Rate",
        "description": (
            "How full a section got. With a CRN, reads that term's quota time series. "
            "With a course code, reports capacity vs enrolment across every past section "
            "so you can judge whether the course reliably fills up; add a term to narrow "
            "the course answer to that one term (term_filter says which). average_fill_ratio, "
            "max_fill_ratio and always_fills are null when no section has a usable ratio. "
            + _CAPTURE +
            "The CRN note states how old the last snapshot is and whether the archive "
            "is scanning now (scan_active); the numbers are never live. "
            "A course whose department code changed keeps its earlier terms under the old code "
            "(CEN 411E ran as BLG 411E): same_course_other_codes lists such codes, never merged; "
            "call again with that code for the earlier history."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "crn": {"type": ["string", "integer"], "description": "Section CRN."},
                "course_code": {"type": "string", "description": "Course code, for historical fill rates."},
                "term": {
                    "type": ["string", "integer"],
                    "description": (
                        "With a CRN: the term to read (default: archive's current term). With a "
                        "course code: only this term (default: every term). " + _TERM
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "archive_search_courses",
        "title": "Archive: Search Courses",
        "description": (
            "Search the archive's full course index by code or name fragment, across every "
            "term it has ever seen — not just the active one. Use this to find the exact "
            "code archive_course_history needs when only a name fragment is known. "
            "Matching ignores case, accents and spacing ('cen411e', 'yazilim'). The index holds "
            "one name per course, in its language of instruction: a course taught in English "
            "(code ending in E) is found by its English name or its code, not by a Turkish "
            "name. An empty result carries a hint saying so."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Course code or name fragment."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "archive_list_branches",
        "title": "Archive: List Branches",
        "description": (
            "List every branch present in one term's archive dump, with section and course "
            "counts and the study levels the dump holds ('levels', e.g. LS undergraduate, "
            "LU graduate). Answers 'does this branch even have a döküm for this term?' "
            "directly, instead of probing archive_term_sections branch by branch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {"type": ["string", "integer"], "description": _TERM},
            },
            "required": ["term"],
            "additionalProperties": False,
        },
    },
    {
        "name": "archive_compare_terms",
        "title": "Archive: Compare Terms",
        "description": (
            "Diff one course's sections between two archived terms: instructor turnover, "
            "section-count delta, and capacity/fill movement, named explicitly rather than "
            "left for two separate archive_term_sections calls to be compared by eye. "
            "Read four flags before quoting a number: 'comparable'; 'coverage_differs' "
            "(the two dumps hold different study levels, listed under 'levels'; only the "
            "shared levels are compared); 'enrolment_comparison' (a side marked "
            "enrolment_not_captured has 0 enrolled in every section of the whole term: no "
            "enrolled count or fill ratio, and not a term with zero students); and "
            "'capacity_comparison' (a side marked capacity_not_captured has 0 capacity in "
            "every section of the whole term: total_capacity and capacity_delta are null). "
            "Each block gives 'comparable', 'not_captured_terms' and a 'reason'; a side's "
            "'data_quality' names what is missing in one line. Section counts and "
            "instructors are compared either way."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_code": {"type": "string", "description": "Course code, e.g. 'BLG 322E'."},
                "term_a": {"type": ["string", "integer"], "description": "First term. " + _TERM},
                "term_b": {"type": ["string", "integer"], "description": "Second term. " + _TERM},
            },
            "required": ["course_code", "term_a", "term_b"],
            "additionalProperties": False,
        },
    },
]
