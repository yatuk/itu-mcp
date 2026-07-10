from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .cache import TtlCache, parse_ttl_seconds
from .client import NinovaAuthError, NinovaClient, NinovaError
from .compact import maybe_compact
from .env import load_ninova_env
from .obs_client import ObsClient, ObsError, redact_obs_profile
from .parsing import (
    SnapshotReference,
    compare_snapshot_payloads,
    extension_allowed,
    extract_announcement_detail,
    extract_announcements_list,
    extract_attendance,
    extract_assignment_detail,
    extract_assignment_upload_form,
    extract_assignment_upload_status,
    extract_assignments_list,
    extract_course_sections,
    extract_gradebook,
    extract_course_info,
    extract_file_directory,
    extract_message_board,
    extract_message_thread_detail,
    extract_remote_learning,
    is_internal_ninova_url,
    make_snapshot_payload,
    match_upload_slot,
    ninova_datetime_iso,
    normalize_lookup_text,
    normalize_url,
    parse_html_page,
    pretty_json,
    sanitize_filename,
    slugify,
    summarize_dashboard,
)
from .text_extract import (
    DEFAULT_MAX_CHARS as TEXT_EXTRACT_DEFAULT_MAX_CHARS,
    extract_text_from_bytes,
    extract_text_from_path,
    guess_extension,
)
from .tracking import diff_course_snapshots, load_tracking_state, merge_updates, save_tracking_state, utc_now_iso

SERVER_NAME = "itu-mcp"
SERVER_VERSION = "0.2.0"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
DEFAULT_COURSE_CACHE_TTL_SECONDS = 60.0
COURSES_CACHE_KEY = "courses"

SERVER_INSTRUCTIONS = (
    "This connector reads the user's own İTÜ Ninova LMS and İTÜ OBS "
    "(obs.itu.edu.tr student portal). Ninova tools cover course materials, "
    "announcements, assignments, files, and LMS grades. OBS tools (obs_*) cover "
    "official registration status, registered courses, letter/midterm grades, "
    "advisor, internships, schedule, graduation remaining, and transcript preview.\n\n"
    "Whenever the user asks about their courses or school — assignments/homework "
    "(ödev), due dates or deadlines (teslim, son tarih), grades (not, ortalama), "
    "announcements (duyuru), lecture or class files (ders/sınıf dosyası), "
    "attendance (yoklama), message boards (mesaj panosu), or a specific course "
    "(ders) — call these tools to fetch the real answer live from Ninova instead "
    "of guessing. You DO have access; don't say otherwise. Questions are often in "
    "Turkish.\n\n"
    "Typical flow: call list_courses or get_dashboard to discover the courses, "
    "resolve the one the user means, then call the specific tool (e.g. "
    "get_course_assignments, get_course_grades, get_course_announcements). For "
    "'what's due / upcoming' use get_upcoming_deadlines; for a broad status use "
    "get_dashboard. To read a PDF/DOCX from Ninova, call read_resource_text with "
    "the file URL (or path from download_resource). Use compact=true on heavy "
    "tools when the full payload is not needed. To upload homework: call "
    "get_assignment_upload_slots, then submit_assignment with confirm=true and a "
    "local file path — never upload without the user's explicit confirmation.\n\n"
    "Requires NINOVA_USERNAME and NINOVA_PASSWORD (usually the İTÜ email like "
    "name@itu.edu.tr); if login fails, ask the user to check credentials."
)


