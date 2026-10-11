"""Tool metadata: Ninova courses, file listings and assignment upload."""

from __future__ import annotations

from typing import Any

NINOVA_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_dashboard",
        "title": "Get Dashboard",
        "description": "Read the Ninova dashboard and summarize courses, recent announcements, assignments, and messages.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "compact": {
                    "type": "boolean",
                    "default": False,
                    "description": "Shrink large fields for smaller LLM context.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_courses",
        "title": "List Courses",
        "description": "List all discovered Ninova courses from the dashboard (TTL-cached).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "refresh": {
                    "type": "boolean",
                    "default": False,
                    "description": "Bypass the course list cache and re-fetch the dashboard.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_announcements",
        "title": "Get Course Announcements",
        "description": "Return announcements for a specific Ninova course.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                },
                "include_full_text": {
                    "type": "boolean",
                    "default": False,
                    "description": "Fetch each announcement detail page and include full body text.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
                "compact": {
                    "type": "boolean",
                    "default": False,
                    "description": "Shrink large fields for smaller LLM context.",
                },
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_class_files",
        "title": "Get Course Class Files",
        "description": "List files and folders under the Ninova 'Sınıf Dosyaları' section for a course.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                },
                "recursive": {"type": "boolean", "default": True},
                "max_depth": {"type": "integer", "minimum": 0, "maximum": 8, "default": 3},
                "compact": {
                    "type": "boolean",
                    "default": False,
                    "description": "Shrink large fields for smaller LLM context.",
                },
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_lesson_files",
        "title": "Get Course Lesson Files",
        "description": "List files and folders under the Ninova 'Ders Dosyaları' section for a course.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                },
                "recursive": {"type": "boolean", "default": True},
                "max_depth": {"type": "integer", "minimum": 0, "maximum": 8, "default": 3},
                "compact": {
                    "type": "boolean",
                    "default": False,
                    "description": "Shrink large fields for smaller LLM context.",
                },
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_assignments",
        "title": "Get Course Assignments",
        "description": (
            "Return a course's assignment list. By default also fetches each "
            "assignment detail/upload page; set include_details=false for a faster list-only read."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
                "include_details": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "When true, fetch each assignment detail and upload status page "
                        "(more accurate, more HTTP requests)."
                    ),
                },
                "compact": {
                    "type": "boolean",
                    "default": False,
                    "description": "Shrink large fields for smaller LLM context.",
                },
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_info",
        "title": "Get Course Info",
        "description": "Return structured information from a course's 'Sınıf Bilgileri' page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                }
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_sections",
        "title": "Get Course Sections",
        "description": "List the direct course routes exposed on the Ninova course home page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                }
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_grades",
        "title": "Get Course Grades",
        "description": (
            "Read the Ninova 'Notlar' page for a course — grades as the instructor entered "
            "them in the LMS, not the official transcript record. For the official letter/"
            "midterm grade, use obs_get_course_grades instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                }
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_message_board",
        "title": "Get Course Message Board",
        "description": "Read the Ninova 'Mesaj Panosu' page for a course.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                },
                "include_thread_details": {
                    "type": "boolean",
                    "default": False,
                    "description": "Fetch each topic page and include parsed posts.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_attendance",
        "title": "Get Course Attendance",
        "description": (
            "Read the Ninova 'Yoklama' page for a course — attendance as the instructor "
            "recorded it in the LMS, not the official OBS record. For official attendance "
            "with an absence-risk summary, use obs_get_attendance instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                }
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_remote_learning",
        "title": "Get Course Remote Learning",
        "description": "Read the Ninova 'Uzaktan Eğitim' page for a course.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                }
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_dashboard_announcements",
        "title": "Get Dashboard Announcements",
        "description": "Return the announcements listed under the Ninova dashboard's aggregated announcements page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_full_text": {
                    "type": "boolean",
                    "default": False,
                    "description": "Fetch full text for each announcement detail page.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_dashboard_assignments",
        "title": "Get Dashboard Assignments",
        "description": (
            "Return the assignments listed under the Ninova dashboard's aggregated "
            "assignments page. Set include_details=false for a faster list-only read."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                "include_details": {
                    "type": "boolean",
                    "default": True,
                    "description": "Fetch each assignment detail/upload page when true.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_assignment_upload_slots",
        "title": "Get Assignment Upload Slots",
        "description": (
            "List the file slots on an assignment's 'Ödevi Yükle / OdevGonder' page, "
            "including which slots are already filled and allowed extensions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code/title/URL (required with assignment if upload_url omitted).",
                },
                "assignment": {
                    "type": "string",
                    "description": "Assignment title or URL (required with course if upload_url omitted).",
                },
                "upload_url": {
                    "type": "string",
                    "description": "Direct OdevGonder (or Odev detail) URL.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_assignment",
        "title": "Submit Assignment File",
        "description": (
            "Upload a local file to one Ninova assignment slot via the official "
            "multipart form. Requires confirm=true to actually post; without it, "
            "returns a dry-run preview only. Ask the user before confirming."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the local file to upload.",
                },
                "course": {
                    "type": "string",
                    "description": "Course code/title/URL (with assignment) if upload_url omitted.",
                },
                "assignment": {
                    "type": "string",
                    "description": "Assignment title or URL (with course) if upload_url omitted.",
                },
                "upload_url": {
                    "type": "string",
                    "description": "Direct OdevGonder URL.",
                },
                "slot_index": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "1-based upload slot index from get_assignment_upload_slots.",
                },
                "slot_description": {
                    "type": "string",
                    "description": "Fuzzy match against the slot description (e.g. 'Internship Report').",
                },
                "confirm": {
                    "type": "boolean",
                    "default": False,
                    "description": "Must be true to perform the real upload.",
                },
                "allow_replace": {
                    "type": "boolean",
                    "default": False,
                    "description": "Allow overwriting a slot that already has a file.",
                },
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
    },
]
