"""Table export tool: ``export_table``.

Each dataset is read with the functions the matching tool already uses
(``gpa_term`` for a term, the record reader of ``gpa_simulation`` for all
attempts, ``obs_schedule`` for the week, the tracking state for deadlines) and
written by ``table_export``. Nothing is fetched in a new way here.

This module does not use ``from __future__ import annotations``: the tool
signature is read when the tool is registered, and real ``Annotated`` types
are what give every parameter a JSON schema type and a description.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from . import table_export
from .client import NinovaError
from .local_files import (
    LocalFileError,
    create_new_file,
    downloads_dir,
    resolve_output_dir,
    safe_download_name,
)
from .obs_client import ObsClient
from .tool_metadata.export import (
    DATASET_DESCRIPTION,
    DATASETS,
    SAVE_DESCRIPTION,
    SEMESTER_DESCRIPTION,
)

EXPORTS_SUBDIR = "exports"
DELIMITER = ";"

COLUMNS: dict[str, tuple[str, ...]] = {
    "term_grades": ("Ders Kodu", "Ders Adı", "Kredi", "Harf Notu"),
    "all_grades": ("Dönem", "Ders Kodu", "Ders Adı", "Kredi", "Harf Notu", "Durum"),
    "weekly_schedule": ("Gün", "Başlangıç", "Bitiş", "Ders Kodu", "Ders Adı", "CRN", "Bina", "Derslik"),
    "deadlines": ("Ders Kodu", "Ders", "Ödev", "Son Teslim (ISO)", "Son Teslim", "Yüklendi"),
}
_FILENAMES = {
    "term_grades": "itu-donem-notlari",
    "all_grades": "itu-tum-notlar",
    "weekly_schedule": "itu-haftalik-program",
    "deadlines": "itu-teslim-tarihleri",
}
_TERM_DATASETS = {"term_grades", "weekly_schedule"}

# The ``Durum`` column of all_grades.
COUNTED = "Sayılıyor"
SUPERSEDED = "Tekrar edildi"
NOT_IN_GPA = "Ortalamaya girmez"
CREDIT_UNKNOWN = "Kredi bilinmiyor"
# The ``Yüklendi`` column of deadlines when the upload slots were not read.
UPLOAD_UNKNOWN = "Bilinmiyor"

_Table = tuple[list[tuple[Any, ...]], dict[str, Any]]


def _term_slug(resolved: dict[str, Any]) -> str | None:
    code = str(resolved.get("donemKodu") or "").strip()
    return code if code.isalnum() else None


class TableExportToolsMixin:
    """Tool method mixed into ``NinovaMcpApp``, which supplies the clients and the state."""

    obs: ObsClient
    state_dir: Path

    def export_table(
        self,
        dataset: Annotated[
            Literal["term_grades", "all_grades", "weekly_schedule", "deadlines"],
            Field(description=DATASET_DESCRIPTION),
        ],
        semester: Annotated[str | int | None, Field(description=SEMESTER_DESCRIPTION)] = None,
        save: Annotated[bool, Field(description=SAVE_DESCRIPTION)] = False,
    ) -> dict[str, Any]:
        """One of the student's own tables as CSV text, or as a saved file."""
        if dataset not in DATASETS:
            raise ValueError("dataset must be one of: " + ", ".join(DATASETS) + ".")
        if isinstance(semester, bool) or not isinstance(semester, (str, int, type(None))):
            raise ValueError('semester must be a term code, id or name, e.g. "202610".')
        if not isinstance(save, bool):
            raise ValueError("save must be true or false.")
        wanted = None if semester is None else str(semester).strip() or None

        resolved: dict[str, Any] | None = None
        if dataset in _TERM_DATASETS:
            resolved = self.obs.resolve_semester(wanted)
            rows, metadata = (
                self._export_term_grades(resolved) if dataset == "term_grades"
                else self._export_weekly_schedule(resolved)
            )
        elif dataset == "all_grades":
            rows, metadata = self._export_all_grades()
        else:
            rows, metadata = self._export_deadlines()

        columns = COLUMNS[dataset]
        slug = _term_slug(resolved) if resolved else None
        filename = _FILENAMES[dataset] + (f"-{slug}" if slug else "") + ".csv"
        result: dict[str, Any] = {
            "dataset": dataset,
            "columns": list(columns),
            "row_count": len(rows),
            "mime_type": table_export.CSV_MIME_TYPE,
            "delimiter": DELIMITER,
            "encoding": "utf-8-sig",
            "suggested_filename": filename,
        }
        if resolved is not None:
            result["semester"] = {
                "id": resolved.get("akademikDonemId"),
                "code": resolved.get("donemKodu"),
                "name": resolved.get("akademikDonemAdi"),
            }
        elif wanted is not None:
            result["semester_ignored"] = True
        result.update(metadata)

        if save:
            text, _written = table_export.render_csv(columns, rows, delimiter=DELIMITER)
            result.update(self._save_export(filename, text))
            return result
        text, written = table_export.render_csv(
            columns, rows, delimiter=DELIMITER, max_bytes=table_export.MAX_INLINE_BYTES,
        )
        if written < len(rows):
            result.update(row_count=written, truncated=True, total_row_count=len(rows))
        result["csv"] = text
        return result

    # ------------------------------------------------------------ delivery

    def _save_export(self, filename: str, text: str) -> dict[str, Any]:
        """Write the text into ``<state>/downloads/exports`` under a new name."""
        try:
            target_dir = resolve_output_dir(
                None, state_dir=self.state_dir, default=downloads_dir(self.state_dir) / EXPORTS_SUBDIR,
            )
            name, _neutralised = safe_download_name(filename)
            path, handle = create_new_file(target_dir, name)
        except LocalFileError as exc:
            raise NinovaError(str(exc)) from exc
        # The text starts with the byte order mark, so this is "utf-8-sig".
        data = text.encode("utf-8")
        try:
            with handle:
                handle.write(data)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {"path": str(path), "size_bytes": len(data)}

    # ------------------------------------------------------------ datasets

    def _export_term_grades(self, resolved: dict[str, Any]) -> _Table:
        """The registered courses of the term, read the way ``obs_calculate_gpa`` reads them."""
        from .gpa import calculate_gpa, plan_credit_table, plan_rows, reconcile_credit_candidates
        from .gpa_common import parse_credit
        from .gpa_term import every_course_failed, read_official_reference, registered_rows, term_course

        registered = registered_rows(self.obs.list_registered_courses(resolved["akademikDonemId"]))
        term_code = resolved.get("donemKodu")
        info = self._fetch_graduation_info()  # type: ignore[attr-defined]
        plan = plan_rows(info)
        credit_table = plan_credit_table(plan, term_code)
        plan_grades = self._plan_grade_lookup(info, term_code)  # type: ignore[attr-defined]
        courses = [
            term_course(item, credit_table=credit_table, plan_grades=plan_grades, plan=plan, term_code=term_code)[0]
            for item in registered
        ]
        reference = read_official_reference(
            lambda: self.obs.get_registration_status(), term_code,
            requested=True, zero_average_is_final=every_course_failed(courses),
        )
        official = reference.get("gpa")
        reconcile_credit_candidates(courses, official)
        calculated = calculate_gpa(courses).get("gpa")
        rows = [
            (course["code"], course["name"], parse_credit(course["credit"]),
             course["grade"] if isinstance(course["grade"], str) else None)
            for course in courses
        ]
        return rows, {
            "term_gpa": official if official is not None else calculated,
            "term_gpa_source": "official_obs" if official is not None
            else "calculated" if calculated is not None else None,
            "graded_course_count": sum(1 for row in rows if row[3]),
        }

    def _export_all_grades(self) -> _Table:
        """Every graded attempt, with the retake decision of the cumulative GPA simulation."""
        from .gpa_simulation import (
            _MAX_EQUIVALENCE_CANDIDATES,
            _choose_equivalences,
            _equivalence_candidates,
            _graduation_info,
            _state,
            rebuild_record,
        )

        graduation = self.obs.get_graduation_remaining(self.obs.default_program_id())
        if not isinstance(graduation, dict) or not isinstance(graduation.get("mezuniyetimeNeKaldiBilgi"), dict):
            raise NinovaError("The graduation record could not be read; nothing was exported.")
        warnings: list[str] = []
        try:
            status = self.obs.get_registration_status()
        except (NinovaError, ValueError):
            status = None
            warnings.append(
                "official_series_unavailable: the official per-term record could not be read, "
                "so the retake decisions were checked against the current official GPA only."
            )
        record = rebuild_record(_graduation_info(graduation), status)
        pairs = _equivalence_candidates(record.attempts)[:_MAX_EQUIVALENCE_CANDIDATES]
        groups, _applied, check = _choose_equivalences(
            record.attempts, pairs, record.official_terms, record.official_gpa,
        )
        counted = {id(attempt) for attempt in _state(record.attempts, groups).values()}

        def state_of(attempt: Any) -> str:
            if attempt.coefficient is None:
                return NOT_IN_GPA
            if attempt.credit is None:
                return CREDIT_UNKNOWN
            return COUNTED if id(attempt) in counted else SUPERSEDED

        rows = [
            (attempt.term, attempt.code, attempt.name, attempt.credit, attempt.grade, state_of(attempt))
            for attempt in record.attempts
        ]
        metadata: dict[str, Any] = {
            "counted_count": len(counted),
            "official_gpa": record.official_gpa,
            "model_status": "not_validated" if check["total"] == 0
            else "reproduced" if check["points"] == check["total"] else "mismatch",
        }
        if record.warnings or warnings:
            metadata["warnings"] = [*record.warnings, *warnings]
        return rows, metadata

    def _export_weekly_schedule(self, resolved: dict[str, Any]) -> _Table:
        from .obs_schedule import weekly_schedule

        meetings, recognized = weekly_schedule(self.obs.get_schedule(resolved["akademikDonemId"]))
        rows = [
            tuple(meeting.get(key) for key in
                  ("day", "start", "end", "course_code", "title", "crn", "building", "room"))
            for meeting in meetings
        ]
        return rows, {} if recognized else {"weekly_schedule_status": "source_shape_not_recognized"}

    def _export_deadlines(self) -> _Table:
        """Dated assignments of the stored snapshot. Nothing is synced or requested."""
        from .app_tracking import tracking_max_age_seconds
        from .parsing import ninova_datetime_iso
        from .tracking import scope_observation, snapshot_freshness

        state = self._load_tracking_state_document()  # type: ignore[attr-defined]
        freshness = snapshot_freshness(state, max_age_seconds=tracking_max_age_seconds())
        now = datetime.now(tz=UTC)
        dated: list[tuple[datetime, tuple[Any, ...]]] = []
        incomplete = 0
        for entry in (state.get("courses") or {}).values():
            course = entry.get("course") or {}
            snapshot = entry.get("snapshot") or {}
            if scope_observation(snapshot, "assignments").get("status") != "complete":
                incomplete += 1
            for assignment in (snapshot.get("overview") or {}).get("assignments") or []:
                iso = assignment.get("submission_end_iso") or ninova_datetime_iso(assignment.get("submission_end"))
                if iso is None:
                    continue
                try:
                    due = datetime.fromisoformat(iso).astimezone(UTC)
                except ValueError:
                    continue
                requested = assignment.get("requested_file_count") or 0
                uploaded = assignment.get("uploaded_file_count") or 0
                dated.append((due, (
                    course.get("code"), course.get("title"), assignment.get("title"), iso,
                    assignment.get("submission_end"),
                    # Without the upload slots (a sync without assignment details) this is not known.
                    (requested <= uploaded) if requested else UPLOAD_UNKNOWN,
                )))
        dated.sort(key=lambda item: item[0])
        if freshness["status"] == "no_snapshot":
            status = "no_snapshot"
        elif freshness["stale"]:
            status = "stale_snapshot"
        else:
            status = "incomplete_snapshot" if incomplete else "ok"
        metadata: dict[str, Any] = {
            "status": status,
            "stale": status in {"no_snapshot", "stale_snapshot"},
            "snapshot_age_hours": freshness["snapshot_age_hours"],
            "stale_after_hours": freshness["stale_after_hours"],
            "last_sync_at": state.get("last_sync_at"),
            "upcoming_count": sum(1 for due, _row in dated if due >= now),
        }
        if status != "ok":
            metadata["message"] = (
                "The table holds what the stored snapshot has; no sync was run. It may miss "
                "assignments. Call sync_all_courses, then export again."
            )
        return [row for _due, row in dated], metadata
