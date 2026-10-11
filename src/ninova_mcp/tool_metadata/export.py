"""Tool metadata: table export (CSV)."""

from __future__ import annotations

from typing import Any

DATASETS = ("term_grades", "all_grades", "weekly_schedule", "deadlines")

# Parameter descriptions are shared with the method signature in
# ``app_export`` so the published schema and this entry say the same.
DATASET_DESCRIPTION = (
    "Which table to export. "
    "term_grades: the courses of one term (Ders Kodu, Ders Adı, Kredi, Harf Notu). "
    "all_grades: every graded attempt of every term (Dönem, Ders Kodu, Ders Adı, Kredi, Harf Notu, Durum), "
    "where Durum says whether the attempt counts in the GPA or was replaced by a retake. "
    "weekly_schedule: the weekly class meetings of one term "
    "(Gün, Başlangıç, Bitiş, Ders Kodu, Ders Adı, CRN, Bina, Derslik). "
    "deadlines: every dated Ninova assignment in the stored course snapshot "
    "(Ders Kodu, Ders, Ödev, Son Teslim (ISO), Son Teslim, Yüklendi)."
)
SEMESTER_DESCRIPTION = (
    "Term for term_grades and weekly_schedule: term code, OBS term id or name, "
    'e.g. "202610", 202610 or "2025-2026 Bahar". Default: the latest term. '
    "Ignored for all_grades and deadlines."
)
SAVE_DESCRIPTION = (
    "false (default): return the CSV text in 'csv'. true: write the file into the exports folder "
    "of the server's downloads directory and return its path instead of the text. "
    "An existing file is never replaced."
)

EXPORT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "export_table",
        "title": "Export Table (CSV)",
        "description": (
            "Export one of the student's own tables as a CSV that opens correctly in a Turkish Excel: "
            "UTF-8 with a byte order mark, ';' as the delimiter, decimal comma, Turkish column headers. "
            "Use it when the user wants their grades, weekly schedule or assignment deadlines as a "
            "spreadsheet or a file. Returns 'columns', 'row_count' and the text in 'csv' "
            "(or 'path' with save=true). A text of more than about 200 KB is cut at a row boundary with "
            "'truncated' and 'total_row_count'. Cells that a spreadsheet would run as a formula are "
            "prefixed with a single quote. term_grades also reports the term average; deadlines is read "
            "from the stored snapshot without a sync and reports 'stale' and 'snapshot_age_hours', so "
            "call sync_all_courses first when it is stale. Reads only; nothing is changed at the university."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset": {
                    "type": "string",
                    "enum": list(DATASETS),
                    "description": DATASET_DESCRIPTION,
                },
                "semester": {
                    "type": ["string", "integer"],
                    "description": SEMESTER_DESCRIPTION,
                },
                "save": {
                    "type": "boolean",
                    "default": False,
                    "description": SAVE_DESCRIPTION,
                },
            },
            "required": ["dataset"],
            "additionalProperties": False,
        },
    },
]
