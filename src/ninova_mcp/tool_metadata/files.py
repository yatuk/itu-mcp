"""Tool metadata: Ninova pages, downloads, text extraction and snapshots."""

from __future__ import annotations

from typing import Any

FILES_TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_page",
        "title": "Read Ninova Page",
        "description": "Fetch any Ninova page and return a structured summary of text, headings, links, tables, and attachments.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute Ninova URL or a relative path like /Kampus1 or /Sinif/123.456.",
                },
                "include_text": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include a text excerpt from the page body.",
                },
                "link_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 200,
                    "description": "Maximum number of links to return.",
                },
                "compact": {
                    "type": "boolean",
                    "default": False,
                    "description": "Shrink large fields for smaller LLM context.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "crawl_course",
        "title": "Crawl Course",
        "description": "Inventory pages and downloadable resources inside a Ninova course tree. A link that is refused or fails is listed in skipped with its reason and the crawl continues.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_url": {
                    "type": "string",
                    "description": "Course root URL or path, for example /Sinif/36851.118733.",
                },
                "max_depth": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 5,
                    "default": 2,
                },
                "max_pages": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 25,
                },
                "include_downloads": {
                    "type": "boolean",
                    "default": True,
                },
            },
            "required": ["course_url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "download_resource",
        "title": "Download Resource",
        "description": (
            "Download a Ninova file or other authenticated resource to disk. The text of a saved "
            "PDF/DOCX/PPTX/TXT is also added to the local index that search_content searches."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute Ninova URL or relative path.",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Directory to save the file into. Must be inside the downloads directory (a relative name is a subdirectory of it) or a directory the operator listed in NINOVA_EXTRA_DOWNLOAD_DIRS.",
                },
                "filename": {
                    "type": "string",
                    "description": "Optional filename override.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_resource_text",
        "title": "Read Resource Text",
        "description": (
            "Extract plain text from a Ninova PDF/DOCX/PPTX/TXT (or a local path from "
            "download_resource) so the assistant can read lecture notes and assignments. "
            "The extracted text is also added to the local index that search_content searches."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Authenticated Ninova file URL (PDF, DOCX, TXT, …).",
                },
                "path": {
                    "type": "string",
                    "description": "Path of a previously downloaded file. Must be inside the downloads directory or a directory the operator listed in NINOVA_EXTRA_READ_DIRS.",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 1000,
                    "maximum": 200000,
                    "default": 50000,
                    "description": "Maximum characters of extracted text to return.",
                },
                "filename": {
                    "type": "string",
                    "description": "Optional filename hint for type detection when using url.",
                },
                "save_download": {
                    "type": "boolean",
                    "default": False,
                    "description": "Also save the downloaded bytes under the state downloads dir.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "snapshot_page",
        "title": "Snapshot Page",
        "description": "Save a structured snapshot of a Ninova page for later comparison.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute Ninova URL or relative path.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional label to help identify the snapshot.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "diff_snapshot",
        "title": "Diff Snapshot",
        "description": "Compare the current state of a Ninova page against a previously stored snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute Ninova URL or relative path.",
                },
                "snapshot_path": {
                    "type": "string",
                    "description": "Optional explicit snapshot file path. If omitted, the latest matching snapshot is used.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional label filter when selecting the latest snapshot.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
]