class NinovaMcpApp:
    def __init__(self) -> None:
        load_ninova_env()
        self._client: NinovaClient | None = None
        self._obs: ObsClient | None = None
        state_root = os.getenv("NINOVA_STATE_DIR") or str(Path.home() / ".ninova_state")
        self.state_dir = Path(state_root)
        self.snapshot_dir = self.state_dir / "snapshots"
        self.tracking_state_path = self.state_dir / "tracking-state.json"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        course_ttl = parse_ttl_seconds(
            os.getenv("NINOVA_COURSE_CACHE_TTL_SECONDS"),
            DEFAULT_COURSE_CACHE_TTL_SECONDS,
        )
        self._course_cache: TtlCache[list[dict[str, Any]]] = TtlCache(course_ttl)
        self._course_cache_ttl_seconds = course_ttl

    @property
    def client(self) -> NinovaClient:
        if self._client is None:
            self._client = NinovaClient()
        return self._client

    @property
    def obs(self) -> ObsClient:
        if self._obs is None:
            self._obs = ObsClient(ninova_client=self.client)
        return self._obs

    def invalidate_caches(self) -> None:
        self._course_cache.clear()

    def _out(self, payload: dict[str, Any], compact: bool = False) -> dict[str, Any]:
        # Per-call compact=True wins; otherwise honor NINOVA_COMPACT_DEFAULT.
        from .compact import compact_default_enabled

        enabled = True if compact else compact_default_enabled()
        return maybe_compact(payload, compact=enabled)

    def auth_status(self) -> dict[str, Any]:
        credentials_present = bool(os.getenv("NINOVA_USERNAME") and os.getenv("NINOVA_PASSWORD"))
        status: dict[str, Any] = {
            "credentials_present": credentials_present,
            "state_dir": str(self.state_dir),
            "course_cache_ttl_seconds": self._course_cache_ttl_seconds,
        }
        if not credentials_present:
            status["authenticated"] = False
            status["message"] = "Set NINOVA_USERNAME and NINOVA_PASSWORD to enable login."
            return status

        try:
            session = self.client.ensure_logged_in(verify=True)
            status["authenticated"] = True
            status["session"] = session
        except NinovaAuthError as exc:
            status["authenticated"] = False
            status["message"] = str(exc)
            return status

        try:
            status["obs"] = self.obs.ensure_ready()
        except Exception as exc:  # pragma: no cover - optional subsystem
            status["obs"] = {"jwt_present": False, "error": str(exc)}
        return status

    def refresh_session(self) -> dict[str, Any]:
        self.invalidate_caches()
        session = self.client.login(force=True)
        return {
            "authenticated": True,
            "session": session,
        }

    def get_dashboard(self, compact: bool = False) -> dict[str, Any]:
        html, response = self.client.get_html("/Kampus1")
        page_data = parse_html_page(response.url, html, base_url=self.client.base_url)
        dashboard = summarize_dashboard(page_data, html=html, base_url=self.client.base_url)
        courses = dashboard.get("courses") or []
        if courses:
            self._course_cache.set(COURSES_CACHE_KEY, courses)
        elif not self._looks_like_authenticated_dashboard(page_data, html):
            dashboard["parse_warning"] = (
                "No courses found and the dashboard did not look like a logged-in "
                "Ninova page. Session may have expired or the HTML layout changed."
            )
        else:
            dashboard["parse_warning"] = (
                "Dashboard loaded but no course links matching /Sinif/<id>.<id> "
                "were found. The course list markup may have changed."
            )
        # Drop raw link dump by default noise; keep courses + recent tables.
        if "links" in dashboard and compact is not False:
            dashboard = {**dashboard, "link_count": len(dashboard.get("links") or [])}
            if compact:
                dashboard.pop("links", None)
        return self._out(dashboard, compact=compact)

    def list_courses(self, refresh: bool = False) -> dict[str, Any]:
        if not refresh:
            cached = self._course_cache.get(COURSES_CACHE_KEY)
            if cached is not None:
                return {
                    "count": len(cached),
                    "courses": cached,
                    "source": "cache",
                    "cache_ttl_seconds": self._course_cache_ttl_seconds,
                }

        dashboard = self.get_dashboard()
        courses = dashboard.get("courses") or []
        self._course_cache.set(COURSES_CACHE_KEY, courses)
        result: dict[str, Any] = {
            "count": len(courses),
            "courses": courses,
            "source": "live",
        }
        if dashboard.get("parse_warning"):
            result["parse_warning"] = dashboard["parse_warning"]
        return result

    def get_courses(self, refresh: bool = False) -> dict[str, Any]:
        return self.list_courses(refresh=refresh)

    @staticmethod
    def _looks_like_authenticated_dashboard(page_data: dict[str, Any], html: str) -> bool:
        text = normalize_lookup_text(page_data.get("text_excerpt") or page_data.get("text") or "")
        if "kampus" in text or "dersler" in text or "hos geldiniz" in text:
            return True
        lower_html = html.casefold()
        return "/sinif/" in lower_html or "kampus1" in lower_html

    def get_course_announcements(
        self,
        course: str,
        include_full_text: bool = False,
        limit: int = 50,
        compact: bool = False,
    ) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/Duyurular")
        announcements = extract_announcements_list(html, response.url, base_url=self.client.base_url)
        announcements = announcements[: max(1, min(limit, 200))]
        if include_full_text:
            announcements = [self._merge_announcement_detail(item) for item in announcements]
        result: dict[str, Any] = {
            "course": resolved,
            "count": len(announcements),
            "announcements": announcements,
        }
        if not announcements:
            warning = self._empty_list_warning(html, response.url, scope="announcements")
            if warning:
                result["parse_warning"] = warning
        return self._out(result, compact=compact)

    def get_dashboard_announcements(
        self,
        include_full_text: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        html, response = self.client.get_html("/Kampus?1/Duyurular")
        announcements = extract_announcements_list(html, response.url, base_url=self.client.base_url)
        announcements = announcements[: max(1, min(limit, 200))]
        if include_full_text:
            announcements = [self._merge_announcement_detail(item) for item in announcements]
        result: dict[str, Any] = {
            "count": len(announcements),
            "announcements": announcements,
        }
        if not announcements:
            warning = self._empty_list_warning(html, response.url, scope="announcements")
            if warning:
                result["parse_warning"] = warning
        return result

    def get_course_assignments(
        self,
        course: str,
        limit: int = 100,
        include_details: bool = True,
        compact: bool = False,
    ) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/Odevler")
        assignments = extract_assignments_list(html, response.url, base_url=self.client.base_url)
        assignments = assignments[: max(1, min(limit, 200))]
        if include_details:
            assignments = [self._merge_assignment_detail(item) for item in assignments]
        for item in assignments:
            item["submission_start_iso"] = ninova_datetime_iso(item.get("submission_start"))
            item["submission_end_iso"] = ninova_datetime_iso(item.get("submission_end"))
        result: dict[str, Any] = {
            "course": resolved,
            "count": len(assignments),
            "include_details": include_details,
            "assignments": assignments,
        }
        if not assignments:
            warning = self._empty_list_warning(html, response.url, scope="assignments")
            if warning:
                result["parse_warning"] = warning
        return self._out(result, compact=compact)

    def get_dashboard_assignments(
        self,
        limit: int = 20,
        include_details: bool = True,
    ) -> dict[str, Any]:
        html, response = self.client.get_html("/Kampus?1/Odevler")
        assignments = extract_assignments_list(html, response.url, base_url=self.client.base_url)
        assignments = assignments[: max(1, min(limit, 200))]
        if include_details:
            assignments = [self._merge_assignment_detail(item) for item in assignments]
        for item in assignments:
            item["submission_start_iso"] = ninova_datetime_iso(item.get("submission_start"))
            item["submission_end_iso"] = ninova_datetime_iso(item.get("submission_end"))
        result: dict[str, Any] = {
            "count": len(assignments),
            "include_details": include_details,
            "assignments": assignments,
        }
        if not assignments:
            warning = self._empty_list_warning(html, response.url, scope="assignments")
            if warning:
                result["parse_warning"] = warning
        return result

    def get_course_info(self, course: str) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/SinifBilgileri")
        payload = extract_course_info(html, response.url, base_url=self.client.base_url)
        payload["course"] = resolved
        return payload

    def get_course_sections(self, course: str) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"])
        sections = extract_course_sections(html, response.url, base_url=self.client.base_url)
        return {
            "course": resolved,
            "count": len(sections),
            "sections": sections,
        }

    def get_course_grades(self, course: str) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/Notlar")
        payload = extract_gradebook(html, response.url, base_url=self.client.base_url)
        payload["course"] = resolved
        return payload

    def get_course_message_board(
        self,
        course: str,
        include_thread_details: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/MesajPanosu")
        payload = extract_message_board(html, response.url, base_url=self.client.base_url)
        topics = payload["topics"][: max(1, min(limit, 200))]
        if include_thread_details:
            topics = [self._merge_message_thread_detail(item) for item in topics]
        return {
            "course": resolved,
            "count": len(topics),
            "topics": topics,
        }

    def get_course_attendance(self, course: str) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/Yoklama")
        payload = extract_attendance(html, response.url, base_url=self.client.base_url)
        payload["course"] = resolved
        return payload

    def get_course_remote_learning(self, course: str) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/UzaktanEgitim")
        payload = extract_remote_learning(html, response.url, base_url=self.client.base_url)
        payload["course"] = resolved
        return payload

    def get_course_overview(
        self,
        course: str,
        refresh: bool = False,
        file_max_depth: int = 3,
        include_assignment_details: bool = False,
        compact: bool = False,
    ) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        if not refresh:
            state = self._load_tracking_state_document()
            existing = state["courses"].get(resolved["url"])
            if existing:
                snapshot = existing["snapshot"]
                snapshot["source"] = "tracked_state"
                return self._out(snapshot, compact=compact)

        snapshot = self._collect_course_snapshot(
            resolved,
            include_files=True,
            file_max_depth=file_max_depth,
            include_assignment_details=include_assignment_details,
        )
        snapshot["source"] = "live"
        return self._out(snapshot, compact=compact)

    def sync_all_courses(
        self,
        include_files: bool = True,
        file_max_depth: int = 3,
        course_limit: int | None = None,
        include_assignment_details: bool = False,
    ) -> dict[str, Any]:
        courses = self.list_courses(refresh=True)["courses"]
        if course_limit is not None:
            courses = courses[: max(1, min(course_limit, len(courses)))]

        synced_at = utc_now_iso()
        state = self._load_tracking_state_document()
        baseline = not bool(state["courses"])
        updates: list[dict[str, Any]] = []
        course_results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        current_course_urls = {course["url"] for course in courses}

        for course in courses:
            try:
                snapshot = self._collect_course_snapshot(
                    course,
                    include_files=include_files,
                    file_max_depth=file_max_depth,
                    include_assignment_details=include_assignment_details,
                )
            except Exception as exc:
                errors.append({"course": course, "error": str(exc)})
                continue

            previous_entry = state["courses"].get(course["url"])
            previous_snapshot = previous_entry.get("snapshot") if previous_entry else None
            course_updates = diff_course_snapshots(
                course=course,
                previous_snapshot=None if baseline else previous_snapshot,
                current_snapshot=snapshot,
                detected_at=synced_at,
            )
            if previous_entry is None and not baseline:
                course_updates.insert(
                    0,
                    {
                        "id": slugify(f"{course['url']}-course-added-{synced_at}")[:32],
                        "detected_at": synced_at,
                        "course": course,
                        "entity_type": "course",
                        "action": "added",
                        "entity_id": course["url"],
                        "summary": f"course:added:{course.get('code') or course.get('title') or course['url']}",
                        "before": None,
                        "after": course,
                    },
                )

            updates.extend(course_updates)
            state["courses"][course["url"]] = {
                "course": course,
                "synced_at": synced_at,
                "snapshot": snapshot,
            }
            course_results.append(
                {
                    "course": course,
                    "update_count": len(course_updates),
                }
            )

        removed_course_urls = set(state["courses"]) - current_course_urls
        for removed_url in sorted(removed_course_urls):
            removed_entry = state["courses"].pop(removed_url)
            if baseline:
                continue
            updates.append(
                {
                    "id": slugify(f"{removed_url}-course-removed-{synced_at}")[:32],
                    "detected_at": synced_at,
                    "course": removed_entry["course"],
                    "entity_type": "course",
                    "action": "removed",
                    "entity_id": removed_url,
                    "summary": f"course:removed:{removed_entry['course'].get('code') or removed_entry['course'].get('title') or removed_url}",
                    "before": removed_entry["course"],
                    "after": None,
                }
            )

        state["last_sync_at"] = synced_at
        state["updates"] = merge_updates(state["updates"], updates)
        self._save_tracking_state_document(state)

        return {
            "synced_at": synced_at,
            "baseline_created": baseline,
            "course_count": len(course_results),
            "update_count": len(updates),
            "courses": course_results,
            "updates": updates[:100],
            "errors": errors,
            "tracking_state_path": str(self.tracking_state_path),
        }

    def get_updates(
        self,
        limit: int = 100,
        course: str | None = None,
        entity_type: str | None = None,
    ) -> dict[str, Any]:
        state = self._load_tracking_state_document()
        updates = list(state["updates"])

        if course is not None:
            resolved = self._resolve_course(course)
            updates = [item for item in updates if item["course"]["url"] == resolved["url"]]

        if entity_type is not None:
            target = normalize_lookup_text(entity_type)
            updates = [
                item
                for item in updates
                if normalize_lookup_text(item.get("entity_type")) == target
            ]

        updates = updates[: max(1, min(limit, 500))]
        return {
            "last_sync_at": state["last_sync_at"],
            "count": len(updates),
            "updates": updates,
        }

    def get_upcoming_deadlines(
        self,
        days: int = 14,
        refresh: bool = False,
    ) -> dict[str, Any]:
        if refresh or not self.tracking_state_path.exists():
            self.sync_all_courses()

        state = self._load_tracking_state_document()
        deadline_items: list[dict[str, Any]] = []
        now = datetime.now(tz=UTC)
        upper_bound = now.timestamp() + max(1, min(days, 180)) * 86400

        for course_entry in state["courses"].values():
            course = course_entry["course"]
            assignments = course_entry["snapshot"]["overview"]["assignments"]
            for assignment in assignments:
                submission_end_iso = assignment.get("submission_end_iso") or ninova_datetime_iso(
                    assignment.get("submission_end")
                )
                if submission_end_iso is None:
                    continue
                due_at = datetime.fromisoformat(submission_end_iso).astimezone(UTC)
                if due_at.timestamp() < now.timestamp() or due_at.timestamp() > upper_bound:
                    continue
                requested_file_count = assignment.get("requested_file_count") or 0
                uploaded_file_count = assignment.get("uploaded_file_count") or 0
                deadline_items.append(
                    {
                        "course": course,
                        "title": assignment.get("title"),
                        "url": assignment.get("url"),
                        "submission_end": assignment.get("submission_end"),
                        "submission_end_iso": submission_end_iso,
                        "requested_file_count": requested_file_count,
                        "uploaded_file_count": uploaded_file_count,
                        "is_fully_uploaded": bool(requested_file_count) and uploaded_file_count >= requested_file_count,
                    }
                )

        deadline_items.sort(key=lambda item: item["submission_end_iso"])
        return {
            "last_sync_at": state["last_sync_at"],
            "days": max(1, min(days, 180)),
            "count": len(deadline_items),
            "deadlines": deadline_items,
        }

    def get_course_class_files(
        self,
        course: str,
        recursive: bool = True,
        max_depth: int = 3,
        compact: bool = False,
    ) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        payload = self._walk_file_directory(
            resolved["url"] + "/SinifDosyalari",
            recursive=recursive,
            max_depth=max_depth,
        )
        payload["course"] = resolved
        payload["scope"] = "class_files"
        return self._out(payload, compact=compact)

    def get_course_lesson_files(
        self,
        course: str,
        recursive: bool = True,
        max_depth: int = 3,
        compact: bool = False,
    ) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        payload = self._walk_file_directory(
            resolved["url"] + "/DersDosyalari",
            recursive=recursive,
            max_depth=max_depth,
        )
        payload["course"] = resolved
        payload["scope"] = "lesson_files"
        return self._out(payload, compact=compact)

    def read_page(
        self,
        url: str,
        include_text: bool = True,
        link_limit: int = 200,
        compact: bool = False,
    ) -> dict[str, Any]:
        html, response = self.client.get_html(url)
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            return {
                "url": response.url,
                "content_type": content_type,
                "content_length": response.headers.get("Content-Length"),
                "message": (
                    "The requested resource is not HTML. "
                    "Use download_resource or read_resource_text."
                ),
            }

        page_data = parse_html_page(response.url, html, base_url=self.client.base_url)
        result = {
            "url": page_data["url"],
            "title": page_data["title"],
            "headings": page_data["headings"],
            "links": page_data["links"][: max(1, min(link_limit, 500))],
            "attachments": page_data["attachments"],
            "tables": page_data["tables"],
            "text_hash": page_data["text_hash"],
        }
        if include_text:
            result["text_excerpt"] = page_data["text_excerpt"]
        return self._out(result, compact=compact)

    def crawl_course(
        self,
        course_url: str,
        max_depth: int = 2,
        max_pages: int = 25,
        include_downloads: bool = True,
    ) -> dict[str, Any]:
        start_url = normalize_url(course_url, self.client.base_url)
        course_path = self._extract_course_root_path(start_url)
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(start_url, 0)]
        pages: list[dict[str, Any]] = []
        downloads: list[dict[str, Any]] = []
        seen_downloads: set[tuple[str, str]] = set()

        while queue and len(pages) < max_pages:
            current_url, depth = queue.pop(0)
            normalized = self._strip_fragment(current_url)
            if normalized in visited:
                continue
            visited.add(normalized)

            html, response = self.client.get_html(normalized)
            page = parse_html_page(response.url, html, base_url=self.client.base_url)
            page_summary = {
                "url": page["url"],
                "title": page["title"],
                "headings": page["headings"][:10],
                "link_count": len(page["links"]),
                "attachment_count": len(page["attachments"]),
            }
            pages.append(page_summary)

            if include_downloads:
                for attachment in page["attachments"]:
                    key = (attachment["url"], attachment["text"])
                    if key in seen_downloads:
                        continue
                    seen_downloads.add(key)
                    downloads.append(attachment)

            if depth >= max_depth:
                continue

            for link in page["links"]:
                if link["kind"] != "page":
                    continue
                if not is_internal_ninova_url(link["url"], self.client.base_url):
                    continue
                if not self._is_inside_course(link["url"], course_path):
                    continue
                candidate = self._strip_fragment(link["url"])
                if candidate not in visited:
                    queue.append((candidate, depth + 1))

        return {
            "course_url": start_url,
            "course_path": course_path,
            "pages_crawled": len(pages),
            "pages": pages,
            "downloads": downloads[:500],
        }

    # ------------------------------------------------------------------
    # OBS (obs.itu.edu.tr) tools
    # ------------------------------------------------------------------

    def obs_auth_status(self) -> dict[str, Any]:
        try:
            ready = self.obs.ensure_ready()
            return {"ok": True, **ready}
        except (NinovaAuthError, ObsError) as exc:
            return {"ok": False, "error": str(exc)}

    def obs_get_profile(self, include_sensitive: bool = False) -> dict[str, Any]:
        payload = self.obs.get_profile()
        if include_sensitive:
            return payload
        return redact_obs_profile(payload)

    def obs_list_programs(self) -> dict[str, Any]:
        return self.obs.list_programs()

    def obs_list_semesters(self) -> dict[str, Any]:
        return self.obs.list_semesters()

    def obs_get_registration_status(self) -> dict[str, Any]:
        return {
            "kayit_durumu": self.obs.get_registration_status(),
            "ders_kayit_durumu": self.obs.get_lesson_registration_status(),
        }

    def obs_get_advisor(self) -> dict[str, Any]:
        return self.obs.get_advisor()

    def obs_get_internships(self) -> dict[str, Any]:
        return self.obs.get_internships()

    def obs_get_contacts(self, include_sensitive: bool = False) -> dict[str, Any]:
        payload = self.obs.get_contacts()
        if include_sensitive:
            return payload
        return redact_obs_profile(payload)

    def obs_list_registered_courses(self, semester: str | None = None) -> dict[str, Any]:
        resolved = self.obs.resolve_semester(semester)
        payload = self.obs.list_registered_courses(resolved["akademikDonemId"])
        return {
            "semester": resolved,
            "count": len(payload.get("kayitSinifResultList") or []),
            **payload,
        }

    def obs_get_course_grades(
        self,
        class_id: int | None = None,
        semester: str | None = None,
        course: str | None = None,
        include_midterms: bool = True,
        include_letter: bool = True,
    ) -> dict[str, Any]:
        resolved_class = self._resolve_obs_class(
            class_id=class_id,
            semester=semester,
            course=course,
        )
        sid = resolved_class["sinifId"]
        result: dict[str, Any] = {"class": resolved_class, "errors": []}
        if include_letter:
            try:
                result["letter_grades"] = self.obs.get_letter_grades(sid)
            except ObsError as exc:
                result["letter_grades"] = None
                result["errors"].append({"scope": "letter_grades", "error": str(exc)})
        if include_midterms:
            try:
                result["midterm_grades"] = self.obs.get_midterm_grades(sid)
            except ObsError as exc:
                result["midterm_grades"] = None
                result["errors"].append({"scope": "midterm_grades", "error": str(exc)})
        return result

    def obs_get_attendance(
        self,
        class_id: int | None = None,
        semester: str | None = None,
        course: str | None = None,
    ) -> dict[str, Any]:
        resolved_class = self._resolve_obs_class(
            class_id=class_id,
            semester=semester,
            course=course,
        )
        try:
            attendance = self.obs.get_attendance(resolved_class["sinifId"])
            error = None
        except ObsError as exc:
            attendance = None
            error = str(exc)
        return {
            "class": resolved_class,
            "attendance": attendance,
            "error": error,
        }

    def obs_get_schedule(self, semester: str | None = None) -> dict[str, Any]:
        resolved = self.obs.resolve_semester(semester)
        return {
            "semester": resolved,
            "schedule": self.obs.get_schedule(resolved["akademikDonemId"]),
            "final_calendar": self.obs.get_final_calendar(resolved["akademikDonemId"]),
        }

    def obs_get_graduation_remaining(self, program_id: int | None = None) -> dict[str, Any]:
        pid = program_id if program_id is not None else self.obs.default_program_id()
        return {
            "program_id": pid,
            "graduation": self.obs.get_graduation_remaining(pid),
            "academic_status": self.obs.get_academic_status(pid),
            "debts": self.obs.get_debts(pid),
        }

    def obs_download_transcript(
        self,
        english: bool = False,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        target = Path(output_dir or (self.state_dir / "downloads" / "obs")).expanduser()
        return self.obs.save_transcript_pdf(target, english=english)

    def _resolve_obs_class(
        self,
        *,
        class_id: int | None,
        semester: str | None,
        course: str | None,
    ) -> dict[str, Any]:
        if class_id is not None:
            return {"sinifId": class_id, "source": "class_id"}
        if not course:
            raise ObsError("Provide class_id, or course (+ optional semester).")
        resolved_semester = self.obs.resolve_semester(semester)
        payload = self.obs.list_registered_courses(resolved_semester["akademikDonemId"])
        items = payload.get("kayitSinifResultList") or []
        target = normalize_lookup_text(course)
        matches = []
        for item in items:
            code = f"{item.get('bransKodu') or ''} {item.get('dersKodu') or ''}".strip()
            blob = normalize_lookup_text(
                " ".join(
                    str(item.get(key) or "")
                    for key in ("bransKodu", "dersKodu", "dersAdiTR", "dersAdiEN", "crn")
                )
            )
            if target == normalize_lookup_text(code) or target in blob:
                matches.append(item)
        if not matches:
            options = ", ".join(
                f"{i.get('bransKodu')} {i.get('dersKodu')} ({i.get('dersAdiTR')})"
                for i in items[:12]
            )
            raise ObsError(f"OBS course not found: {course}. Available: {options}")
        if len(matches) > 1:
            options = ", ".join(
                f"sinifId={i.get('sinifId')} {i.get('bransKodu')} {i.get('dersKodu')}"
                for i in matches[:8]
            )
            raise ObsError(f"Ambiguous OBS course {course!r}. Matches: {options}")
        chosen = matches[0]
        chosen = {**chosen, "source": "course_lookup", "semester": resolved_semester}
        return chosen

    def download_resource(
        self,
        url: str,
        output_dir: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        response = self.client.get(url, stream=True)
        target_dir = Path(output_dir or (self.state_dir / "downloads")).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        resolved_name = sanitize_filename(filename) if filename else self._filename_from_response(response)
        target_path = self._unique_download_path(target_dir / resolved_name)
        with target_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    handle.write(chunk)

        return {
            "url": response.url,
            "content_type": response.headers.get("Content-Type"),
            "content_length": response.headers.get("Content-Length"),
            "path": str(target_path),
        }

    def get_assignment_upload_slots(
        self,
        course: str | None = None,
        assignment: str | None = None,
        upload_url: str | None = None,
    ) -> dict[str, Any]:
        """List file slots and current upload status for an assignment's OdevGonder page."""
        resolved_upload = self._resolve_assignment_upload_url(
            course=course,
            assignment=assignment,
            upload_url=upload_url,
        )
        html, response = self.client.get_html(resolved_upload["upload_url"])
        form = extract_assignment_upload_form(html, response.url, base_url=self.client.base_url)
        return {
            "course": resolved_upload.get("course"),
            "assignment": resolved_upload.get("assignment"),
            "upload_url": response.url,
            "title": form.get("title"),
            "submit_label": form.get("submit_label"),
            "requested_file_count": form.get("requested_file_count"),
            "uploaded_file_count": form.get("uploaded_file_count"),
            "slots": form.get("slots") or [],
            "ok": bool(form.get("ok")),
            "error": form.get("error"),
        }

    def submit_assignment(
        self,
        file_path: str,
        course: str | None = None,
        assignment: str | None = None,
        upload_url: str | None = None,
        slot_index: int | None = None,
        slot_description: str | None = None,
        confirm: bool = False,
        allow_replace: bool = False,
    ) -> dict[str, Any]:
        """Upload a local file into one assignment slot (ASP.NET multipart post).

        Safety: requires confirm=true. Without it, returns a dry-run preview only.
        Optional NINOVA_ALLOW_UPLOADS=0 disables uploads entirely.
        """
        if os.getenv("NINOVA_ALLOW_UPLOADS", "1").strip().lower() in {"0", "false", "no", "off"}:
            raise NinovaError(
                "Assignment uploads are disabled (NINOVA_ALLOW_UPLOADS=0)."
            )

        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise NinovaError(f"File not found: {path}")
        size = path.stat().st_size
        if size <= 0:
            raise NinovaError(f"File is empty: {path}")
        if size > MAX_UPLOAD_BYTES:
            raise NinovaError(
                f"File too large ({size} bytes). Max is {MAX_UPLOAD_BYTES} bytes."
            )

        resolved_upload = self._resolve_assignment_upload_url(
            course=course,
            assignment=assignment,
            upload_url=upload_url,
        )
        html, response = self.client.get_html(resolved_upload["upload_url"])
        form = extract_assignment_upload_form(html, response.url, base_url=self.client.base_url)
        if not form.get("ok"):
            raise NinovaError(form.get("error") or "Could not parse assignment upload form.")
        if not form.get("submit_event_target"):
            raise NinovaError(form.get("error") or "Submit control not found on upload page.")

        try:
            slot = match_upload_slot(
                form.get("slots") or [],
                slot_index=slot_index,
                slot_description=slot_description,
            )
        except ValueError as exc:
            raise NinovaError(str(exc)) from exc

        if not slot.get("field_name"):
            raise NinovaError(
                f"Slot {slot.get('index')} has no file input field "
                f"({slot.get('description')})."
            )

        if slot.get("uploaded") and not allow_replace:
            raise NinovaError(
                f"Slot {slot.get('index')} ({slot.get('description')}) already has an "
                "uploaded file. Pass allow_replace=true to overwrite, or pick another slot."
            )

        if not extension_allowed(path.name, slot.get("allowed_extensions") or []):
            raise NinovaError(
                f"File extension of {path.name!r} is not allowed for this slot. "
                f"Allowed: {slot.get('extensions') or 'any'}"
            )

        preview = {
            "dry_run": not confirm,
            "course": resolved_upload.get("course"),
            "assignment": resolved_upload.get("assignment"),
            "upload_url": response.url,
            "form_url": form.get("form_url"),
            "submit_label": form.get("submit_label"),
            "slot": {
                "index": slot.get("index"),
                "description": slot.get("description"),
                "extensions": slot.get("extensions"),
                "field_name": slot.get("field_name"),
                "already_uploaded": slot.get("uploaded"),
            },
            "file": {
                "path": str(path),
                "name": path.name,
                "size_bytes": size,
            },
        }

        if not confirm:
            preview["message"] = (
                "Dry run only. Re-call submit_assignment with the same arguments "
                "and confirm=true to actually upload this file to Ninova."
            )
            return preview

        file_bytes = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = dict(form.get("hidden_fields") or {})
        data["__EVENTTARGET"] = form["submit_event_target"]
        data["__EVENTARGUMENT"] = data.get("__EVENTARGUMENT", "")
        files = [
            (
                slot["field_name"],
                (path.name, file_bytes, content_type),
            )
        ]

        post_response = self.client.post_multipart(
            form.get("form_url") or response.url,
            data=data,
            files=files,
            referer=response.url,
        )
        result_html = self.client._decode_response(post_response)
        if self.client._looks_like_login_page(post_response, html=result_html):
            raise NinovaAuthError("Session expired during assignment upload.")

        after = extract_assignment_upload_form(
            result_html,
            post_response.url,
            base_url=self.client.base_url,
        )
        after_slots = after.get("slots") or []
        after_slot = next(
            (item for item in after_slots if item.get("index") == slot.get("index")),
            None,
        )
        success = bool(after_slot and after_slot.get("uploaded"))
        if not success:
            # Fallback: compare status text / page markers.
            body = normalize_lookup_text(parse_html_page(post_response.url, result_html, self.client.base_url).get("text_excerpt") or "")
            if any(token in body for token in ("yuklediniz", "gonderdiniz", "basari", "success")):
                success = True

        return {
            "dry_run": False,
            "ok": success,
            "message": (
                "Upload appears successful."
                if success
                else "Upload POST completed but the slot still looks empty; check Ninova in a browser."
            ),
            "course": resolved_upload.get("course"),
            "assignment": resolved_upload.get("assignment"),
            "upload_url": response.url,
            "response_url": post_response.url,
            "slot_before": preview["slot"],
            "slot_after": {
                "index": after_slot.get("index") if after_slot else slot.get("index"),
                "description": (after_slot or slot).get("description"),
                "uploaded": after_slot.get("uploaded") if after_slot else None,
                "file_name": after_slot.get("file_name") if after_slot else None,
                "status_text": after_slot.get("status_text") if after_slot else None,
            },
            "file": preview["file"],
            "requested_file_count": after.get("requested_file_count"),
            "uploaded_file_count": after.get("uploaded_file_count"),
        }

    def read_resource_text(
        self,
        url: str | None = None,
        path: str | None = None,
        max_chars: int = TEXT_EXTRACT_DEFAULT_MAX_CHARS,
        filename: str | None = None,
        save_download: bool = False,
    ) -> dict[str, Any]:
        """Download (or open) a Ninova file and return extracted plain text for the LLM.

        Provide either ``url`` (authenticated Ninova resource) or a local ``path``
        previously saved via ``download_resource``.
        """
        if not url and not path:
            raise NinovaError("Provide either url or path.")
        if url and path:
            raise NinovaError("Provide only one of url or path, not both.")

        max_chars = max(1_000, min(int(max_chars), 200_000))

        if path:
            extracted = extract_text_from_path(path, max_chars=max_chars)
            extracted["source"] = "path"
            return extracted

        assert url is not None
        response = self.client.get(url, stream=True)
        content_type = response.headers.get("Content-Type")
        resolved_name = sanitize_filename(filename) if filename else self._filename_from_response(response)
        extension = guess_extension(
            url=response.url,
            content_type=content_type,
            filename=resolved_name,
        )

        chunks: list[bytes] = []
        total = 0
        max_bytes = 25 * 1024 * 1024  # 25 MiB safety cap for in-memory extract
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise NinovaError(
                    f"Resource larger than {max_bytes} bytes; download with "
                    "download_resource and pass path= to read_resource_text."
                )
            chunks.append(chunk)
        data = b"".join(chunks)

        saved_path: str | None = None
        if save_download:
            target_dir = (self.state_dir / "downloads").expanduser().resolve()
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = self._unique_download_path(target_dir / resolved_name)
            target_path.write_bytes(data)
            saved_path = str(target_path)

        extracted = extract_text_from_bytes(data, extension=extension, max_chars=max_chars)
        extracted.update(
            {
                "source": "url",
                "url": response.url,
                "content_type": content_type,
                "filename": resolved_name,
                "size_bytes": len(data),
            }
        )
        if saved_path:
            extracted["path"] = saved_path
        return extracted

    def snapshot_page(self, url: str, label: str | None = None) -> dict[str, Any]:
        html, response = self.client.get_html(url)
        page_data = parse_html_page(response.url, html, base_url=self.client.base_url)
        payload = make_snapshot_payload(page_data, label=label)
        snapshot_path = self._snapshot_path(page_data["url"], label=label)
        snapshot_path.write_text(
            json.dumps(
                {
                    "captured_at": datetime.now(tz=UTC).isoformat(),
                    "snapshot": payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "snapshot_path": str(snapshot_path),
            "url": page_data["url"],
            "title": page_data["title"],
            "text_hash": page_data["text_hash"],
        }

    def diff_snapshot(
        self,
        url: str,
        snapshot_path: str | None = None,
        label: str | None = None,
    ) -> dict[str, Any]:
        html, response = self.client.get_html(url)
        page_data = parse_html_page(response.url, html, base_url=self.client.base_url)
        current = make_snapshot_payload(page_data, label=label)
        previous = self._load_snapshot(url=response.url, snapshot_path=snapshot_path, label=label)
        comparison = compare_snapshot_payloads(previous.payload, current)
        comparison["snapshot_path"] = str(previous.path)
        return comparison

    def _load_snapshot(
        self,
        *,
        url: str,
        snapshot_path: str | None,
        label: str | None,
    ) -> SnapshotReference:
        if snapshot_path:
            path = Path(snapshot_path).expanduser().resolve()
            payload = json.loads(path.read_text(encoding="utf-8"))["snapshot"]
            return SnapshotReference(path=path, payload=payload)

        candidates = sorted(self.snapshot_dir.glob("*.json"), reverse=True)
        normalized_url = normalize_url(url, self.client.base_url)
        for candidate in candidates:
            document = json.loads(candidate.read_text(encoding="utf-8"))
            snapshot = document["snapshot"]
            if snapshot.get("url") != normalized_url:
                continue
            if label is not None and snapshot.get("label") != label:
                continue
            return SnapshotReference(path=candidate, payload=snapshot)
        raise NinovaError("No matching snapshot was found.")

    def _snapshot_path(self, url: str, label: str | None) -> Path:
        parsed = urlparse(url)
        slug = slugify((label or parsed.path.strip("/") or "dashboard").replace("/", "-"))
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        return self.snapshot_dir / f"{slug}-{timestamp}.json"

    def _resolve_assignment_upload_url(
        self,
        *,
        course: str | None,
        assignment: str | None,
        upload_url: str | None,
    ) -> dict[str, Any]:
        if upload_url:
            normalized = normalize_url(upload_url, self.client.base_url)
            if "OdevGonder" not in normalized and "/Odev/" not in normalized:
                # Accept detail URLs; caller may pass Odev page — try append OdevGonder if needed later.
                pass
            if "/Odev/" in normalized and not normalized.rstrip("/").endswith("OdevGonder"):
                # Detail URL like .../Odev/123 -> .../Odev/123/OdevGonder is common.
                if "OdevGonder" not in normalized:
                    normalized = normalized.rstrip("/") + "/OdevGonder"
            return {
                "upload_url": normalized,
                "course": None,
                "assignment": {"url": normalized, "title": None},
            }

        if not course or not assignment:
            raise NinovaError(
                "Provide upload_url, or both course and assignment "
                "(assignment title / URL / partial name)."
            )

        resolved_course = self._resolve_course(course)
        payload = self.get_course_assignments(
            resolved_course.get("code") or resolved_course["url"],
            limit=200,
            include_details=True,
        )
        items = payload.get("assignments") or []
        target = normalize_lookup_text(assignment)
        matches: list[dict[str, Any]] = []
        for item in items:
            candidates = {
                normalize_lookup_text(item.get("title")),
                normalize_lookup_text(item.get("url")),
            }
            if target in candidates or any(target and target in value for value in candidates if value):
                matches.append(item)

        if not matches:
            titles = ", ".join(item.get("title") or "?" for item in items[:12])
            raise NinovaError(f"Assignment not found: {assignment}. Available: {titles}")
        if len(matches) > 1:
            # Prefer exact title match.
            exact = [item for item in matches if normalize_lookup_text(item.get("title")) == target]
            if len(exact) == 1:
                matches = exact
            else:
                options = ", ".join(item.get("title") or item.get("url") or "?" for item in matches[:8])
                raise NinovaError(f"Ambiguous assignment {assignment!r}. Matches: {options}")

        chosen = matches[0]
        resolved_upload = chosen.get("upload_url")
        if not resolved_upload and chosen.get("url"):
            resolved_upload = chosen["url"].rstrip("/") + "/OdevGonder"
        if not resolved_upload:
            raise NinovaError(
                f"Assignment {chosen.get('title')!r} has no upload URL "
                "(deadline may be closed or upload not enabled)."
            )
        return {
            "upload_url": resolved_upload,
            "course": resolved_course,
            "assignment": chosen,
        }

    def _resolve_course(self, course: str) -> dict[str, Any]:
        if not course.strip():
            raise NinovaError("course must be a non-empty string.")

        courses = self.list_courses()["courses"]

        if "/Sinif/" in course or course.startswith("/") or urlparse(course).scheme:
            normalized = normalize_url(course, self.client.base_url)
            root_path = self._extract_course_root_path(normalized)
            root_url = f"{self.client.base_url}{root_path}"
            for item in courses:
                if item["url"] == root_url:
                    return item
            return {"code": None, "title": None, "url": root_url, "context": course}

        target = normalize_lookup_text(course)
        exact_matches = [
            item
            for item in courses
            if target
            and target
            in {
                normalize_lookup_text(item.get("code")),
                normalize_lookup_text(item.get("title")),
            }
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            options = ", ".join(
                f"{item.get('code') or '?'} ({item.get('title') or item['url']})"
                for item in exact_matches[:8]
            )
            raise NinovaError(f"Ambiguous course reference: {course}. Matches: {options}")

        fuzzy_matches = [
            item
            for item in courses
            if target in normalize_lookup_text(item.get("code"))
            or target in normalize_lookup_text(item.get("title"))
            or target in normalize_lookup_text(item.get("context"))
        ]
        if not fuzzy_matches:
            available = ", ".join(
                f"{item.get('code') or item.get('title') or item['url']}" for item in courses[:12]
            )
            hint = f" Available courses: {available}." if available else " Course list is empty."
            raise NinovaError(f"Course not found: {course}.{hint}")
        if len(fuzzy_matches) > 1:
            options = ", ".join(
                f"{item.get('code') or '?'} ({item.get('title') or item['url']})"
                for item in fuzzy_matches[:8]
            )
            raise NinovaError(
                f"Course reference is ambiguous: {course}. Matches: {options}. "
                "Use an exact course code or course URL."
            )
        return fuzzy_matches[0]

    def _empty_list_warning(self, html: str, page_url: str, *, scope: str) -> str | None:
        """Return a diagnostic when a list page parsed to zero items but looks non-empty."""
        page = parse_html_page(page_url, html, base_url=self.client.base_url)
        text = normalize_lookup_text(page.get("text_excerpt") or page.get("text") or "")
        empty_markers = (
            "herhangi bir",
            "kayit bulunamadi",
            "kayıt bulunamadı",
            "bulunmamaktadir",
            "bulunmamaktadır",
            "no records",
            "no data",
        )
        if any(marker in text for marker in empty_markers):
            return None
        if len(text) < 80 and not page.get("tables") and not page.get("links"):
            return (
                f"Empty {scope} list and the page body looks nearly empty "
                f"(url={page_url}). Session or access rights may be wrong."
            )
        if page.get("tables") or len(text) > 200:
            return (
                f"Parsed zero {scope} from {page_url} even though the page has content "
                f"(title={page.get('title')!r}, tables={len(page.get('tables') or [])}). "
                "Ninova HTML layout may have changed."
            )
        return None

    def _merge_announcement_detail(self, announcement: dict[str, Any]) -> dict[str, Any]:
        html, response = self.client.get_html(announcement["url"])
        detail = extract_announcement_detail(html, response.url, base_url=self.client.base_url)
        merged = announcement.copy()
        merged.update(
            {
                "body_text": detail.get("body_text"),
                "published_at": announcement.get("published_at") or detail.get("published_at"),
            }
        )
        return merged

    def _merge_assignment_detail(self, assignment: dict[str, Any]) -> dict[str, Any]:
        html, response = self.client.get_html(assignment["url"])
        detail = extract_assignment_detail(html, response.url, base_url=self.client.base_url)
        upload_status: dict[str, Any] = {}
        upload_url = assignment.get("upload_url") or detail.get("upload_url")
        if upload_url:
            try:
                upload_html, upload_response = self.client.get_html(upload_url)
            except NinovaError:
                upload_status = {}
            else:
                upload_status = extract_assignment_upload_status(
                    upload_html,
                    upload_response.url,
                    base_url=self.client.base_url,
                )
        merged = assignment.copy()
        merged.update(
            {
                "description": detail.get("description"),
                "source_files": detail.get("source_files"),
                "required_files": detail.get("required_files"),
                "upload_url": upload_url,
                "submission_start": assignment.get("submission_start") or detail.get("submission_start"),
                "submission_end": assignment.get("submission_end") or detail.get("submission_end"),
                "requested_file_count": upload_status.get("requested_file_count", assignment.get("requested_file_count")),
                "uploaded_file_count": upload_status.get("uploaded_file_count", assignment.get("uploaded_file_count")),
                "upload_items": upload_status.get("upload_items"),
            }
        )
        return merged

    def _merge_message_thread_detail(self, topic: dict[str, Any]) -> dict[str, Any]:
        if not topic.get("url"):
            return topic
        html, response = self.client.get_html(topic["url"])
        detail = extract_message_thread_detail(html, response.url, base_url=self.client.base_url)
        merged = topic.copy()
        merged["thread"] = detail
        return merged

    def _load_tracking_state_document(self) -> dict[str, Any]:
        return load_tracking_state(self.tracking_state_path)

    def _save_tracking_state_document(self, state: dict[str, Any]) -> None:
        save_tracking_state(self.tracking_state_path, state)

    def _collect_course_snapshot(
        self,
        course: dict[str, Any],
        *,
        include_files: bool,
        file_max_depth: int,
        include_assignment_details: bool = False,
    ) -> dict[str, Any]:
        errors: list[dict[str, str]] = []

        course_html, course_response = self.client.get_html(course["url"])
        sections = extract_course_sections(course_html, course_response.url, base_url=self.client.base_url)

        info = self._safe_extract_course_payload(
            course["url"] + "/SinifBilgileri",
            extractor=extract_course_info,
            default={
                "url": course["url"] + "/SinifBilgileri",
                "title": None,
                "headings": [],
                "identity": {},
                "class_meta": {},
                "weekly_schedule": [],
                "course_details": {},
                "weekly_plan": [],
            },
            errors=errors,
            error_scope="info",
        )

        announcements = self._safe_extract_course_payload(
            course["url"] + "/Duyurular",
            extractor=lambda html, url, base_url: {
                "announcements": extract_announcements_list(html, url, base_url=base_url)
            },
            default={"announcements": []},
            errors=errors,
            error_scope="announcements",
        )["announcements"][:200]
        for item in announcements:
            item["published_at_iso"] = ninova_datetime_iso(item.get("published_at"))

        assignments = self._safe_extract_course_payload(
            course["url"] + "/Odevler",
            extractor=lambda html, url, base_url: {
                "assignments": extract_assignments_list(html, url, base_url=base_url)
            },
            default={"assignments": []},
            errors=errors,
            error_scope="assignments",
        )["assignments"][:200]
        if include_assignment_details:
            assignments = [self._merge_assignment_detail(item) for item in assignments]
        for item in assignments:
            item["submission_start_iso"] = ninova_datetime_iso(item.get("submission_start"))
            item["submission_end_iso"] = ninova_datetime_iso(item.get("submission_end"))

        if include_files:
            try:
                class_files = self._walk_file_directory(
                    course["url"] + "/SinifDosyalari",
                    recursive=True,
                    max_depth=file_max_depth,
                )["entries"]
            except Exception as exc:
                class_files = []
                errors.append({"scope": "class_files", "path": course["url"] + "/SinifDosyalari", "error": str(exc)})
            try:
                lesson_files = self._walk_file_directory(
                    course["url"] + "/DersDosyalari",
                    recursive=True,
                    max_depth=file_max_depth,
                )["entries"]
            except Exception as exc:
                lesson_files = []
                errors.append({"scope": "lesson_files", "path": course["url"] + "/DersDosyalari", "error": str(exc)})
        else:
            class_files = []
            lesson_files = []

        grades = self._safe_extract_course_payload(
            course["url"] + "/Notlar",
            extractor=extract_gradebook,
            default={"url": course["url"] + "/Notlar", "student_name": None, "weighted_average": None, "count": 0, "grades": []},
            errors=errors,
            error_scope="grades",
        )

        message_board = self._safe_extract_course_payload(
            course["url"] + "/MesajPanosu",
            extractor=extract_message_board,
            default={"url": course["url"] + "/MesajPanosu", "count": 0, "topics": []},
            errors=errors,
            error_scope="message_board",
        )

        attendance = self._safe_extract_course_payload(
            course["url"] + "/Yoklama",
            extractor=extract_attendance,
            default={
                "url": course["url"] + "/Yoklama",
                "student_name": None,
                "headers": [],
                "count": 0,
                "weeks": [],
                "total_present_marks": 0,
                "total_absent_marks": 0,
            },
            errors=errors,
            error_scope="attendance",
        )

        remote_learning = self._safe_extract_course_payload(
            course["url"] + "/UzaktanEgitim",
            extractor=extract_remote_learning,
            default={
                "url": course["url"] + "/UzaktanEgitim",
                "active_count": 0,
                "past_count": 0,
                "active_sessions": [],
                "past_sessions": [],
            },
            errors=errors,
            error_scope="remote_learning",
        )

        return {
            "course": course,
            "captured_at": utc_now_iso(),
            "overview": {
                "sections": sections,
                "info": info,
                "announcements": announcements,
                "assignments": assignments,
                "class_files": class_files,
                "lesson_files": lesson_files,
                "grades": grades,
                "message_board": message_board,
                "attendance": attendance,
                "remote_learning": remote_learning,
            },
            "errors": errors,
        }

    def _safe_extract_course_payload(
        self,
        path: str,
        *,
        extractor: Callable[[str, str, str], dict[str, Any]],
        default: dict[str, Any],
        errors: list[dict[str, str]],
        error_scope: str,
    ) -> dict[str, Any]:
        try:
            html, response = self.client.get_html(path)
            return extractor(html, response.url, self.client.base_url)
        except Exception as exc:
            errors.append({"scope": error_scope, "path": path, "error": str(exc)})
            return default

    def _walk_file_directory(
        self,
        root_url: str,
        *,
        recursive: bool,
        max_depth: int,
    ) -> dict[str, Any]:
        start_url = normalize_url(root_url, self.client.base_url)
        queue: list[tuple[str, int, str]] = [(start_url, 0, "/")]
        visited: set[str] = set()
        entries: list[dict[str, Any]] = []
        pages: list[dict[str, Any]] = []

        while queue:
            current_url, depth, current_path = queue.pop(0)
            normalized = self._strip_fragment(current_url)
            if normalized in visited:
                continue
            visited.add(normalized)

            html, response = self.client.get_html(current_url)
            listing = extract_file_directory(
                html,
                response.url,
                base_url=self.client.base_url,
                current_path=current_path,
            )
            pages.append(
                {
                    "url": response.url,
                    "current_path": current_path,
                    "entry_count": len(listing["entries"]),
                }
            )

            for entry in listing["entries"]:
                entries.append(entry)
                if recursive and entry["entry_type"] == "folder" and depth < max_depth:
                    queue.append((entry["url"], depth + 1, entry["path"]))

        return {
            "root_url": start_url,
            "recursive": recursive,
            "max_depth": max_depth,
            "pages_visited": len(pages),
            "pages": pages,
            "entry_count": len(entries),
            "entries": entries,
        }

    def _extract_course_root_path(self, url: str) -> str:
        parsed = urlparse(url)
        match = re.search(r"(/Sinif/\d+\.\d+)", parsed.path)
        if not match:
            raise NinovaError("course_url must point to a Ninova course path like /Sinif/<id>.<id>.")
        return match.group(1)

    def _is_inside_course(self, url: str, course_path: str) -> bool:
        path = urlparse(url).path
        return path == course_path or path.startswith(course_path + "/")

    def _strip_fragment(self, url: str) -> str:
        parsed = urlparse(url)
        suffix = f"?{parsed.query}" if parsed.query else ""
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}{suffix}"

    def _filename_from_response(self, response: Any) -> str:
        disposition = response.headers.get("Content-Disposition", "")
        filename_match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE)
        if filename_match:
            return sanitize_filename(filename_match.group(1))
        filename_match = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
        if filename_match:
            return sanitize_filename(filename_match.group(1))
        parsed = urlparse(response.url)
        basename = Path(parsed.path).name
        return sanitize_filename(basename or "download")

    def _unique_download_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem or "download"
        suffix = path.suffix
        counter = 2
        while True:
            candidate = path.with_name(f"{stem}-{counter}{suffix}")
            if not candidate.exists():
                return candidate
            counter += 1


TOOLS: list[dict[str, Any]] = [
    {
        "name": "auth_status",
        "title": "Authentication Status",
        "description": "Check whether Ninova credentials are configured and whether a fresh session can be created.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "refresh_session",
        "title": "Refresh Ninova Session",
        "description": "Force a new login with NINOVA_USERNAME and NINOVA_PASSWORD.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
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
        "name": "get_courses",
        "title": "Get Courses",
        "description": "Return all courses visible in the Ninova dashboard (alias of list_courses).",
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
        "description": "Read the Ninova 'Notlar' page for a course.",
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
        "description": "Read the Ninova 'Yoklama' page for a course.",
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
        "name": "get_course_overview",
        "title": "Get Course Overview",
        "description": "Return a combined view of a course's sections, announcements, assignments, files, grades, message board, attendance, and remote learning routes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                },
                "refresh": {
                    "type": "boolean",
                    "default": False,
                    "description": "Fetch live data instead of using the stored tracking state when available.",
                },
                "file_max_depth": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 8,
                    "default": 3,
                },
                "include_assignment_details": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "When true (and refresh is needed), fetch each assignment detail page. "
                        "Default false for a faster overview."
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
        "name": "sync_all_courses",
        "title": "Sync All Courses",
        "description": (
            "Fetch all visible courses, store a tracking snapshot, and return newly "
            "detected changes since the previous sync. Assignment detail pages are "
            "skipped by default for speed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_files": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include class and lesson file inventories in the sync.",
                },
                "file_max_depth": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 8,
                    "default": 3,
                },
                "course_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Optional limit for how many courses to sync.",
                },
                "include_assignment_details": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "When true, fetch each assignment detail/upload page during sync. "
                        "Much slower; list-page dates and counts are usually enough for deadlines."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_updates",
        "title": "Get Tracked Updates",
        "description": "Read the stored Ninova tracking history and return recent detected changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 100,
                },
                "course": {
                    "type": "string",
                    "description": "Optional course code, title, path, or full course URL filter.",
                },
                "entity_type": {
                    "type": "string",
                    "description": "Optional entity type filter such as assignments, announcements, grades, or message_topics.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_upcoming_deadlines",
        "title": "Get Upcoming Deadlines",
        "description": "Return assignments whose submission deadline is approaching based on the stored tracking snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 180,
                    "default": 14,
                },
                "refresh": {
                    "type": "boolean",
                    "default": False,
                    "description": "Run a sync before computing deadlines.",
                },
            },
            "additionalProperties": False,
        },
    },
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
        "description": "Inventory pages and downloadable resources inside a Ninova course tree.",
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
        "description": "Download a Ninova file or other authenticated resource to disk.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute Ninova URL or relative path.",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Directory to save the file into. Defaults to ./downloads.",
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
            "Extract plain text from a Ninova PDF/DOCX/TXT (or a local path from "
            "download_resource) so the assistant can read lecture notes and assignments."
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
                    "description": "Local filesystem path previously downloaded.",
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
    # --- OBS tools ---
    {
        "name": "obs_auth_status",
        "title": "OBS Auth Status",
        "description": "Check whether an OBS JWT can be obtained with the configured İTÜ credentials.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
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
        "name": "obs_get_registration_status",
        "title": "OBS Registration Status",
        "description": "Read OBS registration and course-registration status (active/class level).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
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
        "description": "Read midterm and/or letter grades for an OBS class (by sinifId or course code).",
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
        "description": "Read attendance for an OBS class.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "class_id": {"type": "integer"},
                "semester": {"type": "string"},
                "course": {"type": "string"},
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
        "description": "Read 'mezuniyetime ne kaldı', academic status, and debts for a program.",
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
]


