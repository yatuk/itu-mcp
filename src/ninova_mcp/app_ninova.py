"""Ninova course tools: dashboard, course pages, file listings and assignment upload.

Also holds the course helpers the other Ninova mixins share: resolving a
course reference, walking a file directory and the course URL helpers.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .client import NinovaAuthError, NinovaClient, NinovaError
from .parsing import (
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
    ninova_datetime_iso,
    normalize_lookup_text,
    normalize_url,
    parse_html_page,
    summarize_dashboard,
)
from .tracking_coverage import coverage, enrollment_coverage, scope_coverage
from .app_base import COURSES_CACHE_KEY, server_namespace

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# The assignment upload page, as Ninova links it from an assignment and as its
# own form posts back to it: /Sinif/<course>.<class>/Odev/<assignment>/OdevGonder
UPLOAD_PATH_RE = re.compile(r"^/Sinif/(\d+\.\d+)/Odev/(\d+)/OdevGonder/?$")
ASSIGNMENT_DETAIL_PATH_RE = re.compile(r"^/Sinif/\d+\.\d+/Odev/\d+/?$")


def match_upload_slot(*args: Any, **kwargs: Any) -> Any:
    """Call ``parsing.match_upload_slot`` through ``ninova_mcp.server``.

    Tests patch ``ninova_mcp.server.match_upload_slot``. Forwarding keeps that
    patch effective without changing the body of ``submit_assignment``.
    """
    return server_namespace().match_upload_slot(*args, **kwargs)


class NinovaCourseToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``client`` and the course cache."""

    client: NinovaClient

    def get_dashboard(self, compact: bool = False) -> dict[str, Any]:
        dashboard = self._read_dashboard()
        # Internal enrollment reads always use the full payload, even when
        # NINOVA_COMPACT_DEFAULT truncates lists in a public tool response.
        if "links" in dashboard and compact is not False:
            dashboard = {**dashboard, "link_count": len(dashboard.get("links") or [])}
            if compact:
                dashboard.pop("links", None)
        return self._out(dashboard, compact=compact)

    def _read_dashboard(self) -> dict[str, Any]:
        html, response = self.client.get_html("/Kampus1")
        page_data = parse_html_page(response.url, html, base_url=self.client.base_url)
        dashboard = summarize_dashboard(page_data, html=html, base_url=self.client.base_url)
        courses = dashboard.get("courses") or []
        observed = enrollment_coverage(html, response.url, self.client.base_url + "/Kampus1", courses)
        dashboard["enrollment_coverage"] = observed
        if observed["status"] == "complete":
            self._course_cache.set(COURSES_CACHE_KEY, courses)
        else:
            if courses and observed["status"] != "failed" and self._course_cache.get(COURSES_CACHE_KEY) is None:
                self._course_cache.set(COURSES_CACHE_KEY, courses)
            dashboard["parse_warning"] = (
                f"Course discovery is {observed['status']}: {observed.get('reason')}. "
                "Missing courses cannot be treated as removed."
            )
        return dashboard

    def list_courses(self, refresh: bool = False) -> dict[str, Any]:
        if not refresh:
            cached = self._course_cache.get(COURSES_CACHE_KEY)
            if cached is not None:
                return {
                    "count": len(cached),
                    "courses": cached,
                    "source": "cache",
                    "cache_ttl_seconds": self._course_cache_ttl_seconds,
                    "enrollment_coverage": coverage("unknown", "cached_discovery"),
                }

        dashboard = self._read_dashboard()
        courses = dashboard.get("courses") or []
        observed = dashboard.get("enrollment_coverage") or coverage("unknown", "missing_coverage")
        if observed["status"] == "complete":
            self._course_cache.set(COURSES_CACHE_KEY, courses)
        result: dict[str, Any] = {
            "count": len(courses),
            "courses": courses,
            "source": "live",
            "enrollment_coverage": observed,
        }
        if dashboard.get("parse_warning"):
            result["parse_warning"] = dashboard["parse_warning"]
        return result

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
        self._require_upload_page_url(response.url, "The upload page")
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
        # Read once, here. The size and hash shown to the user and the bytes
        # that are sent all come from this one read, so a file that changes on
        # disk after the user approved cannot change what is uploaded.
        with path.open("rb") as handle:
            file_bytes = handle.read(MAX_UPLOAD_BYTES + 1)
        size = len(file_bytes)
        if size <= 0:
            raise NinovaError(f"File is empty: {path}")
        if size > MAX_UPLOAD_BYTES:
            raise NinovaError(
                f"File too large (more than {MAX_UPLOAD_BYTES} bytes). Max is {MAX_UPLOAD_BYTES} bytes."
            )
        file_sha256 = hashlib.sha256(file_bytes).hexdigest()

        resolved_upload = self._resolve_assignment_upload_url(
            course=course,
            assignment=assignment,
            upload_url=upload_url,
        )
        html, response = self.client.get_html(resolved_upload["upload_url"])
        page_ids = self._require_upload_page_url(response.url, "The upload page")
        form = extract_assignment_upload_form(html, response.url, base_url=self.client.base_url)
        if not form.get("ok"):
            raise NinovaError(form.get("error") or "Could not parse assignment upload form.")
        if not form.get("submit_event_target"):
            raise NinovaError(form.get("error") or "Submit control not found on upload page.")
        # The form's action is page content. The file is posted only to the
        # upload page of the same assignment on the Ninova host.
        post_url = form.get("form_url") or response.url
        if self._require_upload_page_url(post_url, "The upload form's target") != page_ids:
            raise NinovaError(
                "The upload form's target belongs to a different assignment than the upload page. "
                "Nothing was sent."
            )

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
            "form_url": post_url,
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
                "sha256": file_sha256,
            },
        }

        if not confirm:
            preview["message"] = (
                "Dry run only. Re-call submit_assignment with the same arguments "
                "and confirm=true to actually upload this file to Ninova."
            )
            return preview

        # Last gate before the upload: the user answers in the client itself.
        # confirm=true from the model does not skip this.
        from .interaction import confirm_write, public_confirmation, upload_confirmation_message

        confirmation = confirm_write(upload_confirmation_message(
            resolved_upload.get("course"), resolved_upload.get("assignment"), response.url,
            slot, path.name, str(path), size,
            post_url=post_url, sha256_hex=file_sha256,
        ))
        if confirmation is not None and confirmation["proceed"] is not True:
            return {
                **preview,
                "dry_run": False,
                "ok": False,
                "uploaded": False,
                "status": confirmation["status"],
                "message": confirmation["note"],
                "confirmation": public_confirmation(confirmation),
            }

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
            post_url,
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
            **({"confirmation": public_confirmation(confirmation)} if confirmation is not None else {}),
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

    def _resolve_assignment_upload_url(
        self,
        *,
        course: str | None,
        assignment: str | None,
        upload_url: str | None,
    ) -> dict[str, Any]:
        if upload_url:
            normalized = self.client.ninova_url(upload_url)
            if ASSIGNMENT_DETAIL_PATH_RE.match(urlparse(normalized).path):
                # Detail URL like .../Odev/123 -> .../Odev/123/OdevGonder is common.
                normalized = normalized.split("?", 1)[0].split("#", 1)[0].rstrip("/") + "/OdevGonder"
            self._require_upload_page_url(normalized, "upload_url")
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
        # This link was read from a Ninova page, so it is checked like any other.
        self._require_upload_page_url(resolved_upload, "The assignment's upload link")
        return {
            "upload_url": resolved_upload,
            "course": resolved_course,
            "assignment": chosen,
        }

    def _require_upload_page_url(self, url: str, what: str) -> tuple[str, str]:
        """Return (course ID, assignment ID) of a Ninova upload page URL, or raise.

        Accepts only the Ninova host and the path
        ``/Sinif/<id>.<id>/Odev/<id>/OdevGonder``.
        """
        checked = self.client.ninova_url(url)
        match = UPLOAD_PATH_RE.match(urlparse(checked).path)
        if not match:
            raise NinovaError(
                f"{what} is not a Ninova assignment upload page "
                "(/Sinif/<id>.<id>/Odev/<id>/OdevGonder). Nothing was sent."
            )
        return match.group(1), match.group(2)

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
        from .archive import normalize_course_code

        def normalized_code(value: str | None) -> str | None:
            try:
                return normalize_course_code(value or "")
            except ValueError:
                return None

        target_code = normalized_code(course)
        exact_matches = [
            item
            for item in courses
            if (target_code is not None and target_code in {normalized_code(item.get("code")), normalized_code(item.get("title"))})
            or (target and target in {
                normalize_lookup_text(item.get("code")),
                normalize_lookup_text(item.get("title")),
            })
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            options = ", ".join(
                f"{item.get('code') or '?'} ({item.get('title') or item['url']})"
                for item in exact_matches[:8]
            )
            raise NinovaError(f"Ambiguous course reference: {course}. Matches: {options}")

        fuzzy_matches = [] if target_code is not None else [
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
        errors: list[dict[str, str]] = []
        unvisited_folders: list[str] = []

        while queue:
            current_url, depth, current_path = queue.pop(0)
            normalized = self._strip_fragment(current_url)
            if normalized in visited:
                continue
            visited.add(normalized)

            try:
                html, response = self.client.get_html(current_url)
                listing = extract_file_directory(
                    html, response.url, base_url=self.client.base_url, current_path=current_path,
                )
                observation = scope_coverage(html, response.url, current_url, "class_files", listing)
            except Exception as exc:
                errors.append({"path": current_url, "error": str(exc)})
                continue
            if observation["status"] != "complete":
                errors.append({"path": current_url, "error": observation.get("reason") or observation["status"]})
            pages.append(
                {
                    "url": response.url,
                    "current_path": current_path,
                    "entry_count": len(listing["entries"]),
                    "coverage": observation,
                }
            )

            for entry in listing["entries"]:
                entries.append(entry)
                if recursive and entry["entry_type"] == "folder" and depth < max_depth:
                    queue.append((entry["url"], depth + 1, entry["path"]))
                elif entry["entry_type"] == "folder" and self._strip_fragment(entry["url"]) not in visited:
                    unvisited_folders.append(entry["url"])

        if unvisited_folders:
            errors.append({"path": start_url, "error": "file_depth_or_recursion_limit"})
        observation = coverage(
            "partial" if errors and pages else "failed" if errors else "complete",
            "incomplete_file_inventory" if errors else None,
            max_depth=max_depth, recursive=recursive, unvisited_folder_count=len(unvisited_folders),
        )

        return {
            "root_url": start_url,
            "recursive": recursive,
            "max_depth": max_depth,
            "pages_visited": len(pages),
            "pages": pages,
            "entry_count": len(entries),
            "entries": entries,
            "coverage": observation,
            "errors": errors,
        }

    def _extract_course_root_path(self, url: str) -> str:
        parsed = urlparse(url)
        match = re.search(r"(/Sinif/\d+\.\d+)", parsed.path)
        if not match:
            raise NinovaError("course_url must point to a Ninova course path like /Sinif/<id>.<id>.")
        return match.group(1)

    def _strip_fragment(self, url: str) -> str:
        parsed = urlparse(url)
        suffix = f"?{parsed.query}" if parsed.query else ""
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}{suffix}"
