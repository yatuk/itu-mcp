"""Search tool ``search_content`` and the feeding of its local index.

The index (``search_index``) is filled as a side effect of tools that already
fetched the content: ``sync_all_courses`` and the file readers. Feeding never
makes a request and never fails the tool that triggered it.

This module does not use ``from __future__ import annotations``: the tool
signature is read when the tool is registered, and real ``Annotated`` types
are what give every parameter a JSON schema type and a description.
"""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from .client import NinovaError
from .search_index import KINDS, SEARCH_INDEX_FILENAME, SearchIndex
from .tool_metadata.search import (
    COURSE_DESCRIPTION,
    KINDS_DESCRIPTION,
    LIMIT_DESCRIPTION,
    OFFSET_DESCRIPTION,
    QUERY_DESCRIPTION,
)

SearchKind = Literal["announcement", "assignment", "message_topic", "file_name", "file_text"]

EMPTY_INDEX_MESSAGE = (
    "The search index is empty. Run sync_all_courses to index announcements, assignments, message "
    "board topics and file names; read_resource_text adds the text of the files it opens."
)


def _join(*parts: Any) -> str:
    return "\n".join(str(part).strip() for part in parts if isinstance(part, str) and part.strip())


def snapshot_documents(course: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Index documents for one stored course snapshot (no file contents, only what a sync saw)."""
    overview = snapshot.get("overview") or {}
    course_url = str(course.get("url") or "")
    base = {"course_url": course_url, "course_code": course.get("code"), "course_title": course.get("title")}
    documents: list[dict[str, Any]] = []

    def add(kind: str, index: int, item: dict[str, Any], title: Any, body: str, date: Any) -> None:
        entity = item.get("url") or item.get("path") or title or f"item-{index}"
        documents.append({
            **base, "kind": kind, "title": title, "body": body, "date": date, "url": item.get("url"),
            "source_key": f"sync|{course_url}|{kind}|{entity}",
        })

    for index, item in enumerate(overview.get("announcements") or []):
        add("announcement", index, item, item.get("title"), _join(item.get("summary"), item.get("author")),
            item.get("published_at_iso") or item.get("published_at"))
    for index, item in enumerate(overview.get("assignments") or []):
        due = item.get("submission_end")
        add("assignment", index, item, item.get("title"),
            _join(item.get("description"), f"Son teslim: {due}" if due else None),
            item.get("submission_end_iso") or due)
    for index, item in enumerate((overview.get("message_board") or {}).get("topics") or []):
        add("message_topic", index, item, item.get("title"),
            _join(item.get("summary"), item.get("last_message_text")),
            item.get("last_message_at_iso") or item.get("last_message_at"))
    for scope in ("class_files", "lesson_files"):
        for index, item in enumerate(overview.get(scope) or []):
            if item.get("entry_type") == "folder":
                continue
            add("file_name", index, item, item.get("name"), _join(item.get("path")), item.get("date"))
    return documents


class SearchToolsMixin:
    """Tool method mixed into ``NinovaMcpApp``, which supplies ``state_dir``."""

    state_dir: Path

    def _search_index(self) -> SearchIndex | None:
        state_dir = getattr(self, "state_dir", None)
        return SearchIndex(Path(state_dir) / SEARCH_INDEX_FILENAME) if state_dir else None

    def search_content(
        self,
        query: Annotated[str, Field(description=QUERY_DESCRIPTION)],
        course: Annotated[str | None, Field(description=COURSE_DESCRIPTION)] = None,
        kinds: Annotated[list[SearchKind] | None, Field(description=KINDS_DESCRIPTION)] = None,
        limit: Annotated[int, Field(ge=1, le=25, description=LIMIT_DESCRIPTION)] = 10,
        offset: Annotated[int, Field(ge=0, description=OFFSET_DESCRIPTION)] = 0,
    ) -> dict[str, Any]:
        """Search the local index of already fetched course content. No request is made."""
        unknown = [kind for kind in kinds or [] if kind not in KINDS]
        if unknown:
            raise NinovaError(f"Unknown kinds: {', '.join(map(str, unknown))}. Valid: {', '.join(KINDS)}.")
        index = self._search_index()
        if index is None:
            raise NinovaError("The state directory is not configured, so there is no search index.")
        result = index.search(query, course=course, kinds=list(kinds) if kinds else None, limit=limit, offset=offset)
        if result["status"] == "empty_index":
            result["message"] = EMPTY_INDEX_MESSAGE
        elif result["status"] == "empty_query":
            result["message"] = "The query has no letters or digits to search for."
        return result

    def _index_tracking_state(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Bring the index in line with the stored snapshots. Errors are reported, not raised."""
        try:
            index = self._search_index()
            if index is None:
                return None
            totals = {"indexed": 0, "unchanged": 0, "deleted": 0}
            courses = state.get("courses") or {}
            totals["deleted"] += index.prune_courses(courses.keys())
            for course_url, entry in courses.items():
                course = entry.get("course") or {"url": course_url}
                counts = index.replace_prefix(
                    f"sync|{course_url}|",
                    snapshot_documents({**course, "url": course_url}, entry.get("snapshot") or {}),
                )
                for key in totals:
                    totals[key] += counts.get(key, 0)
            return totals
        except Exception as exc:  # the index is a convenience; a sync must not fail over it
            return {"error": str(exc) or type(exc).__name__}

    def _index_file_text(
        self,
        pages: list[tuple[int | None, str]],
        *,
        title: str,
        url: str | None = None,
        path: str | None = None,
    ) -> None:
        """Index text a file tool has just extracted, one document per PDF page."""
        try:
            index = self._search_index()
            source = url or path
            if index is None or not source:
                return
            course: dict[str, Any] = {}
            if url:
                loader = getattr(self, "_load_tracking_state_document", None)
                tracked = (loader()["courses"] if loader else {}) or {}
                for course_url, entry in tracked.items():
                    if url == course_url or url.startswith(course_url + "/"):
                        course = {**(entry.get("course") or {}), "url": course_url}
                        break
            prefix = f"file|{source}|"
            index.replace_prefix(prefix, [
                {
                    "kind": "file_text", "title": title, "body": text, "page": page, "url": url,
                    "course_url": course.get("url"), "course_code": course.get("code"),
                    "course_title": course.get("title"), "source_key": f"{prefix}{page or 0}",
                }
                for page, text in pages if text and text.strip()
            ])
        # Feeding the index must never fail a file read.
        except Exception:  # nosec B110
            pass