LOCAL_TOOL_NAMES: list[str] = [tool["name"] for tool in TOOLS]
REMOTE_EXCLUDED_TOOLS = {
    "download_resource",
    "snapshot_page",
    "diff_snapshot",
    "submit_assignment",
    "get_assignment_upload_slots",
}
REMOTE_TOOL_NAMES: list[str] = [
    name for name in LOCAL_TOOL_NAMES if name not in REMOTE_EXCLUDED_TOOLS
]


def register_tools(mcp: Any, app: NinovaMcpApp, tool_names: list[str]) -> None:
    """Register Ninova tools on a FastMCP instance from the shared metadata.

    Both the local stdio server and the remote HTTP server go through this so
    the two transports always expose the same tool contract.
    """
    metadata = {tool["name"]: tool for tool in TOOLS}
    for name in tool_names:
        fn = getattr(app, name)
        meta = metadata.get(name, {})
        mcp.add_tool(
            fn,
            name=name,
            title=meta.get("title"),
            description=meta.get("description"),
            structured_output=True,
        )


def apply_server_version(mcp: Any, version: str = SERVER_VERSION) -> None:
    """Report our package version in the MCP ``serverInfo`` handshake.

    FastMCP does not forward a version, so the low-level server otherwise
    falls back to the SDK's own version. This tolerates SDK internals
    changing and simply leaves the default in place if it cannot.
    """
    server = getattr(mcp, "_mcp_server", None)
    if server is not None:
        try:
            server.version = version
        except Exception:  # pragma: no cover - defensive against SDK changes
            pass


