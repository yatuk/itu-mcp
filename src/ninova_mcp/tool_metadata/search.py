"""Tool metadata: local search over course content that was already fetched."""

from __future__ import annotations

from typing import Any

# Parameter descriptions are shared with the method signature in
# ``app_search`` so the published schema and this entry say the same.
QUERY_DESCRIPTION = (
    'Words to look for, e.g. "vize tarihi" or "sınav". Turkish letters and capitals are folded '
    "(sinav finds SINAV and sınavda); every word must match, and when nothing does, any word may."
)
COURSE_DESCRIPTION = 'Optional course code to search in, e.g. "BLG 335E"; part of a course title also works.'
KINDS_DESCRIPTION = (
    "Optional kinds to search: announcement, assignment, message_topic, file_name (names of listed "
    'files), file_text (text of files that were read or downloaded), e.g. ["announcement", "file_text"].'
)
LIMIT_DESCRIPTION = "Results per call, 1 to 25."
OFFSET_DESCRIPTION = "Results to skip; pass next_offset of the previous call for the next page."

SEARCH_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_content",
        "title": "Search Fetched Course Content",
        "description": (
            "Search one local index of the Ninova content this server has already fetched, for questions "
            "like 'is the midterm date mentioned anywhere this term?'. Covers announcements, assignments, "
            "message board topics and file names stored by sync_all_courses, and the text of files opened "
            "with read_resource_text or download_resource (per PDF page). It never contacts Ninova and "
            "needs no login, so it only knows what was fetched before: a file that was never read is "
            "found by name only, and status=empty_index means run sync_all_courses first. No match is "
            "not proof that something is not mentioned. Results carry a snippet, the course, page, date "
            "and url; matched is 'all' or 'any' (fallback when not every word matched)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": QUERY_DESCRIPTION},
                "course": {"type": "string", "description": COURSE_DESCRIPTION},
                "kinds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["announcement", "assignment", "message_topic", "file_name", "file_text"],
                    },
                    "description": KINDS_DESCRIPTION,
                },
                "limit": {
                    "type": "integer", "minimum": 1, "maximum": 25, "default": 10,
                    "description": LIMIT_DESCRIPTION,
                },
                "offset": {"type": "integer", "minimum": 0, "default": 0, "description": OFFSET_DESCRIPTION},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]
