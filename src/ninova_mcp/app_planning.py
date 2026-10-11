"""Planning tools: GPA, relative grade, conflicts, degree plans, eligibility, remaining courses."""

from __future__ import annotations

import inspect
import math
import os
from typing import Annotated, Any

from .client import NinovaError
from .archive_client import ItuArchiveClient, ItuArchiveError
from .community_data import CrossCheckDataClient, CrossCheckDataError
from .graduation import summarize_graduation_plan
from .obs_client import ObsClient, ObsError, ObsPublicClient


class PlanningToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies the clients.

    ``plan_remaining_courses`` reads the archive through ``ArchiveHistoryToolsMixin``.
    """

    obs: ObsClient
    obs_public: ObsPublicClient
    archive: ItuArchiveClient
    prereq_crosscheck: CrossCheckDataClient

    def obs_calculate_gpa(
        self,
        semester: str | int | float | None = None,
        projected_grades: dict[str, str] | None = None,
        include_official: bool = True,
    ) -> dict[str, Any]:
        """Calculate a term GPA estimate and compare with the official term record.

        If ``projected_grades`` is given (e.g. ``{"BLG 223E": "AA", ...}``),
        those override the actual grade, which is useful for "what-if"
        scenarios. A key is a course code or a CRN of the term.
        """
        from .archive import normalize_course_code
        from .gpa import (
            attempt_notes,
            calculate_gpa,
            parse_credit,
            plan_credit_table,
            plan_rows,
            reconcile_credit_candidates,
            resolve_projection_keys,
            validate_projected_grades,
        )
        from .gpa_reference import attach_official_reference, official_term_reference
        from .grading import LETTER_TO_GRADE, normalize_grade
        from .parsing import normalize_lookup_text

        # A bad grade is rejected before OBS is contacted.
        validate_projected_grades(projected_grades)
        resolved = self.obs.resolve_semester(semester)
        payload = self.obs.list_registered_courses(resolved["akademikDonemId"])
        if not isinstance(payload, dict) or (
            "statusCode" in payload and (type(payload["statusCode"]) is not int or payload["statusCode"] != 0)
        ):
            raise NinovaError("Registered courses could not be read successfully; GPA was not calculated.")
        registered = payload.get("kayitSinifResultList")
        if not isinstance(registered, list) or any(not isinstance(item, dict) for item in registered):
            raise NinovaError("Registered-course data has an unrecognized shape; GPA was not calculated.")

        # The registered-course endpoint can omit usable credit. Preserve the
        # reported value and identify degree-plan credit used for the estimate.
        # A course's credit does not depend on the attempt, so a plan row of
        # another term is accepted when this term has none.
        #
        # Separately, observed on at least one account: this endpoint's
        # harfNotu comes back None for every course in every term, including
        # terms years in the past, not just "not graded yet". When that
        # happens the tool would otherwise silently compute gpa=None every
        # time. Fall back to the same graduation-remaining payload for
        # grades too, scoped to this term's donemKodu: a retaken course has
        # one entry per attempt with a different grade each time, so a
        # code-only lookup would risk picking the wrong attempt's grade here.
        term_code = resolved.get("donemKodu")
        graduation_info = self._fetch_graduation_info()
        rows = plan_rows(graduation_info)
        credit_table = plan_credit_table(rows, term_code)
        plan_grades = self._plan_grade_lookup(graduation_info, term_code)

        courses: list[dict[str, Any]] = []
        credit_fallbacks: list[str] = []
        missing_credits: list[str] = []
        grade_fallbacks: list[str] = []
        for item in registered:
            code = f"{item.get('bransKodu', '')} {item.get('dersKodu', '')}".strip()
            try:
                code_key = normalize_course_code(code)
            except ValueError:
                code_key = None
            plan = credit_table.get(code_key) or {}
            credit = item.get("kredi")
            credit_source = "registration"
            try:
                numeric = float(str(credit).replace(",", "."))
            except (TypeError, ValueError):
                numeric = 0.0
            if isinstance(credit, bool) or not math.isfinite(numeric) or numeric <= 0:
                fallback = plan.get("credit")
                if fallback:
                    credit = fallback
                    credit_source = (
                        "degree_plan_other_term" if plan["scope"] == "other_term" else "degree_plan"
                    )
                    credit_fallbacks.append(code)
                else:
                    missing_credits.append(code)

            grade = item.get("harfNotu")
            grade_source = "registration"
            if not grade:
                fallback_grade = plan_grades.get(code_key)
                if fallback_grade:
                    grade = fallback_grade
                    grade_source = "degree_plan"
                    grade_fallbacks.append(code)

            course = {
                "code": code,
                "name": item.get("dersAdiTR") or item.get("dersAdiEN", ""),
                "credit": credit,
                "recorded_credit": item.get("kredi"),
                "plan_credit": plan.get("credit"),
                "credit_source": credit_source,
                "grade": grade,
                "grade_source": grade_source,
                "crn": item.get("crn"),
            }
            counted = plan.get("counted_credit")
            if counted is not None:
                course["counted_credit"] = counted
                # Only a plan row that itself carries two different values makes
                # the weight ambiguous. Nothing else is ever tried as a credit.
                if credit_source != "registration" and counted != credit:
                    course["credit_candidates"] = sorted({credit, counted})
            known_credits = {value for value in (parse_credit(credit), counted) if value}
            names = {
                "".join(ch for ch in normalize_lookup_text(str(item.get(field) or "")) if ch.isalnum())
                for field in ("dersAdiTR", "dersAdiEN")
            }
            attempts = attempt_notes(rows, code=code_key, term_code=term_code,
                                     names=names, credits=known_credits)
            if attempts:
                course["attempts"] = attempts
            courses.append(course)

        # OBS reports an average of 0 with 0 earned credits both for a term that
        # is not graded yet and for a term in which every course was failed.
        # The grades of this term tell the two apart.
        coefficients = [LETTER_TO_GRADE.get(normalize_grade(course["grade"]), -1.0)
                        if course["grade"] else -1.0 for course in courses]
        every_course_failed = (
            any(value == 0 for value in coefficients)
            and all(value is None or value == 0 for value in coefficients)
        )

        official_payload = {}
        reference_error = None
        if include_official:
            try:
                official_payload = self.obs.get_registration_status()
            except (NinovaError, ValueError):
                reference_error = "Official term record could not be read."
        reference = official_term_reference(
            official_payload, term_code, zero_average_is_final=every_course_failed,
        )
        if not include_official:
            reference["status"] = "not_requested"
        if reference_error:
            reference["error"] = reference_error

        # Settled on the actual grades, before any what-if grade is applied.
        ambiguity = reconcile_credit_candidates(courses, reference.get("gpa"))
        projection, unmatched_keys = resolve_projection_keys(projected_grades, courses)
        result = calculate_gpa(courses, projected_grades=projection or None)
        attach_official_reference(
            result, reference, projection=bool(projected_grades),
            ambiguous_credits=ambiguity["courses"] if ambiguity else [],
        )
        result["semester"] = resolved
        result["attempts_status"] = "from_graduation_plan" if graduation_info else "unavailable"
        if ambiguity:
            result["credit_ambiguity"] = ambiguity
        if projected_grades:
            result["projection_status"] = (
                "applied" if not result["unused_projected_courses"]
                else "partially_applied" if result["applied_projection_count"] else "not_applied"
            )
        if unmatched_keys:
            result["unmatched_projected_keys"] = unmatched_keys
            result["valid_projected_keys"] = [
                {"code": course["code"], "crn": None if course["crn"] is None else str(course["crn"])}
                for course in courses
            ]
            result["projection_warning"] = (
                "No course of this term matches: " + ", ".join(unmatched_keys)
                + ". Use a code or CRN from valid_projected_keys."
            )
        if credit_fallbacks:
            result["credit_fallback_courses"] = credit_fallbacks
            result["credit_fallback_note"] = (
                "Bu derslerin kayıt kaydında geçerli kredi bulunamadı; "
                "hesaplama için kredi ders planından alındı."
            )
        if missing_credits:
            result["credits_unresolved"] = missing_credits
            result["credits_unresolved_note"] = (
                "Bu dersler için güvenilir kredi belirlenemedi. "
                "Hesaplama kapsamı ve bilinen derslerin ortalaması ayrı değerlendirilmelidir."
            )
        if grade_fallbacks:
            result["grade_fallback_courses"] = grade_fallbacks
            result["grade_fallback_note"] = (
                "Bu derslerin notu kayıt kaydında boş geldi; not mezuniyet durumu "
                "(MezuniyetimeNeKaldi) verisinden, bu döneme ait kayıttan alındı."
            )
        return result

    def _fetch_graduation_info(self) -> dict[str, Any]:
        """Fetch and unwrap the MezuniyetimeNeKaldi payload once.

        Shared by the credit, grade and attempt lookups so obs_calculate_gpa
        hits this endpoint once per call.
        """
        try:
            graduation = self.obs.get_graduation_remaining(self.obs.default_program_id())
        except (ObsError, NinovaError):
            return {}
        info = graduation.get("mezuniyetimeNeKaldiBilgi") if isinstance(graduation, dict) else None
        return info if isinstance(info, dict) else {}

    def _plan_credit_lookup(self, info: dict[str, Any], donem_kodu: str | None = None) -> dict[str, float]:
        """Unambiguous plan credit per course code.

        Order: a row of this term, an undated plan slot, a row of another term.
        """
        from .gpa import plan_credit_table, plan_rows

        return {code: entry["credit"] for code, entry in plan_credit_table(plan_rows(info), donem_kodu).items()}

    def _plan_grade_lookup(self, info: dict[str, Any], donem_kodu: str | None) -> dict[str, str]:
        """Map normalised course code → official letter grade for one term.

        Fallback source for obs_calculate_gpa when the registered-courses
        endpoint's own harfNotu is empty. Scoped to a single term
        (``donem_kodu``, e.g. "202410") rather than returning one grade per
        code globally: a retaken course appears once per attempt with a
        different grade each time, and this must not silently return the
        wrong attempt's grade for the term actually being asked about.
        """
        if not donem_kodu:
            return {}
        from .gpa import plan_rows

        grades: dict[str, set[str]] = {}
        for row in plan_rows(info):
            if row["term"] == str(donem_kodu) and row["code"] and row["grade"]:
                grades.setdefault(row["code"], set()).add(row["grade"])
        return {code: next(iter(values)) for code, values in grades.items() if len(values) == 1}

    def calculate_target_gpa(
        self,
        current_gpa: float,
        current_credits: float,
        target_gpa: float,
        future_credits: float,
        retakes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Estimate the future average needed to reach a target cumulative GPA.

        Pure arithmetic on the supplied numbers: no login and no request.
        """
        from .gpa import calculate_target_gpa

        return calculate_target_gpa(
            current_gpa=current_gpa,
            current_credits=current_credits,
            target_gpa=target_gpa,
            future_credits=future_credits,
            retakes=retakes,
        )

    def estimate_relative_grade(
        self,
        class_scores: list[float],
        my_score: float,
        my_score_included: bool | None = None,
    ) -> dict[str, Any]:
        """Estimate a likely letter grade under İTÜ's relative-grading rules.

        Pure local computation — no OBS/network call. Uses both official
        methods from İTÜ's bağıl değerlendirme yönetmeliği: Method 1 (T-score
        against the example class-level table) and Method 2 (mean ± standard
        deviation multiples). This is an estimate, not the official grade.
        """
        from .relative_grading import estimate_relative_grade

        return estimate_relative_grade(class_scores=class_scores, my_score=my_score, my_score_included=my_score_included)

    def _obs_login_available(self) -> bool:
        """Whether an OBS read can be tried without starting a doomed login."""
        return self._obs is not None or bool(  # type: ignore[attr-defined]
            os.getenv("NINOVA_USERNAME") and os.getenv("NINOVA_PASSWORD")
        )

    def _registered_course_rows(self) -> list[dict[str, Any]]:
        """Registered courses of the student's latest term, as OBS lists them."""
        return registered_course_rows(self.obs)

    def _departments_of_crns(
        self, crns: list[str], program_type: str,
    ) -> tuple[dict[str, str], list[str], list[str]]:
        """Find the department of each CRN: ``(crn -> department, sources, notes)``.

        The archive's search index of the current term is read first, since it
        needs no login. The student's registered courses are read only for the
        CRNs it leaves open, and only when a login is possible. Neither source
        raises: what failed is returned as a note. The caller still confirms
        every CRN against the live schedule of the department found here.
        """
        from .seat_watch import term_key

        found: dict[str, str] = {}
        sources: list[str] = []
        notes: list[str] = []
        try:
            index = self.archive.get_index()
            slug = index.get("currentSlug")
            rows = self.archive.get_term_search(str(slug)) if slug else None
            active = self.obs_public.get_active_semester(program_type)
            if not rows:
                notes.append("Ders arşivinde güncel dönemin arama dizini yok.")
            elif not active or term_key(active) != term_key(index.get("currentTerm")):
                # CRNs are reused between terms, so another term's index proves nothing.
                notes.append("Ders arşivinin güncel dönemi OBS'nin aktif dönemiyle eşleşmiyor; arşiv kullanılmadı.")
            else:
                by_crn = {
                    str(row[0]).strip(): str(row[3]).strip().upper()
                    for row in rows if isinstance(row, list) and len(row) >= 4 and row[3]
                }
                found.update({crn: by_crn[crn] for crn in crns if by_crn.get(crn)})
                if found:
                    sources.append("archive_search_index")
        except (ItuArchiveError, NinovaError) as exc:
            notes.append(f"Ders arşivinden bölüm bulunamadı: {exc}")

        open_crns = [crn for crn in crns if crn not in found]
        if not open_crns:
            return found, sources, notes
        if not self._obs_login_available():
            notes.append("OBS kayıtlı dersleri okunmadı: giriş bilgisi tanımlı değil.")
            return found, sources, notes
        try:
            registered = {
                str(row.get("crn") or "").strip(): str(row.get("bransKodu") or "").strip().upper()
                for row in self._registered_course_rows()
            }
        except Exception as exc:  # noqa: BLE001 - an optional lookup must never fail the tool
            notes.append(f"OBS kayıtlı dersleri okunamadı ({type(exc).__name__}).")
            return found, sources, notes
        from_obs = {crn: registered[crn] for crn in open_crns if registered.get(crn)}
        if from_obs:
            found.update(from_obs)
            sources.append("obs_registered_courses")
        return found, sources, notes

    def check_course_conflicts(
        self,
        crns: list[str | int],
        program_type: str = "LS",
        department_code: str | None = None,
        department_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Check for time conflicts between courses by their CRNs.

        Uses the public course schedule to look up session times. Without a
        department the departments are found from the CRNs themselves.
        """
        from .schedule_utils import check_conflicts

        targets = list(dict.fromkeys(
            str(crn).strip() for crn in (crns or []) if not isinstance(crn, bool) and str(crn).strip()
        ))
        if not targets:
            raise ObsError("En az bir CRN gerekli.")
        given = department_codes or ([department_code] if department_code else [])
        explicit = list(dict.fromkeys(str(code).strip().upper() for code in given if str(code).strip()))
        if len(explicit) > 25:
            raise ObsError("Tek çağrıda en fazla 25 bölüm taranabilir.")
        supply_hint = (
            "Bu CRN'lerin bağlı olduğu bölüm kodlarını department_codes ile verin "
            "(örn. ['MAT', 'FIZ']); bölüm kodu ders kodunun harf kısmıdır."
        )
        base: dict[str, Any] = {"program_type": program_type}
        notes: list[str] = []
        if explicit:
            departments, sources = explicit, ["parameter"]
        else:
            mapping, sources, notes = self._departments_of_crns(targets, program_type)
            departments = sorted(set(mapping.values()))[:25]
            if not departments:
                return {
                    **base,
                    "status": "departments_required",
                    "missing_crns": targets,
                    "departments_scanned": [],
                    "department_codes": [],
                    "departments_resolved_by": [],
                    "resolution_notes": notes,
                    "hint": "CRN'lerin bölümü kendiliğinden bulunamadı; çakışma kontrolü yapılmadı. " + supply_hint,
                }

        all_courses: dict[str, dict[str, Any]] = {}
        scanned: list[str] = []
        for code in departments:
            try:
                schedule = self.obs_public.get_course_schedule(program_type, code)
            except ObsError as exc:
                if explicit:
                    raise
                notes.append(f"{code} programı okunamadı: {exc}")
                continue
            scanned.append(code)
            for course in schedule.get("courses") or []:
                crn_value = str(course.get("crn") or "").strip()
                if crn_value:
                    all_courses.setdefault(crn_value, course)
        matched = [all_courses[crn] for crn in targets if crn in all_courses]
        missing = [crn for crn in targets if crn not in all_courses]
        base.update({
            "department_codes": scanned,
            "departments_scanned": scanned,
            "departments_resolved_by": sources,
            "missing_crns": missing,
        })
        if notes:
            base["resolution_notes"] = notes
        if not matched:
            return {
                **base,
                "status": "not_found",
                "hint": (
                    f"Verilen CRN'lerin hiçbiri taranan bölümlerde ({', '.join(scanned) or 'yok'}) yok; "
                    "çakışma kontrolü yapılmadı. " + supply_hint
                ),
            }

        result = check_conflicts(matched)
        result.update(base)
        result["status"] = "partial" if missing else "ok"
        if missing:
            result["hint"] = (
                f"Çakışma kontrolü yalnızca bulunan {len(matched)} CRN'i kapsıyor; "
                f"{', '.join(missing)} taranan bölümlerde yok. " + supply_hint
            )
        return result

    def list_degree_faculties(self) -> dict[str, Any]:
        """List official faculty/unit ids used by public OBS degree plans."""
        faculties = self.obs_public.list_degree_faculties()
        return {
            "count": len(faculties),
            "faculties": faculties,
            "source": "obs.itu.edu.tr/public/DersPlan",
            "untrusted_external_content": True,
        }

    def list_degree_programs(
        self,
        faculty_id: int,
        plan_type: str = "lisans",
    ) -> dict[str, Any]:
        """List official OBS degree-program codes for a faculty."""
        programs = self.obs_public.list_degree_programs(faculty_id, plan_type)
        return {
            "faculty_id": faculty_id,
            "plan_type": plan_type,
            "count": len(programs),
            "programs": programs,
            "source": "obs.itu.edu.tr/public/DersPlan",
            "untrusted_external_content": True,
        }

    def build_degree_plan(
        self,
        faculty_id: int,
        program_code: str,
        plan_type: str = "lisans",
        plan_id: int | None = None,
        latest: bool = True,
    ) -> dict[str, Any]:
        """Read an official semester-by-semester OBS degree plan."""
        catalog = self.obs_public.list_degree_plans(
            faculty_id=faculty_id,
            program_code=program_code,
            plan_type=plan_type,
        )
        selected_id = plan_id
        if selected_id is None and latest and catalog.get("plans"):
            selected_id = catalog["plans"][-1].get("plan_id")
        result: dict[str, Any] = {
            "faculty_id": faculty_id,
            "program_code": program_code.upper(),
            "plan_type": plan_type,
            "available_plans": catalog.get("plans") or [],
            "selected_plan_id": selected_id,
            "source": "obs.itu.edu.tr/public/DersPlan",
            "untrusted_external_content": True,
        }
        if selected_id is not None:
            result["plan"] = self.obs_public.get_degree_plan(selected_id)
        else:
            result["message"] = "Bir plan_id seçin veya latest=true kullanın."
        return result

    def explain_course_eligibility(
        self,
        course_code: str,
        completed_courses: list[str] | None = None,
        use_obs_history: bool = False,
        completed_credits: float | None = None,
        class_year: int | None = None,
        program_type: str = "LS",
    ) -> dict[str, Any]:
        """Explain prerequisite eligibility against the official OBS rule table."""
        from .archive import split_course_code
        from .prerequisites import compare_required_course_sets, describe_tree, evaluate_tree

        try:
            branch, number = split_course_code(course_code)
        except ValueError as exc:
            raise ObsError(str(exc)) from exc
        canonical = f"{branch} {number}"

        # Grades are tracked alongside completion because the official rules set
        # per-course minimums (CEN 4902E wants CEN 4901E at BB, not just a pass).
        completed: dict[str, str | None] = {}
        for entry in completed_courses or []:
            raw = str(entry).strip()
            if not raw:
                continue
            code_part, _, grade_part = raw.partition(":")
            try:
                normalized = f"{split_course_code(code_part)[0]} {split_course_code(code_part)[1]}"
            except ValueError:
                continue
            completed[normalized] = grade_part.strip().upper() or None

        obs_credits: float | None = None
        # Courses registered this term without a grade yet: read only with the
        # student's own history, and never a reason to fail.
        taking_now: list[dict[str, Any]] = []
        in_progress_status = "not_requested"
        plan_credit: dict[str, float] = {}
        if use_obs_history:
            from .gpa import plan_credit_table, plan_rows
            from .graduation import completed_course_history

            graduation = self.obs.get_graduation_remaining(self.obs.default_program_id())
            info = graduation.get("mezuniyetimeNeKaldiBilgi") or {}
            completed.update(completed_course_history(info))
            obs_credits = info.get("metKrediTotal")
            if completed_credits is None:
                completed_credits = obs_credits
            taking_now, taking_note = courses_in_progress(self.obs)
            in_progress_status = "unavailable" if taking_note else "read"
            plan_credit = {
                code: entry["credit"] for code, entry in plan_credit_table(plan_rows(info), None).items()
            }

        rules = self.obs_public.get_branch_prerequisites(branch)
        rule = (rules.get("rules") or {}).get(canonical)
        obs_tree = rule.get("requirement_tree") if rule else None
        obs_credit_requirement = rule.get("credit_requirement") if rule else None

        def cross_check_against_secondary_source() -> dict[str, Any]:
            """Diff the OBS-derived rule against an independent community dataset.

            Purely informational: OBS stays authoritative regardless of the
            outcome here, and any fetch or parse failure is reported as
            'unavailable' rather than raised, since a broken third-party
            comparison must never block the primary OBS-based answer.
            """
            try:
                secondary = self.prereq_crosscheck.get_course_prerequisite_tree(canonical)
            except CrossCheckDataError as exc:
                return {
                    "available": False,
                    "source": "üçüncü taraf topluluk veri seti (resmi değil)",
                    "note": f"Çapraz doğrulama kaynağına erişilemedi: {exc}",
                }
            if secondary is None:
                return {
                    "available": False,
                    "source": "üçüncü taraf topluluk veri seti (resmi değil)",
                    "note": f"{canonical} çapraz doğrulama veri setinde yok.",
                }
            comparison = compare_required_course_sets(obs_tree, secondary["tree"])
            credit_matches = obs_credit_requirement == secondary.get("credit_requirement")
            return {
                "available": True,
                "source": "üçüncü taraf topluluk veri seti (resmi değil; yalnızca çapraz doğrulama)",
                "agrees_with_obs": comparison["matches"] and credit_matches,
                "only_in_obs": comparison["only_in_first"],
                "only_in_secondary_source": comparison["only_in_second"],
                "grade_mismatches": comparison["grade_mismatches"],
                "credit_requirement_secondary_source": secondary.get("credit_requirement"),
                "credit_requirement_matches": credit_matches,
            }

        def archive_seasonality_note() -> dict[str, Any] | None:
            """Attach the archive's seasonality read for this course, if any.

            A course can be prerequisite-eligible and still only worth planning
            for one term a year; this surfaces that even when it isn't the
            question that was asked. Failure or absence from the archive is
            silent — the archive doesn't cover every course, and that must
            never look like a claim about the course's actual schedule.
            """
            from .archive import seasonality as _seasonality
            from .archive import seasonality_sentence

            try:
                history = self.archive.get_course_history(branch)
            except ItuArchiveError:
                return None
            entry = history.get(canonical)
            if entry is None:
                return None
            summary = _seasonality(entry.get("terms") or [])
            if summary["total_terms"] == 0:
                return None
            return {**summary, "note": seasonality_sentence(canonical, summary)}

        result: dict[str, Any] = {
            "course_code": canonical,
            "completed_courses": sorted(completed),
            "completed_grades": completed,
            "completed_credits": completed_credits,
            "used_obs_history": use_obs_history,
            "program_type": program_type,
            "source": rules.get("url"),
            "untrusted_external_content": True,
        }

        if rule is None:
            if not rules.get("table_parsed"):
                # An unreadable table is not evidence of anything. Say so rather
                # than reporting a confident "no prerequisites".
                result.update({
                    "prerequisite_status": "unknown",
                    "eligible": None,
                    "explanation": (
                        f"{branch} önşart tablosu ayrıştırılamadı; {canonical} için ön şart "
                        "durumu doğrulanamıyor."
                    ),
                })
                result["cross_check"] = cross_check_against_secondary_source()
                result["archive_seasonality"] = archive_seasonality_note()
                return result
            result.update({
                "prerequisite_status": "no_prerequisites",
                "eligible": True,
                "explanation": (
                    f"{canonical} resmî {branch} önşart tablosunda yer almıyor, "
                    "yani ders ön şartı yok."
                ),
            })
            result["cross_check"] = cross_check_against_secondary_source()
            result["archive_seasonality"] = archive_seasonality_note()
            return result

        tree = rule.get("requirement_tree")
        verdict = evaluate_tree(tree, completed)
        taking_keys = {course["key"] for course in taking_now}
        unmet = unmet_requirements(tree, completed, taking_keys)
        blockers: list[str] = [item["sentence"] for item in unmet]
        if not verdict["satisfied"] and not blockers:
            blockers.append(verdict["reason"])
        taking_prerequisites = sorted({
            code for item in unmet
            for code in (item["in_progress"] if "any_of" in item else [item["course"]] * bool(item["in_progress"]))
        })

        # Credits a pass this term would add: a course already completed adds none.
        credits_in_progress: float | None = None
        credits_in_progress_complete = True
        if in_progress_status == "read":
            adding = [course for course in taking_now if course["key"] not in completed]
            sizes = [
                course["credit"] if course["credit"] is not None else plan_credit.get(course["key"])
                for course in adding
            ]
            credits_in_progress = round(math.fsum(size for size in sizes if size is not None), 2)
            credits_in_progress_complete = all(size is not None for size in sizes)
        credits_after: float | None = None
        if credits_in_progress is not None and completed_credits is not None:
            try:
                credits_after = round(float(completed_credits) + credits_in_progress, 2)
            except (TypeError, ValueError):
                credits_after = None

        credit_requirement = rule.get("credit_requirement")
        credit_met: bool | None = None
        credit_met_after: bool | None = None
        if credit_requirement is not None:
            if completed_credits is None:
                credit_met = None
                blockers.append(
                    f"{credit_requirement} kredi şartı var; tamamlanan kredi bilinmiyor "
                    "(completed_credits verin veya use_obs_history=true kullanın)."
                )
            else:
                credit_met = completed_credits >= credit_requirement
                if credits_after is not None:
                    credit_met_after = credits_after >= credit_requirement
                    if not credit_met_after and not credits_in_progress_complete:
                        credit_met_after = None  # a registered course without a readable credit
                if not credit_met:
                    blockers.append(
                        f"Kredi şartı {credit_requirement}, tamamlanan {completed_credits}"
                        + (f"; bu dönem alınan {credits_in_progress:g} kredi geçilirse {credits_after:g} olur."
                           if credits_after is not None and credits_in_progress else ".")
                    )

        eligible: bool | None
        if verdict["satisfied"] is False or credit_met is False:
            eligible = False
        elif verdict["satisfied"] is None or (credit_requirement is not None and credit_met is None):
            eligible = None
        else:
            eligible = True

        # What the answer becomes if every course taken now is passed. The
        # verdict above is left as it is.
        eligible_after: bool | None = None
        after_note: str | None = None
        if eligible is True:
            eligible_after = True
        elif in_progress_status == "read":
            passed = {**completed, **dict.fromkeys(taking_prerequisites, "AA")}
            tree_after = evaluate_tree(tree, passed)["satisfied"]
            credit_after_ok = True if credit_requirement is None else credit_met_after
            if tree_after is False or credit_after_ok is False:
                eligible_after = False
            elif tree_after is None or credit_after_ok is None:
                eligible_after = None
            else:
                eligible_after = True
            graded = [
                f"{item['course']} için en az {item['min_grade']}"
                for item in unmet if "course" in item and item["in_progress"] and item["min_grade"]
            ]
            if graded and eligible_after:
                after_note = "Yalnızca geçmek yetmez: " + ", ".join(graded) + " gerekiyor."

        missing_courses = [
            " VEYA ".join(item["any_of"]) if "any_of" in item else item["course"]
            for item in unmet if item["state"] != "unverified"
        ]
        parts = blockers or ["Tüm ön şartlar karşılanıyor."]
        result.update({
            "prerequisite_status": "has_prerequisites",
            "eligible": eligible,
            "explanation": " ".join(parts),
            "explanation_parts": parts,
            "requirement": describe_tree(tree),
            "requirement_tree": tree,
            "minimum_grades": rule.get("minimum_grades"),
            "missing_courses": missing_courses,
            "missing_requirements": [
                {key: value for key, value in item.items() if key != "sentence"} for item in unmet
            ],
            "unknown_courses": verdict.get("unknown", []),
            "credit_requirement": credit_requirement,
            "credit_requirement_met": credit_met,
            "in_progress_status": in_progress_status,
            "in_progress_prerequisites": taking_prerequisites,
            "eligible_if_in_progress_passed": eligible_after,
            "credits_in_progress": credits_in_progress,
            "credits_if_in_progress_passed": credits_after,
        })
        if after_note:
            result["eligible_if_in_progress_passed_note"] = after_note
        result["cross_check"] = cross_check_against_secondary_source()
        result["archive_seasonality"] = archive_seasonality_note()
        return result

    def plan_remaining_courses(
        self,
        program_id: int | None = None,
        limit_terms: int = 6,
    ) -> dict[str, Any]:
        """Combine graduation-remaining required courses with archive scheduling history.

        The remaining required courses are split in two: the ones the student
        is taking this term (``in_progress``) and the ones still to plan. Only
        the second group gets an archive lookup: which season it opens in, who
        has taught it, and a one-line scheduling recommendation. Open elective
        slots are listed with their credits.
        """
        from .archive import recommend_course_timing, seasonality, who_taught

        pid = program_id if program_id is not None else self.obs.default_program_id()
        graduation = self.obs.get_graduation_remaining(pid)
        summary = summarize_graduation_plan(graduation)
        remaining = summary.get("remaining_required_courses") or []
        registered, registered_note = courses_in_progress(self.obs)
        matches, other_registered = match_courses_in_progress(remaining, registered)

        in_progress: list[dict[str, Any]] = []
        plans: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        to_plan_credits: list[float] = []
        for index, course in enumerate(remaining):
            code = course.get("course_code")
            base = {
                "course_code": code,
                "course_name": course.get("course_name"),
                "credit": course.get("credit"),
                "semester": course.get("semester"),
            }
            if index in matches:
                in_progress.append({**base, "in_progress": True, **matches[index]})
                continue
            to_plan_credits.append(float(course.get("credit") or 0.0))
            try:
                canonical, entry = self._archive_course_entry(str(code or ""), course.get("course_name"))
            except ItuArchiveError as exc:
                unresolved.append({**base, "in_progress": False, "reason": str(exc)})
                continue

            seasonality_summary = seasonality(entry.get("terms") or [])
            taught = who_taught(entry, limit_terms=limit_terms)
            top_instructors = taught["instructors"][:3]
            plans.append({
                **base,
                "course_code": canonical,
                "in_progress": False,
                "seasonality": seasonality_summary,
                "top_instructors": top_instructors,
                "recommendation": recommend_course_timing(canonical, seasonality_summary, top_instructors),
            })

        slots = [
            {"slot": slot.get("slot"), "credit": slot.get("credit"), "semester": slot.get("semester")}
            for slot in summary.get("open_elective_slots") or []
        ]
        slot_credits = round(math.fsum(float(slot["credit"] or 0.0) for slot in slots), 2)
        result: dict[str, Any] = {
            "program_id": pid,
            "remaining_course_count": len(remaining),
            "in_progress_count": len(in_progress),
            "to_plan_count": len(plans) + len(unresolved),
            "resolved_count": len(plans),
            "in_progress": in_progress,
            "plans": plans,
            "unresolved_courses": unresolved,
            "open_elective_slots": slots,
            "open_elective_slot_count": len(slots),
            "open_elective_credits": slot_credits,
            "credits": {
                "in_progress": round(math.fsum(float(item["credit"] or 0.0) for item in in_progress), 2),
                "to_plan": round(math.fsum(to_plan_credits), 2),
                "open_elective_slots": slot_credits,
            },
            "other_registered_courses": other_registered,
            "registered_courses_status": "unavailable" if registered_note else "read",
            "note": (
                "in_progress: bu dönem alınan zorunlu dersler; geçilirse kalan listesinden çıkar. "
                "plans ve unresolved_courses: henüz planlanması gereken zorunlu dersler. "
                "open_elective_slots: boş seçmeli slotlar. other_registered_courses içindeki bir ders "
                "bu slotlardan birini dolduruyor olabilir; OBS bunu not girilmeden göstermez."
            ),
            "untrusted_external_content": True,
        }
        if registered_note:
            result["registered_courses_note"] = registered_note
        return result


def registered_course_rows(obs: Any) -> list[dict[str, Any]]:
    """Registered courses of the student's latest term, as OBS lists them."""
    resolved = obs.resolve_semester(None)
    payload = obs.list_registered_courses(resolved["akademikDonemId"])
    if not isinstance(payload, dict) or payload.get("statusCode") not in (None, 0):
        raise ObsError("Kayıtlı ders listesi okunamadı.")
    rows = payload.get("kayitSinifResultList")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ObsError("Kayıtlı ders listesi beklenmeyen biçimde.")
    return [row for row in rows if isinstance(row, dict)]


def courses_in_progress(obs: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Courses the student is taking now: ``(courses, note)``.

    Registered courses of the latest term that have no letter grade yet, in
    the shape ``gpa_simulation`` reads them (key, code, crns, name, names,
    credit). An unreadable list gives no courses and a note, never an
    exception.
    """
    from .gpa_simulation import _read_registered

    try:
        rows = registered_course_rows(obs)
    except Exception as exc:  # noqa: BLE001 - an optional lookup must never fail the tool
        return [], f"Bu dönemin kayıtlı dersleri okunamadı ({type(exc).__name__}); devam eden dersler işaretlenmedi."
    return _read_registered([row for row in rows if not str(row.get("harfNotu") or "").strip()]), None


def unmet_requirements(
    tree: Any, completed: dict[str, str | None], in_progress: set[str],
) -> list[dict[str, Any]]:
    """The unmet parts of a prerequisite tree, one entry per requirement.

    A course the rule needs outright is one entry. The alternatives of an OR
    group are one entry together, since any one of them is enough. Each entry
    carries the sentence that explains it. ``state`` is ``not_taken``,
    ``grade_too_low`` or ``unverified`` for a course, ``unmet`` or
    ``unverified`` for a group.
    """
    from .archive import normalize_course_code
    from .prerequisites import describe_tree, evaluate_tree, flatten_courses

    if not isinstance(tree, dict):
        return []
    if tree.get("type") == "and" and isinstance(tree.get("operands"), list):
        return [item for operand in tree["operands"] for item in unmet_requirements(operand, completed, in_progress)]
    verdict = evaluate_tree(tree, completed)
    if verdict["satisfied"] is True:
        return []

    def taking(node: Any) -> list[str]:
        codes = []
        for course in flatten_courses(node):
            try:
                code = normalize_course_code(str(course.get("code") or ""))
            except ValueError:
                continue
            if code in in_progress and evaluate_tree(course, completed)["satisfied"] is not True:
                codes.append(code)
        return sorted(set(codes))

    now = taking(tree)
    now_text = "".join(f" {code} bu dönem alınıyor; henüz tamamlanmadı." for code in now)
    if tree.get("type") == "course":
        try:
            code = normalize_course_code(str(tree.get("code") or ""))
        except ValueError:
            return [{"course": tree.get("code"), "min_grade": tree.get("min_grade"), "state": "unverified",
                     "in_progress": False, "sentence": verdict["reason"]}]
        minimum = tree.get("min_grade")
        if verdict["satisfied"] is None:
            state, sentence = "unverified", verdict["reason"]
        elif code not in completed:
            state = "not_taken"
            needs = f" (en az {minimum} gerekiyor)" if minimum else ""
            sentence = (
                f"{code} bu dönem alınıyor; henüz tamamlanmadı{needs}." if now
                else f"{code} alınmamış{needs}."
            )
        else:
            state, sentence = "grade_too_low", verdict["reason"] + now_text
        return [{"course": code, "min_grade": minimum, "state": state, "in_progress": bool(now), "sentence": sentence}]

    options = [describe_tree(operand) for operand in tree.get("operands") or []]
    options = [option for option in options if option]
    listed = " VEYA ".join(options)
    if verdict["satisfied"] is None:
        state = "unverified"
        sentence = f"Şu seçeneklerden birinin sağlandığı doğrulanamıyor (biri yeterli): {listed}.{now_text}"
    else:
        state = "unmet"
        sentence = f"Şu seçeneklerden hiçbiri sağlanmıyor (biri yeterli): {listed}.{now_text}"
    return [{"any_of": options, "state": state, "in_progress": now, "sentence": sentence}]


def match_courses_in_progress(
    remaining: list[dict[str, Any]], registered: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Pair remaining plan courses with the courses registered now.

    ``remaining`` are ``remaining_required_courses`` rows of the graduation
    summary; ``registered`` is what ``gpa_simulation._read_registered``
    returns. A pair is the same course code, or the likely-equivalent rule of
    the GPA simulation for a changed department code: the same normalized
    name and the same credit, with exactly one candidate on each side. When
    the registration carries no credit the name alone decides and the match
    says so. Returns ``{index in remaining: match}`` and the registered
    courses that matched nothing.
    """
    from .gpa import course_key
    from .gpa_simulation import _name_key, _same

    def describe(course: dict[str, Any], basis: str, plan_code: Any) -> dict[str, Any]:
        match: dict[str, Any] = {"crn": ", ".join(course["crns"]) or None, "matched_by": basis}
        if basis != "code":
            match["registered_as"] = course["code"]
            match["match_note"] = (
                f"{plan_code} plandaki kodla kayıtlı değil; aynı adı"
                + (" ve krediyi" if basis == "name_and_credit" else "")
                + f" taşıyan {course['code']} bu dönem alınıyor. Eşdeğerlik OBS'de doğrulanmadı."
            )
        return match

    by_key = {course["key"]: course for course in registered}
    matches: dict[int, dict[str, Any]] = {}
    used: set[str] = set()
    for index, course in enumerate(remaining):
        key = course_key(course.get("course_code")) if course.get("course_code") else ""
        if key and key in by_key and key not in used:
            matches[index] = describe(by_key[key], "code", course.get("course_code"))
            used.add(key)

    def candidates(course: dict[str, Any]) -> list[dict[str, Any]]:
        name = _name_key(course.get("course_name"))
        credit = course.get("credit")
        return [
            other for other in registered
            if other["key"] not in used and name and name in other["names"]
            and (other["credit"] is None or _same(other["credit"], float(credit) if credit else None))
        ]

    open_indexes = [index for index in range(len(remaining)) if index not in matches]
    found = {index: candidates(remaining[index]) for index in open_indexes}
    for index, options in found.items():
        if len(options) != 1:
            continue
        other = options[0]
        rivals = [i for i, rival_options in found.items() if any(o["key"] == other["key"] for o in rival_options)]
        if len(rivals) == 1:
            basis = "name_and_credit" if other["credit"] is not None else "name"
            matches[index] = describe(other, basis, remaining[index].get("course_code"))
            used.add(other["key"])
    others = [
        {"code": course["code"], "crn": ", ".join(course["crns"]) or None, "name": course["name"]}
        for course in registered if course["key"] not in used
    ]
    return matches, others


def _publish_parameter_schema(function: Any, **annotations: Any) -> None:
    """Give tool parameters a published type and description.

    The server copies a tool's signature without evaluating its annotations, so
    an ``Annotated`` written in the ``def`` line would reach FastMCP as text it
    cannot resolve. A stored signature with real annotation objects is copied
    as is. The return annotation is left untouched, which keeps the output
    schema unchanged.
    """
    signature = inspect.signature(function)
    function.__signature__ = signature.replace(parameters=[
        parameter.replace(annotation=annotations.get(name, parameter.annotation))
        for name, parameter in signature.parameters.items()
    ])


try:
    from pydantic import ConfigDict, Field
    from typing_extensions import TypedDict
except ImportError:  # pragma: no cover - pydantic ships with the MCP SDK
    pass
else:
    from .tool_metadata import planning as _descriptions

    # Functional form: the field types are real objects, not postponed text.
    Retake = TypedDict("Retake", {
        "credit": Annotated[float, Field(gt=0, description=_descriptions.TARGET_RETAKE_CREDIT_DESCRIPTION)],
        "old_grade": Annotated[str, Field(description=_descriptions.TARGET_RETAKE_OLD_GRADE_DESCRIPTION)],
    })
    Retake.__pydantic_config__ = ConfigDict(extra="forbid")  # type: ignore[attr-defined]

    _publish_parameter_schema(
        PlanningToolsMixin.calculate_target_gpa,
        current_gpa=Annotated[float, Field(
            ge=0, le=4, description=_descriptions.TARGET_CURRENT_GPA_DESCRIPTION,
        )],
        current_credits=Annotated[float, Field(
            ge=0, description=_descriptions.TARGET_CURRENT_CREDITS_DESCRIPTION,
        )],
        target_gpa=Annotated[float, Field(
            ge=0, le=4, description=_descriptions.TARGET_TARGET_GPA_DESCRIPTION,
        )],
        future_credits=Annotated[float, Field(
            gt=0, description=_descriptions.TARGET_FUTURE_CREDITS_DESCRIPTION,
        )],
        retakes=Annotated[list[Retake] | None, Field(
            description=_descriptions.TARGET_RETAKES_DESCRIPTION,
        )],
    )
    _publish_parameter_schema(
        PlanningToolsMixin.obs_calculate_gpa,
        semester=Annotated[str | int | float | None, Field(description=(
            "Term to calculate. Accepted forms: the term code as a number or a string "
            '(202410 or "202410"), the internal term id (akademikDonemId from '
            'obs_list_semesters), or the term name ("2024-2025 Güz"). '
            "Omit it for the latest term."
        ))],
        projected_grades=Annotated[dict[str, str] | None, Field(description=(
            "What-if letter grades for courses of this term. Each key is a course code "
            "(spacing and case do not matter) or a CRN, each value an İTÜ letter grade. "
            'Example: {"BLG 223E": "AA", "21547": "BB"}. Keys that match no course of the '
            "term come back in unmatched_projected_keys with the valid keys."
        ))],
    )