def build_stdio_server(app: NinovaMcpApp | None = None) -> Any:
    """Build the FastMCP server for the local stdio transport.

    Uses the official MCP SDK so message framing is spec-compliant
    (newline-delimited JSON), which is what Claude Desktop, Claude Code,
    Cursor, and Codex actually speak.
    """
    from mcp.server.fastmcp import FastMCP

    app = app or NinovaMcpApp()
    mcp = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    apply_server_version(mcp)
    register_tools(mcp, app, LOCAL_TOOL_NAMES)
    return mcp


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="itu-mcp",
        description=(
            "MCP server for İTÜ Ninova (LMS) and OBS (student portal). "
            "With no arguments, starts the local stdio MCP server "
            "(used by Claude Desktop, Claude Code, Cursor, Codex, etc.)."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"{SERVER_NAME} {SERVER_VERSION}",
    )
    parser.add_argument(
        "--check-auth",
        action="store_true",
        help="Load credentials, attempt a Ninova login, print JSON status, and exit.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print registered local tool names and exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint. No args → stdio MCP server (MCP clients)."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.list_tools:
        for name in LOCAL_TOOL_NAMES:
            print(name)
        return

    if args.check_auth:
        status = NinovaMcpApp().auth_status()
        print(pretty_json(status))
        raise SystemExit(0 if status.get("authenticated") else 1)

    build_stdio_server().run()


if __name__ == "__main__":
    main()
