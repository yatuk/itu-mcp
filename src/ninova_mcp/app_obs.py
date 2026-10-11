"""OBS student record tools: profile, grades, attendance, schedule, transcript."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .graduation import summarize_graduation_plan
from .local_files import LocalFileError, downloads_dir, resolve_output_dir
from .obs_client import ObsClient, ObsError, ObsPublicClient, redact_obs_profile
from .operator_settings import SENSITIVE_OUTPUT_DISABLED_NOTE, sensitive_output_allowed
from .parsing import normalize_lookup_text
from .attendance_summary import summarize_obs_attendance


def _registered_label(item: dict[str, Any]) -> str:
    code = f"{item.get('bransKodu') or ''} {item.get('dersKodu') or ''}".strip()
    crn = f"; CRN {item.get('crn')}" if item.get("crn") else ""
    return f"{code} ({item.get('dersAdiTR')}{crn})"


def _registered_options(items: list[dict[str, Any]]) -> str:
    return ", ".join(_registered_label(item) for item in items[:12])


def _term_note(semester: dict[str, Any]) -> str:
    """Name the term that was searched, so a wrong term is visible in the error."""
    label = semester.get("akademikDonemAdi") or semester.get("akademikDonemKodu") or semester.get("akademikDonemId")
    return f" (term searched: {label})" if label else ""


def _match_registered(course: str, items: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Match a reference against registered OBS classes (see ``course_matching``).

    Codes are compared without spacing or case (``cen354e``), names without
    accents or case and with Roman and Arabic numerals as equal (``fizik 2``).
    """
    from .course_matching import match_courses

    return match_courses(
        course,
        items,
        codes=lambda item: (f"{item.get('bransKodu') or ''} {item.get('dersKodu') or ''}",),
        names=lambda item: (item.get("dersAdiTR"), item.get("dersAdiEN")),
        crn=lambda item: str(item.get("crn") or "") or None,
        partial_text=lambda item: (" ".join(
            str(item.get(key) or "") for key in ("bransKodu", "dersKodu", "dersAdiTR", "dersAdiEN", "crn")
        ),),
    )


class ObsRecordToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``obs`` and ``obs_public``."""

    obs: ObsClient
    obs_public: ObsPublicClient
    state_dir: Path

    def _redact_unless_operator_allows(
        self, payload: dict[str, Any], include_sensitive: bool,
    ) -> dict[str, Any]:
        """Redact unless the model asked AND the operator enabled unredacted output.

        ``include_sensitive`` is a tool argument, so on its own it is not a
        decision by a person. The environment switch is.
        """
        if include_sensitive and sensitive_output_allowed():
            return payload
        redacted = redact_obs_profile(payload)
        if include_sensitive and isinstance(redacted, dict):
            redacted["sensitive_output"] = "disabled_by_operator"
            redacted["sensitive_output_note"] = SENSITIVE_OUTPUT_DISABLED_NOTE
        return redacted

    def obs_get_profile(self, include_sensitive: bool = False) -> dict[str, Any]:
        return self._redact_unless_operator_allows(self.obs.get_profile(), bool(include_sensitive))

    def obs_list_programs(self) -> dict[str, Any]:
        return self.obs.list_programs()

    def obs_list_semesters(self) -> dict[str, Any]:
        return self.obs.list_semesters()

    def obs_get_grade_distribution(
        self, course_code: str, year: int | None = None, term_code: str | None = None,
    ) -> dict[str, Any]:
        from .grade_distribution import get_grade_distribution
        return get_grade_distribution(self.obs_public, course_code, year, term_code)

    def obs_get_advisor(self) -> dict[str, Any]:
        return self.obs.get_advisor()

    def obs_get_internships(self) -> dict[str, Any]:
        return self.obs.get_internships()

    def obs_get_contacts(self, include_sensitive: bool = False) -> dict[str, Any]:
        return self._redact_unless_operator_allows(self.obs.get_contacts(), bool(include_sensitive))

    def obs_list_registered_courses(self, semester: str | int | None = None) -> dict[str, Any]:
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
        semester: str | int | None = None,
        course: str | int | None = None,
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
                from .obs_grades import get_class_letter_grades

                result["letter_grades"] = get_class_letter_grades(
                    self.obs, resolved_class, semester=semester,
                )
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
        semester: str | int | None = None,
        course: str | int | None = None,
        include_summary: bool = True,
        max_absence_ratio: float = 0.30,
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
        result: dict[str, Any] = {
            "class": resolved_class,
            "attendance": attendance,
            "error": error,
        }
        if include_summary:
            result["summary"] = summarize_obs_attendance(
                attendance,
                max_absence_ratio=max_absence_ratio,
                course=resolved_class,
            )
        return result

    def obs_get_schedule(self, semester: str | int | None = None) -> dict[str, Any]:
        resolved = self.obs.resolve_semester(semester)
        return {
            "semester": resolved,
            "schedule": self.obs.get_schedule(resolved["akademikDonemId"]),
            "final_calendar": self.obs.get_final_calendar(resolved["akademikDonemId"]),
        }

    def obs_get_graduation_remaining(self, program_id: int | None = None) -> dict[str, Any]:
        pid = program_id if program_id is not None else self.obs.default_program_id()
        graduation = self.obs.get_graduation_remaining(pid)
        return {
            "program_id": pid,
            "summary": summarize_graduation_plan(graduation),
            "graduation": graduation,
            "academic_status": self.obs.get_academic_status(pid),
            "debts": self.obs.get_debts(pid),
        }

    def obs_download_transcript(
        self,
        english: bool = False,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        # output_dir is written by the model: it may only name a place inside
        # the downloads directory or a directory the operator listed in
        # NINOVA_EXTRA_DOWNLOAD_DIRS. Checked before OBS is contacted.
        try:
            target = resolve_output_dir(
                output_dir, state_dir=self.state_dir, default=downloads_dir(self.state_dir) / "obs",
            )
        except LocalFileError as exc:
            raise ObsError(str(exc)) from exc
        return self.obs.save_transcript_pdf(target, english=english)

    def _resolve_obs_class(
        self,
        *,
        class_id: int | None,
        semester: str | int | None,
        course: str | int | None,
    ) -> dict[str, Any]:
        if class_id is not None:
            return {"sinifId": class_id, "source": "class_id"}
        if not course:
            raise ObsError("Provide class_id, or course (+ optional semester).")
        resolved_semester = self.obs.resolve_semester(semester)
        payload = self.obs.list_registered_courses(resolved_semester["akademikDonemId"])
        items = payload.get("kayitSinifResultList") or []
        tier, matches = _match_registered(str(course), items)
        if not matches:
            raise ObsError(
                f"OBS course not found: {course}{_term_note(resolved_semester)}. "
                f"Available: {_registered_options(items)}"
            )
        if len(matches) > 1:
            options = "; ".join(
                f"sinifId={i.get('sinifId')} {_registered_label(i)}" for i in matches[:8]
            )
            raise ObsError(
                f"Ambiguous OBS course {course!r}{_term_note(resolved_semester)}. Matches: {options}. "
                "Pass class_id, the CRN or the full course code."
            )
        chosen = matches[0]
        chosen = {
            **chosen,
            "source": "course_lookup",
            "semester": resolved_semester,
            "matched_by": tier,
            "requested": course,
        }
        return chosen

    def get_personal_exam_calendar(
        self,
        semester: str | int | None = None,
        course: str | int | None = None,
    ) -> dict[str, Any]:
        """Read the authenticated student's OBS final calendar directly."""
        resolved = self.obs.resolve_semester(semester)
        semester_id = resolved["akademikDonemId"]
        registered_payload = self.obs.list_registered_courses(semester_id)
        registered = registered_payload.get("kayitSinifResultList") or []
        final_calendar = self.obs.get_final_calendar(semester_id)
        target = normalize_lookup_text(str(course)) if course else ""
        matched_courses: list[dict[str, Any]] = []
        if target:
            # A filter, so several matching courses are all kept.
            _tier, matched_courses = _match_registered(str(course), registered)
            if not matched_courses:
                raise ObsError(
                    f"Kayıtlı dersler arasında eşleşme bulunamadı: {course}{_term_note(resolved)}. "
                    f"Kayıtlı dersler: {_registered_options(registered) or 'yok'}"
                )
        return {
            "semester": resolved,
            "course_filter": course,
            "matched_courses": matched_courses if target else registered,
            "final_calendar": final_calendar,
            "source": "obs.itu.edu.tr/api/ogrenci/Takvim/FinalTakvimi",
            "untrusted_external_content": True,
        }
