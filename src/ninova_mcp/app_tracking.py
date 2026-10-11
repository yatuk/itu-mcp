"""Ninova change tracking: course snapshots, sync, updates and upcoming deadlines."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .client import NinovaClient
from .parsing import (
    extract_announcements_list,
    extract_attendance,
    extract_assignments_list,
    extract_course_sections,
    extract_gradebook,
    extract_course_info,
    extract_message_board,
    extract_remote_learning,
    ninova_datetime_iso,
    normalize_lookup_text,
    slugify,
)
from .tracking import (
    TRACKING_STATE_VERSION,
    diff_course_snapshots,
    has_complete_baseline,
    load_tracking_state,
    merge_course_snapshot,
    merge_updates,
    save_tracking_state,
    utc_now_iso,
)
from .tracking_coverage import coverage, scope_coverage


class TrackingToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``client`` and the state paths.

    Course lookups and file walks come from ``NinovaCourseToolsMixin``.
    """

    client: NinovaClient
    tracking_state_path: Path

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
        enrollment = self.list_courses(refresh=True)
        courses = enrollment["courses"]
        observed_enrollment = dict(enrollment.get("enrollment_coverage") or coverage("unknown", "missing_coverage"))
        if enrollment.get("parse_warning") and observed_enrollment["status"] == "complete":
            observed_enrollment = coverage("unknown", enrollment["parse_warning"])
        current_course_urls = {course["url"] for course in courses}
        if course_limit is not None:
            courses = courses[: max(1, min(course_limit, len(courses)))]

        synced_at = utc_now_iso()
        state = self._load_tracking_state_document()
        baseline = not bool(state["courses"])
        updates: list[dict[str, Any]] = []
        course_results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        previous_enrollment = state.get("enrollment_coverage") or coverage("unknown", "legacy_or_missing_coverage")
        enrollment_complete = observed_enrollment["status"] == "complete"
        if not enrollment_complete:
            errors.append({"scope": "enrollment", "error": observed_enrollment.get("reason") or observed_enrollment["status"]})
        if enrollment_complete:
            observed_enrollment["baseline_complete"] = True
            observed_enrollment["last_complete_at"] = synced_at
        elif has_complete_baseline(previous_enrollment):
            observed_enrollment["baseline_complete"] = True
            observed_enrollment["last_complete_at"] = previous_enrollment.get("last_complete_at") or state.get("last_sync_at")

        from .interaction import progress_steps

        steps = progress_steps(len(courses))
        for course in courses:
            steps.advance(
                f"Ders eşitleniyor ({steps.done + 1}/{len(courses)}): "
                f"{course.get('code') or course.get('title') or 'ders'}"
            )
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
            errors.extend({"course": course, **error} for error in snapshot.get("errors", []))
            snapshot = merge_course_snapshot(previous_snapshot, snapshot)
            course_updates = diff_course_snapshots(
                course=course,
                previous_snapshot=None if baseline else previous_snapshot,
                current_snapshot=snapshot,
                detected_at=synced_at,
            )
            if previous_entry is None and not baseline and enrollment_complete and has_complete_baseline(previous_enrollment):
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
                    "coverage": state["courses"][course["url"]]["snapshot"]["coverage"],
                    "snapshot_complete": state["courses"][course["url"]]["snapshot"]["snapshot_complete"],
                }
            )

        steps.finish("Dersler okundu, değişiklikler kaydediliyor")
        removed_course_urls = (
            set(state["courses"]) - current_course_urls
            if enrollment_complete and has_complete_baseline(previous_enrollment) else set()
        )
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
        state["version"] = TRACKING_STATE_VERSION
        state["enrollment_coverage"] = observed_enrollment
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
            "enrollment_coverage": observed_enrollment,
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
                        "is_fully_uploaded": requested_file_count <= uploaded_file_count,
                    }
                )

        deadline_items.sort(key=lambda item: item["submission_end_iso"])
        return {
            "last_sync_at": state["last_sync_at"],
            "days": max(1, min(days, 180)),
            "count": len(deadline_items),
            "deadlines": deadline_items,
        }

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
        observations: dict[str, dict[str, Any]] = {}

        course_html, course_response = self.client.get_html(course["url"])
        sections = extract_course_sections(course_html, course_response.url, base_url=self.client.base_url)
        observations["sections"] = coverage("complete") if sections else coverage("unknown", "course_sections_not_recognized")
        if not sections:
            errors.append({"scope": "sections", "path": course["url"], "error": "course_sections_not_recognized"})

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
            observations=observations,
        )

        announcements = self._safe_extract_course_payload(
            course["url"] + "/Duyurular",
            extractor=lambda html, url, base_url: {
                "announcements": extract_announcements_list(html, url, base_url=base_url)
            },
            default={"announcements": []},
            errors=errors,
            error_scope="announcements",
            observations=observations,
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
            observations=observations,
        )["assignments"][:200]
        if include_assignment_details:
            detailed = []
            for item in assignments:
                try:
                    detailed.append(self._merge_assignment_detail(item))
                except Exception as exc:
                    detailed.append(item)
                    observations["assignments"] = coverage("partial", "assignment_detail_failed")
                    errors.append({"scope": "assignments", "path": item.get("url") or course["url"], "error": str(exc)})
            assignments = detailed
        observations["assignments"]["details_included"] = include_assignment_details
        for item in assignments:
            item["submission_start_iso"] = ninova_datetime_iso(item.get("submission_start"))
            item["submission_end_iso"] = ninova_datetime_iso(item.get("submission_end"))

        files: dict[str, list[dict[str, Any]]] = {"class_files": [], "lesson_files": []}
        for scope, path in (("class_files", "/SinifDosyalari"), ("lesson_files", "/DersDosyalari")):
            observations[scope] = coverage("skipped", "include_files_false")
            if not include_files:
                continue
            try:
                listing = self._walk_file_directory(course["url"] + path, recursive=True, max_depth=file_max_depth)
                files[scope] = listing["entries"]
                observations[scope] = listing["coverage"]
                errors.extend({"scope": scope, **error} for error in listing.get("errors", []))
            except Exception as exc:
                observations[scope] = coverage("failed", "file_listing_failed")
                errors.append({"scope": scope, "path": course["url"] + path, "error": str(exc)})

        grades = self._safe_extract_course_payload(
            course["url"] + "/Notlar",
            extractor=extract_gradebook,
            default={"url": course["url"] + "/Notlar", "student_name": None, "weighted_average": None, "count": 0, "grades": []},
            errors=errors,
            error_scope="grades",
            observations=observations,
        )

        message_board = self._safe_extract_course_payload(
            course["url"] + "/MesajPanosu",
            extractor=extract_message_board,
            default={"url": course["url"] + "/MesajPanosu", "count": 0, "topics": []},
            errors=errors,
            error_scope="message_board",
            observations=observations,
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
            observations=observations,
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
            observations=observations,
        )

        return {
            "course": course,
            "captured_at": utc_now_iso(),
            "overview": {
                "sections": sections,
                "info": info,
                "announcements": announcements,
                "assignments": assignments,
                "class_files": files["class_files"],
                "lesson_files": files["lesson_files"],
                "grades": grades,
                "message_board": message_board,
                "attendance": attendance,
                "remote_learning": remote_learning,
            },
            "errors": errors,
            "coverage": observations,
            "snapshot_complete": all(item["status"] == "complete" for item in observations.values()),
        }

    def _safe_extract_course_payload(
        self,
        path: str,
        *,
        extractor: Callable[[str, str, str], dict[str, Any]],
        default: dict[str, Any],
        errors: list[dict[str, str]],
        error_scope: str,
        observations: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            html, response = self.client.get_html(path)
            payload = extractor(html, response.url, self.client.base_url)
            observation = scope_coverage(html, response.url, path, error_scope, payload)
            observations[error_scope] = observation
            if observation["status"] != "complete":
                errors.append({"scope": error_scope, "path": path, "error": observation.get("reason") or observation["status"]})
            return payload
        except Exception as exc:
            errors.append({"scope": error_scope, "path": path, "error": str(exc)})
            observations[error_scope] = coverage("failed", "fetch_or_parse_failed")
            return default
