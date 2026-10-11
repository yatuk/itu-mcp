"""Ninova course tools: dashboard, course pages, file listings and assignment upload.

Also holds the course helpers the other Ninova mixins share: resolving a
course reference, walking a file directory and the course URL helpers.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from .assignment_upload import (
    DRY_RUN_MESSAGE,
    LocalUpload,
    UploadPage,
    choose_upload_slot,
    find_assignment,
    multipart_fields,
    read_upload_once,
    slot_after_upload,
    text_reports_success,
    upload_link_of,
    upload_preview,
    upload_result,
    uploads_disabled,
)
from .client import NinovaAuthError, NinovaClient, NinovaError
from .local_files import LocalFileError, resolve_upload_file
from .tool_params import publish_include_raw
from .parsing import (
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
    apply_attendance_calendar,
    parse_ninova_datetime,
    TURKIYE_TZ,
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


def _names_a_course_url(reference: str) -> bool:
    return (
        "/Sinif/" in reference
        or reference.startswith("/")
        or urlparse(reference).scheme.lower() in {"http", "https"}
    )


def _crn_of_course(item: dict[str, Any]) -> str | None:
    if item.get("crn"):
        return str(item["crn"])
    # Course lists cached or stored before the crn field existed.
    match = re.search(r"\bCRN\s*:?\s*(\d{3,6})\b", str(item.get("context") or ""), re.I)
    return match.group(1) if match else None


def _describe_course(item: dict[str, Any]) -> str:
    """One candidate of an ambiguous reference, with what tells it apart from the others."""
    details = [str(item.get("title") or "?")]
    section = item.get("section")
    crn = _crn_of_course(item)
    if section and not (crn and crn in str(section)):
        details.append(f"section {section}")
    if crn:
        details.append(f"CRN {crn}")
    details.append(item["url"])
    return f"{item.get('code') or '?'} ({'; '.join(details)})"


def _course_label(item: dict[str, Any]) -> str:
    if item.get("code") and item.get("title"):
        return f"{item.get('code')} ({item['title']})"
    return str(item.get("code") or item.get("title") or item["url"])


def _available_courses_hint(courses: list[dict[str, Any]]) -> str:
    available = ", ".join(_course_label(item) for item in courses[:12])
    return f" Available courses: {available}." if available else " Course list is empty."


def _file_inventory_status(errors: list[dict[str, str]], pages: list[dict[str, Any]]) -> str:
    if not errors:
        return "complete"
    return "partial" if pages else "failed"


class NinovaCourseToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``client`` and the course cache."""

    client: NinovaClient

    def get_dashboard(self, compact: bool = False, include_raw: bool = False) -> dict[str, Any]:
        from .output_shape import RAW_AVAILABLE_KEY, raw_hint

        # Internal enrollment reads use _read_dashboard, so they always see
        # the full payload whatever this tool leaves out or shortens.
        dashboard = dict(self._read_dashboard())
        if "links" in dashboard:
            dashboard["link_count"] = len(dashboard.get("links") or [])
        if not include_raw:
            # The link list is mostly site navigation and the headings only
            # repeat the section names; the courses carry their own URLs.
            dashboard.pop("links", None)
            page = dashboard.get("page")
            if isinstance(page, dict):
                dashboard["page"] = {key: value for key, value in page.items() if key != "headings"}
            dashboard[RAW_AVAILABLE_KEY] = raw_hint("links", "page.headings")
        # include_raw decides what the result holds; compact then shortens it.
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
        course: str | int,
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
        for item in announcements:
            item["published_at_iso"] = ninova_datetime_iso(item.get("published_at"))
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
        for item in announcements:
            item["published_at_iso"] = ninova_datetime_iso(item.get("published_at"))
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
        course: str | int,
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

    def get_course_info(self, course: str | int) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/SinifBilgileri")
        payload = extract_course_info(html, response.url, base_url=self.client.base_url)
        payload["course"] = resolved
        return payload

    def get_course_sections(self, course: str | int) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"])
        sections = extract_course_sections(html, response.url, base_url=self.client.base_url)
        return {
            "course": resolved,
            "count": len(sections),
            "sections": sections,
        }

    def get_course_grades(self, course: str | int) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/Notlar")
        payload = extract_gradebook(html, response.url, base_url=self.client.base_url)
        payload["course"] = resolved
        if not payload.get("grades"):
            # Ninova prints an average of "0,00" on a page without any grade.
            payload["weighted_average_note"] = (
                "No grade has been entered for this course yet; the weighted average shown by "
                "Ninova is a placeholder, not a result."
            )
        return payload

    def get_course_message_board(
        self,
        course: str | int,
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

    def get_course_attendance(self, course: str | int) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/Yoklama")
        payload = extract_attendance(html, response.url, base_url=self.client.base_url)
        # The term start dates the weeks, so weeks that have not happened yet
        # are not counted. Without it the grid is reported undated.
        term_start = None
        try:
            info_html, info_response = self.client.get_html(resolved["url"] + "/SinifBilgileri")
            info = extract_course_info(info_html, info_response.url, base_url=self.client.base_url)
            parsed = parse_ninova_datetime(str((info.get("class_meta") or {}).get("Başlangıç Tarihi") or ""))
            term_start = parsed.date() if parsed else None
        except Exception:
            term_start = None
        apply_attendance_calendar(payload, term_start=term_start, today=self._today())
        payload["course"] = resolved
        return payload

    def _today(self) -> date:
        return datetime.now(tz=TURKIYE_TZ).date()

    def get_course_remote_learning(self, course: str | int) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/UzaktanEgitim")
        payload = extract_remote_learning(html, response.url, base_url=self.client.base_url)
        payload["course"] = resolved
        return payload

    def get_course_class_files(
        self,
        course: str | int,
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
        course: str | int,
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
        course: str | int | None = None,
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
        course: str | int | None = None,
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

        The steps run in this order and none is skipped: switch, local file,
        upload page, slot, preview, confirmation, POST. Nothing is requested
        before the local file is accepted and read, and nothing is posted
        before the user's answer.
        """
        if uploads_disabled():
            raise NinovaError(
                "Assignment uploads are disabled (NINOVA_ALLOW_UPLOADS=0)."
            )
        upload = self._read_local_upload(file_path)

        resolved_upload = self._resolve_assignment_upload_url(
            course=course,
            assignment=assignment,
            upload_url=upload_url,
        )
        page = self._open_upload_page(resolved_upload["upload_url"])
        slot = choose_upload_slot(
            page.form,
            upload.path.name,
            slot_index=slot_index,
            slot_description=slot_description,
            allow_replace=allow_replace,
            match_slot=match_upload_slot,
        )

        preview = upload_preview(resolved_upload, page, slot, upload, dry_run=not confirm)
        if not confirm:
            preview["message"] = DRY_RUN_MESSAGE
            return preview

        # Last gate before the upload: the user answers in the client itself.
        # confirm=true from the model does not skip this.
        from .interaction import confirm_write, public_confirmation, upload_confirmation_message

        confirmation = confirm_write(upload_confirmation_message(
            resolved_upload.get("course"), resolved_upload.get("assignment"), page.url,
            slot, upload.path.name, str(upload.path), upload.size,
            post_url=page.post_url, sha256_hex=upload.sha256,
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

        post_response, result_html = self._post_upload(page, slot, upload)
        return {
            **({"confirmation": public_confirmation(confirmation)} if confirmation is not None else {}),
            **self._read_upload_outcome(post_response, result_html, slot, preview),
        }

    def _read_local_upload(self, file_path: str) -> LocalUpload:
        """Accept the local path, then read the file once. No request is made here."""
        try:
            path = resolve_upload_file(file_path, state_dir=self.state_dir)
        except LocalFileError as exc:
            raise NinovaError(str(exc)) from exc
        return read_upload_once(path, MAX_UPLOAD_BYTES)

    def _open_upload_page(self, upload_url: str) -> UploadPage:
        """Read the upload page and pin it and its form's target to the same assignment."""
        html, response = self.client.get_html(upload_url)
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
        return UploadPage(response.url, form, post_url)

    def _post_upload(self, page: UploadPage, slot: dict[str, Any], upload: LocalUpload) -> tuple[Any, str]:
        """Post the bytes that were read to the checked form target; return the response and its HTML."""
        data, files = multipart_fields(page.form, slot, upload)
        post_response = self.client.post_multipart(
            page.post_url,
            data=data,
            files=files,
            referer=page.url,
        )
        result_html = self.client._decode_response(post_response)
        if self.client._looks_like_login_page(post_response, html=result_html):
            raise NinovaAuthError("Session expired during assignment upload.")
        return post_response, result_html

    def _read_upload_outcome(
        self, post_response: Any, result_html: str, slot: dict[str, Any], preview: dict[str, Any],
    ) -> dict[str, Any]:
        """Report what the page answering the POST shows for the chosen slot."""
        after = extract_assignment_upload_form(
            result_html,
            post_response.url,
            base_url=self.client.base_url,
        )
        after_slot = slot_after_upload(after, slot)
        success = bool(after_slot and after_slot.get("uploaded"))
        if not success:
            # Fallback: compare status text / page markers.
            page = parse_html_page(post_response.url, result_html, self.client.base_url)
            success = text_reports_success(page.get("text_excerpt"))
        return upload_result(preview, slot, after, after_slot, success=success, response_url=post_response.url)

    def _resolve_assignment_upload_url(
        self,
        *,
        course: str | int | None,
        assignment: str | None,
        upload_url: str | None,
    ) -> dict[str, Any]:
        if upload_url:
            return self._upload_target_at_url(upload_url)

        if not course or not assignment:
            raise NinovaError(
                "Provide upload_url, or both course and assignment "
                "(assignment title / URL / partial name)."
            )

        resolved_course = self._resolve_course(course)
        # By URL: two sections of one course share a code, so the code would
        # make this second lookup ambiguous after the first one succeeded.
        payload = self.get_course_assignments(
            resolved_course["url"],
            limit=200,
            include_details=True,
        )
        chosen = find_assignment(payload.get("assignments") or [], assignment)
        resolved_upload = upload_link_of(chosen)
        # This link was read from a Ninova page, so it is checked like any other.
        self._require_upload_page_url(resolved_upload, "The assignment's upload link")
        return {
            "upload_url": resolved_upload,
            "course": resolved_course,
            "assignment": chosen,
        }

    def _upload_target_at_url(self, upload_url: str) -> dict[str, Any]:
        """The upload target a caller named by URL; an assignment's own URL stands for its upload page."""
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

    def _resolve_course(self, course: str | int) -> dict[str, Any]:
        """Find one course by URL, code, CRN or name.

        Order: course URL; code ignoring spacing and case; the same branch and
        number where only one side has a letter suffix (``ING 201A`` for
        ``ING 201``); CRN; full name; words of the name (accents, case and
        Roman or Arabic numerals ignored); substring of code, name or section
        label. The first step with a candidate decides. Two candidates are an
        error listing both, never a choice, and any step after the exact code
        or full name is reported in ``matched_by``.
        """
        if isinstance(course, bool) or not isinstance(course, (str, int)):
            raise NinovaError("course must be a course code, name, CRN or URL.")
        course = str(course)
        if not course.strip():
            raise NinovaError("course must be a non-empty string.")

        courses = self.list_courses()["courses"]
        if _names_a_course_url(course):
            return self._course_at_url(course, courses)

        from .course_matching import EXACT_TIERS, match_courses

        tier, found = match_courses(
            course,
            courses,
            codes=lambda item: (item.get("code"), item.get("title")),
            names=lambda item: (item.get("title"),),
            crn=_crn_of_course,
            partial_text=lambda item: (item.get("code"), item.get("title"), item.get("context"), item.get("section")),
        )
        if not found:
            raise NinovaError(f"Course not found: {course}.{_available_courses_hint(courses)}")
        if len(found) > 1:
            options = "; ".join(_describe_course(item) for item in found[:8])
            raise NinovaError(
                f"Ambiguous course reference: {course}. Matches: {options}. "
                "Pass the URL of the one you mean."
            )
        if tier in EXACT_TIERS:
            return found[0]
        # A tolerant match is never silent: the caller sees what was matched and how.
        return {**found[0], "matched_by": tier, "requested": course}

    def _course_at_url(self, course_url: str, courses: list[dict[str, Any]]) -> dict[str, Any]:
        """The listed course at a course URL, or a bare record for a course that is not listed."""
        normalized = normalize_url(course_url, self.client.base_url)
        root_path = self._extract_course_root_path(normalized)
        root_url = f"{self.client.base_url}{root_path}"
        for item in courses:
            if item["url"] == root_url:
                return item
        return {"code": None, "title": None, "url": root_url, "context": course_url}

    def _empty_list_warning(self, html: str, page_url: str, *, scope: str) -> str | None:
        """Return a diagnostic when a list page parsed to zero items but holds items.

        Every Ninova page carries menus and layout tables, so the amount of
        text or the number of tables says nothing about the list. The signal
        is a link to an item of this kind (``/Duyuru/<id>``, ``/Odev/<id>``)
        that the parser did not turn into an item.
        """
        page = parse_html_page(page_url, html, base_url=self.client.base_url)
        text = normalize_lookup_text(page.get("text_excerpt") or page.get("text") or "")
        if len(text) < 80 and not page.get("tables") and not page.get("links"):
            return (
                f"Empty {scope} list and the page body looks nearly empty "
                f"(url={page_url}). Session or access rights may be wrong."
            )
        segment = {"announcements": "Duyuru", "assignments": "Odev"}.get(scope)
        if segment is None:
            return None
        item_links = [
            link["url"] for link in page.get("links") or []
            if re.search(rf"/{segment}/\d+", urlparse(link["url"]).path, re.I)
        ]
        if item_links:
            return (
                f"Parsed zero {scope} from {page_url} even though the page links to "
                f"{len(item_links)} item(s) (title={page.get('title')!r}). "
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
                response_url, listing, observation = self._read_directory_page(current_url, current_path)
            except Exception as exc:
                errors.append({"path": current_url, "error": str(exc)})
                continue
            if observation["status"] != "complete":
                errors.append({"path": current_url, "error": observation.get("reason") or observation["status"]})
            pages.append(
                {
                    "url": response_url,
                    "current_path": current_path,
                    "entry_count": len(listing["entries"]),
                    "coverage": observation,
                }
            )

            entries.extend(listing["entries"])
            follow = recursive and depth < max_depth
            for folder in (entry for entry in listing["entries"] if entry["entry_type"] == "folder"):
                if follow:
                    queue.append((folder["url"], depth + 1, folder["path"]))
                elif self._strip_fragment(folder["url"]) not in visited:
                    unvisited_folders.append(folder["url"])

        if unvisited_folders:
            errors.append({"path": start_url, "error": "file_depth_or_recursion_limit"})
        observation = coverage(
            _file_inventory_status(errors, pages),
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

    def _read_directory_page(self, url: str, current_path: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Read one directory page: where it ended up, its listing and how completely it was read."""
        html, response = self.client.get_html(url)
        listing = extract_file_directory(
            html, response.url, base_url=self.client.base_url, current_path=current_path,
        )
        observation = scope_coverage(html, response.url, url, "class_files", listing)
        return response.url, listing, observation

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


publish_include_raw(NinovaCourseToolsMixin, "get_dashboard")
