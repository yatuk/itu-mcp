"""Planning tools: GPA, relative grade, conflicts, degree plans, eligibility, remaining courses."""

from __future__ import annotations

import math
from typing import Any

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
        semester: str | None = None,
        projected_grades: dict[str, str] | None = None,
        include_official: bool = True,
    ) -> dict[str, Any]:
        """Calculate a term GPA estimate and compare with the official term record.

        If ``projected_grades`` is given (e.g. ``{"BLG 223E": "AA", ...}``),
        those override the actual grade — useful for "what-if" scenarios.
        """
        from .gpa import calculate_gpa
        from .archive import normalize_course_code

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
        #
        # Separately, observed on at least one account: this endpoint's
        # harfNotu comes back None for every course in every term, including
        # terms years in the past — not just "not graded yet". When that
        # happens the tool would otherwise silently compute gpa=None every
        # time. Fall back to the same graduation-remaining payload for
        # grades too, scoped to this term's donemKodu: a retaken course has
        # one entry per attempt with a different grade each time, so a
        # code-only lookup
        # would risk picking the wrong attempt's grade here.
        graduation_info = self._fetch_graduation_info()
        plan_credits = self._plan_credit_lookup(graduation_info, resolved.get("donemKodu"))
        plan_grades = self._plan_grade_lookup(graduation_info, resolved.get("donemKodu"))

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
            credit = item.get("kredi")
            credit_source = "registration"
            try:
                numeric = float(str(credit).replace(",", "."))
            except (TypeError, ValueError):
                numeric = 0.0
            if isinstance(credit, bool) or not math.isfinite(numeric) or numeric <= 0:
                fallback = plan_credits.get(code_key)
                if fallback:
                    credit = fallback
                    credit_source = "degree_plan"
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

            courses.append({
                "code": code,
                "name": item.get("dersAdiTR") or item.get("dersAdiEN", ""),
                "credit": credit,
                "recorded_credit": item.get("kredi"),
                "plan_credit": plan_credits.get(code_key),
                "credit_source": credit_source,
                "grade": grade,
                "grade_source": grade_source,
                "crn": item.get("crn"),
            })

        result = calculate_gpa(courses, projected_grades=projected_grades)
        from .gpa_reference import attach_official_reference, official_term_reference

        official_payload = {}
        reference_error = None
        if include_official:
            try:
                official_payload = self.obs.get_registration_status()
            except (NinovaError, ValueError):
                reference_error = "Official term record could not be read."
        reference = official_term_reference(official_payload, resolved.get("donemKodu"))
        if not include_official:
            reference["status"] = "not_requested"
        if reference_error:
            reference["error"] = reference_error
        attach_official_reference(result, reference, projection=bool(projected_grades))
        result["semester"] = resolved
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

        Shared by _plan_credit_lookup and _plan_grade_lookup so
        obs_calculate_gpa hits this endpoint once per call, not twice.
        """
        try:
            graduation = self.obs.get_graduation_remaining(self.obs.default_program_id())
        except (ObsError, NinovaError):
            return {}
        return graduation.get("mezuniyetimeNeKaldiBilgi") or {}

    def _plan_credit_lookup(self, info: dict[str, Any], donem_kodu: str | None = None) -> dict[str, float]:
        """Use unambiguous plan credit for this attempt or an undated plan slot."""
        from .archive import normalize_course_code

        exact: dict[str, set[float]] = {}
        undated: dict[str, set[float]] = {}
        for bucket in ("checkMetMezuniyetList", "unusedSinifOgrenciList"):
            for item in info.get(bucket) or []:
                try:
                    code = normalize_course_code(str(item.get("bransKodu") or ""))
                except ValueError:
                    continue
                term = str(item.get("donem") or "")
                if donem_kodu and term and term != str(donem_kodu):
                    continue
                credit = item.get("kredisiDec")
                if credit is None:
                    credit = item.get("kredisi")
                try:
                    numeric = float(str(credit).replace(",", "."))
                except (TypeError, ValueError):
                    continue
                if not isinstance(credit, bool) and math.isfinite(numeric) and numeric > 0:
                    target = exact if term else undated
                    target.setdefault(code, set()).add(numeric)
        return {code: next(iter(values)) for code in exact.keys() | undated.keys()
                if len(values := exact.get(code, undated.get(code, set()))) == 1}

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
        from .archive import normalize_course_code

        grades: dict[str, set[str]] = {}
        for bucket in ("checkMetMezuniyetList", "unusedSinifOgrenciList"):
            for item in info.get(bucket) or []:
                if str(item.get("donem") or "") != str(donem_kodu):
                    continue
                try:
                    code = normalize_course_code(str(item.get("bransKodu") or ""))
                except ValueError:
                    continue
                grade = str(item.get("harfNotu") or "").strip().upper()
                if code and grade:
                    grades.setdefault(code, set()).add(grade)
        return {code: next(iter(values)) for code, values in grades.items() if len(values) == 1}

    def calculate_target_gpa(
        self,
        current_gpa: float,
        current_credits: float,
        target_gpa: float,
        future_credits: float,
    ) -> dict[str, Any]:
        """Estimate the future average needed to reach a target cumulative GPA."""
        from .gpa import calculate_target_gpa

        return calculate_target_gpa(
            current_gpa=current_gpa,
            current_credits=current_credits,
            target_gpa=target_gpa,
            future_credits=future_credits,
        )

    def estimate_relative_grade(
        self,
        class_scores: list[float],
        my_score: float,
    ) -> dict[str, Any]:
        """Estimate a likely letter grade under İTÜ's relative-grading rules.

        Pure local computation — no OBS/network call. Uses both official
        methods from İTÜ's bağıl değerlendirme yönetmeliği: Method 1 (T-score
        against the example class-level table) and Method 2 (mean ± standard
        deviation multiples). This is an estimate, not the official grade.
        """
        from .relative_grading import estimate_relative_grade

        return estimate_relative_grade(class_scores=class_scores, my_score=my_score)

    def check_course_conflicts(
        self,
        crns: list[str],
        program_type: str = "LS",
        department_code: str = "BLG",
        department_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Check for time conflicts between courses by their CRNs.

        Uses the public course schedule to look up session times.
        """
        from .schedule_utils import check_conflicts

        departments = department_codes or [department_code]
        departments = list(dict.fromkeys(code.strip().upper() for code in departments if code.strip()))
        if not departments:
            raise ObsError("En az bir department_code gerekli.")
        if len(departments) > 25:
            raise ObsError("Tek çağrıda en fazla 25 bölüm taranabilir.")
        schedules = [self.obs_public.get_course_schedule(program_type, code) for code in departments]
        all_courses: list[dict[str, Any]] = []
        seen: set[str] = set()
        for schedule in schedules:
            for course in schedule.get("courses") or []:
                crn_value = str(course.get("crn") or "")
                if crn_value and crn_value not in seen:
                    all_courses.append(course)
                    seen.add(crn_value)
        target_crns = {str(c).strip() for c in crns}
        matched = [c for c in all_courses if str(c.get("crn")) in target_crns]

        if not matched:
            return {
                "error": f"Hiçbir CRN bulunamadı. Aranan: {crns}. "
                         f"Mevcut: {', '.join(str(c.get('crn')) for c in all_courses[:20])}"
            }

        result = check_conflicts(matched)
        result["program_type"] = program_type
        result["department_codes"] = departments
        result["missing_crns"] = sorted(target_crns - {str(course.get("crn")) for course in matched})
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
        if use_obs_history:
            from .graduation import completed_course_history

            graduation = self.obs.get_graduation_remaining(self.obs.default_program_id())
            info = graduation.get("mezuniyetimeNeKaldiBilgi") or {}
            completed.update(completed_course_history(info))
            obs_credits = info.get("metKrediTotal")
            if completed_credits is None:
                completed_credits = obs_credits

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
            note = None
            if summary["only_season"]:
                note = (
                    f"{canonical} arşivde yalnızca {summary['only_season']} döneminde "
                    f"açılmış ({summary['total_terms']} dönem)."
                )
            return {**summary, "note": note}

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

        verdict = evaluate_tree(rule.get("requirement_tree"), completed)
        blockers: list[str] = []
        if not verdict["satisfied"]:
            blockers.append(verdict["reason"])

        credit_requirement = rule.get("credit_requirement")
        credit_met: bool | None = None
        if credit_requirement is not None:
            if completed_credits is None:
                credit_met = None
                blockers.append(
                    f"{credit_requirement} kredi şartı var; tamamlanan kredi bilinmiyor "
                    "(completed_credits verin veya use_obs_history=true kullanın)."
                )
            else:
                credit_met = completed_credits >= credit_requirement
                if not credit_met:
                    blockers.append(
                        f"Kredi şartı {credit_requirement}, tamamlanan {completed_credits}."
                    )

        eligible: bool | None
        if verdict["satisfied"] is False or credit_met is False:
            eligible = False
        elif verdict["satisfied"] is None or (credit_requirement is not None and credit_met is None):
            eligible = None
        else:
            eligible = True

        result.update({
            "prerequisite_status": "has_prerequisites",
            "eligible": eligible,
            "explanation": " ".join(blockers) if blockers else "Tüm ön şartlar karşılanıyor.",
            "requirement": describe_tree(rule.get("requirement_tree")),
            "requirement_tree": rule.get("requirement_tree"),
            "minimum_grades": rule.get("minimum_grades"),
            "missing_courses": verdict.get("missing", []),
            "unknown_courses": verdict.get("unknown", []),
            "credit_requirement": credit_requirement,
            "credit_requirement_met": credit_met,
        })
        result["cross_check"] = cross_check_against_secondary_source()
        result["archive_seasonality"] = archive_seasonality_note()
        return result

    def plan_remaining_courses(
        self,
        program_id: int | None = None,
        limit_terms: int = 6,
    ) -> dict[str, Any]:
        """Combine graduation-remaining required courses with archive scheduling history.

        For every course still needed to graduate, looks up which season it
        opens in and who has taught it, and produces a one-line scheduling
        recommendation per course — the manual "call archive_course_history
        once per remaining course and mentally merge the results" workflow,
        done in one call.
        """
        from .archive import recommend_course_timing, seasonality, who_taught

        pid = program_id if program_id is not None else self.obs.default_program_id()
        graduation = self.obs.get_graduation_remaining(pid)
        summary = summarize_graduation_plan(graduation)
        remaining = summary.get("remaining_required_courses") or []

        plans: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for course in remaining:
            code = course.get("course_code")
            if not code:
                # Named electives ("8th Sems. Elect. Course I (MT)") have no
                # fixed course code and can't be looked up in the archive.
                unresolved.append({
                    "course_code": None,
                    "course_name": course.get("course_name"),
                    "reason": "Bu bir seçmeli slot; sabit bir ders kodu yok.",
                })
                continue
            try:
                canonical, entry = self._archive_course_entry(code)
            except ItuArchiveError as exc:
                unresolved.append({"course_code": code, "reason": str(exc)})
                continue

            seasonality_summary = seasonality(entry.get("terms") or [])
            taught = who_taught(entry, limit_terms=limit_terms)
            top_instructors = taught["instructors"][:3]
            plans.append({
                "course_code": canonical,
                "course_name": course.get("course_name"),
                "credit": course.get("credit"),
                "semester": course.get("semester"),
                "seasonality": seasonality_summary,
                "top_instructors": top_instructors,
                "recommendation": recommend_course_timing(canonical, seasonality_summary, top_instructors),
            })

        return {
            "program_id": pid,
            "remaining_course_count": len(remaining),
            "resolved_count": len(plans),
            "unresolved_courses": unresolved,
            "plans": plans,
            "untrusted_external_content": True,
        }
