"""Course archive history tools: terms, offerings, instructors, sections, fill rates.

The dataset tools added later (grade distribution, catalog, exam schedule) live in
``archive_tools``.
"""

from __future__ import annotations

from typing import Any

from .archive_client import ItuArchiveClient, ItuArchiveError


class ArchiveHistoryToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``archive``."""

    archive: ItuArchiveClient

    def _archive_course_entry(
        self, course_code: str, course_name: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Resolve a course code to its archive history entry.

        ``course_name``, when the caller knows it, lets a missing code be
        matched to the same course under another department code.
        """
        from .archive import enrolment_uncaptured_terms, split_course_code

        try:
            branch, number = split_course_code(course_code)
        except ValueError as exc:
            raise ItuArchiveError(str(exc)) from exc
        canonical = f"{branch} {number}"
        history = self.archive.get_course_history(branch)
        entry = history.get(canonical)
        if entry is None:
            raise ItuArchiveError(self._archive_missing_course_message(canonical, history, course_name))
        # Whether a term's enrolment was captured is decided over the whole
        # branch file, so it is attached here, once, for every tool that reads
        # the entry (history, who taught, fill rate, the degree planner).
        entry = dict(entry)
        entry["enrolment_uncaptured_terms"] = enrolment_uncaptured_terms(
            history, captured_terms=self._archive_terms_with_enrolment()
        )
        return canonical, entry

    def _archive_terms_with_enrolment(self) -> list[str]:
        """Terms the index itself shows to hold enrolment: the live term.

        ``index.json`` carries term-wide totals for the current term only. A
        small branch whose sections are all still empty must not be read as
        "enrolment not captured" when the term as a whole has students.
        """
        from .archive import stats_enrolment_captured

        # The index is a best-effort refinement: an archive stand-in without
        # it, or a failed read, leaves the branch-file rule on its own.
        read_index = getattr(self.archive, "get_index", None)
        try:
            index = read_index() if callable(read_index) else None
        except ItuArchiveError:
            return []
        if not isinstance(index, dict):
            return []
        slug = index.get("currentSlug")
        if slug and stats_enrolment_captured(index.get("stats")):
            return [str(slug)]
        return []

    @staticmethod
    def _attach_enrolment_note(result: dict[str, Any]) -> None:
        from .archive import ENROLMENT_NOT_CAPTURED_NOTE

        if result.get("enrolment_not_captured_terms") or result.get("enrolment_not_captured"):
            result["enrolment_note"] = ENROLMENT_NOT_CAPTURED_NOTE

    def _archive_missing_course_message(
        self, canonical: str, history: dict[str, Any], course_name: str | None = None,
    ) -> str:
        """Say that a code is absent without implying the course never ran.

        A course whose department code changed, or that is recorded under its
        Turkish or English sibling, exists in the archive under another code.
        Those codes are named when the index or the branch file shows them.
        """
        from .archive import related_course_codes, same_course_other_branches

        message = (
            f"{canonical} bu kodla arşivde bulunamadı. Arşiv 2016-2017 Yaz'dan itibaren "
            "açılmış şubeleri kapsıyor."
        )
        siblings = related_course_codes(canonical, history.keys())
        try:
            codes = self.archive.get_course_codes()
        except ItuArchiveError:
            codes = []
        # Without a name nothing ties another branch's code to this course:
        # the same number alone matches unrelated courses.
        own_name = course_name or next(
            (row[1] for row in codes
             if isinstance(row, list) and len(row) >= 2 and str(row[0]).strip().upper() == canonical),
            None,
        )
        others = same_course_other_branches(canonical, own_name, codes) if own_name else []
        if not siblings and not others:
            return message + (
                " Hiç açılmamış ya da yalnızca başka bir kodla açılmış dersler bu kod altında "
                "yer almaz; archive_search_courses ile dersin adından arayın."
            )
        message += " Bu, dersin hiç açılmadığı anlamına gelmez; ders başka bir kodla kayıtlı olabilir."
        if others:
            listed = ", ".join(
                f"{item['course_code']} ({item['course_name']}, {item['term_count']} dönem)" for item in others
            )
            message += f" Başka bölüm kodunda aynı numara ve aynı ad: {listed}."
        if siblings:
            message += f" Aynı branşta aynı numara: {', '.join(siblings)}."
        return message + " Aracı bu kodlardan biriyle yeniden çağırın."

    def _attach_other_branch_codes(self, result: dict[str, Any], canonical: str, name: Any) -> None:
        """Add the codes that may be the same course under another department code.

        One read of the course index, cached by the client; no request per
        candidate. A failed read leaves the result as it is.
        """
        from .archive import same_course_other_branches

        try:
            candidates = same_course_other_branches(canonical, name, self.archive.get_course_codes())
        except ItuArchiveError:
            return
        if not candidates:
            return
        result["same_course_other_codes"] = candidates
        result["same_course_other_codes_note"] = (
            f"{', '.join(item['course_code'] for item in candidates)}: başka bölüm kodunda aynı numara ve "
            "aynı adla kayıtlı. Bölüm kodu değişmiş aynı ders olabilir (term_count değeri büyük olan "
            "çoğunlukla eski koddur) ya da başka bölümün paralel dersi olabilir. Bu sonuca katılmadı; "
            "o kodun geçmişi için aracı o kodla yeniden çağırın. Dizin dönem aralığını tutmuyor, "
            "yalnızca dönem sayısını veriyor."
        )

    def archive_list_terms(self) -> dict[str, Any]:
        """List every term the archive holds, with coverage and gaps."""
        from .archive import is_empty_record

        index = self.archive.get_index()
        # Only the live term carries a filled provenance; the older ones hold
        # an object of empty strings, which says nothing and is dropped.
        terms = [
            {key: value for key, value in term.items() if not (key == "provenance" and is_empty_record(value))}
            if isinstance(term, dict) else term
            for term in index.get("terms") or []
        ]
        return {
            "current_term": index.get("currentTerm"),
            "current_slug": index.get("currentSlug"),
            "scraped_at": index.get("scrapedAt"),
            "term_count": len(terms),
            "terms": terms,
            "missing_terms": [t.get("slug") for t in terms if t.get("missing")],
            "note": (
                "Arşiv, OBS'nin yalnızca aktif dönemi yayınlaması nedeniyle tutuluyor. "
                "Aktif dönem OBS'den, geçmiş dönemler tarihsel dökümlerden geliyor."
            ),
            "untrusted_external_content": True,
        }

    def archive_course_history(
        self,
        course_code: str,
        limit_terms: int = 10,
    ) -> dict[str, Any]:
        """Show a course's term-by-term offering history and which seasons it opens in."""
        from .archive import course_history

        canonical, entry = self._archive_course_entry(course_code)
        result = course_history(entry, limit_terms=limit_terms)
        result["course_code"] = canonical
        self._attach_enrolment_note(result)
        self._attach_other_branch_codes(result, canonical, entry.get("name"))
        result["untrusted_external_content"] = True
        return result

    def archive_who_taught(
        self,
        course_code: str,
        limit_terms: int | None = None,
    ) -> dict[str, Any]:
        """List who has taught a course, how often, and how recently."""
        from .archive import who_taught

        canonical, entry = self._archive_course_entry(course_code)
        result = who_taught(entry, limit_terms=limit_terms)
        result["course_code"] = canonical
        self._attach_enrolment_note(result)
        self._attach_other_branch_codes(result, canonical, entry.get("name"))
        result["untrusted_external_content"] = True
        return result

    def archive_instructor_courses(
        self,
        instructor: str,
        limit: int = 40,
    ) -> dict[str, Any]:
        """List the courses one instructor has taught across archived terms."""
        from .archive import clean_placeholder, instructor_courses
        from .parsing import normalize_lookup_text as _norm

        target = _norm(instructor)
        if not target:
            raise ItuArchiveError("instructor boş olamaz.")
        if clean_placeholder(str(instructor)) is None:
            raise ItuArchiveError(
                f"{instructor!r} bir öğretim üyesi adı değil. Arşiv, öğretim üyesi duyurulmamış "
                "şubeleri bu işaretle tutuyor; bir kişi olarak listelenmez."
            )

        # The index also lists the placeholder ("-", "--") the dumps use for
        # sections with no announced instructor. That is not a person.
        names = [
            row for row in self.archive.get_instructor_names()
            if isinstance(row, list) and row and clean_placeholder(str(row[0])) is not None
        ]
        # names.json rows are [name, index_letter, term_count, section_count];
        # the letter tells us which shard holds the full history.
        exact = [row for row in names if len(row) >= 2 and _norm(str(row[0])) == target]
        partial = [row for row in names if len(row) >= 2 and target in _norm(str(row[0]))]
        candidates = exact or partial
        if not candidates:
            raise ItuArchiveError(f"Öğretim üyesi arşivde bulunamadı: {instructor!r}")
        if not exact and len(candidates) > 1:
            return {
                "query": instructor,
                "ambiguous": True,
                "matches": [
                    {"instructor": row[0], "term_count": row[2], "section_count": row[3]}
                    for row in candidates[:25]
                    if len(row) >= 4
                ],
                "message": "Birden fazla eşleşme var; tam adı verin.",
                "untrusted_external_content": True,
            }

        name, letter = str(candidates[0][0]), str(candidates[0][1])
        shard = self.archive.get_instructor_history(letter)
        entry = shard.get(name)
        if entry is None:
            raise ItuArchiveError(f"Öğretim üyesi geçmişi okunamadı: {name!r}")
        result = instructor_courses(entry, limit=limit)
        result["untrusted_external_content"] = True
        return result

    def _resolve_archive_term_branch(
        self, term: str | int, branch: str
    ) -> tuple[str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        """Resolve coverage + sections for one (term, branch) pair.

        Shared by every tool that reads a term's section list, so
        "term never captured" vs "branch absent from this term's dump" is
        computed and worded consistently everywhere, not just in one tool.
        ``term`` may be any spelling ``known_archive_term`` accepts.
        """
        from .archive_tools import known_archive_term

        term, term_entry = known_archive_term(self.archive, term)

        # An empty result has three very different meanings — the term was never
        # captured, this branch is absent from that term's dump, or the filters
        # simply matched nothing. Reporting all three as "0 sonuç" is what made
        # the unpublished-term case unreadable, so each is named explicitly.
        meta = self.archive.get_term_meta(term) if not term_entry.get("missing") else {}
        branch_codes = {str(item.get("code")).upper() for item in (meta.get("branches") or [])}
        sections = self.archive.get_term_branch(term, branch)

        if term_entry.get("missing"):
            coverage = "term_missing"
        elif branch.upper() not in branch_codes:
            coverage = "branch_absent_from_term"
        else:
            coverage = "covered"

        return coverage, term_entry, meta, sections

    @staticmethod
    def _archive_enrolment_captured(meta: dict[str, Any], sections: list[dict[str, Any]]) -> bool:
        """Whether one term's enrolment numbers are measurements.

        The term-wide totals in ``meta.json`` decide when they are there;
        otherwise every section of the loaded branch file is looked at. A
        file with no section at all has nothing to disprove.
        """
        from .archive import enrolment_captured, stats_enrolment_captured

        captured = stats_enrolment_captured(meta.get("stats") if isinstance(meta, dict) else None)
        if captured is None:
            captured = enrolment_captured(
                (section.get("capacity"), section.get("enrolled"))
                for section in sections
                if isinstance(section, dict)
            )
        return captured is not False

    def archive_term_sections(
        self,
        term: str | int,
        branch: str | None = None,
        course_code: str | None = None,
        instructor: str | None = None,
        limit: int = 40,
    ) -> dict[str, Any]:
        """List archived sections for a term, filtered by branch, course, or instructor.

        This answers a question OBS cannot while a term is unpublished: the
        archive already holds the upcoming term's section list, so "which
        courses actually open in Güz" is checkable instead of guessable.
        """
        from .archive import split_course_code, summarize_section
        from .archive_tools import known_archive_term
        from .parsing import normalize_lookup_text as _norm

        term, _ = known_archive_term(self.archive, term)
        target_code: str | None = None
        if course_code:
            try:
                code_branch, code_number = split_course_code(course_code)
            except ValueError as exc:
                raise ItuArchiveError(str(exc)) from exc
            target_code = f"{code_branch} {code_number}"
            branch = branch or code_branch
        if not branch:
            raise ItuArchiveError("branch veya course_code verilmeli.")

        coverage, term_entry, meta, sections = self._resolve_archive_term_branch(term, branch)
        captured = self._archive_enrolment_captured(meta, sections)

        matched = []
        for section in sections:
            if target_code and _norm(section.get("code")) != _norm(target_code):
                continue
            if instructor and _norm(instructor) not in _norm(section.get("instructor")):
                continue
            matched.append(summarize_section(section, enrolment_captured=captured))

        coverage_notes = {
            "term_missing": (
                f"{term} hiçbir kaynakta yok; bu dönem için şube verisi bulunmuyor. "
                "Sonucun boş olması 'ders açılmadı' demek değildir."
            ),
            "branch_absent_from_term": (
                f"{branch.upper()} branşı {term} dökümünde hiç yok ({meta.get('sections')} şube "
                "kaydedilmiş). Bu branşın o dönem açılmadığı anlamına gelmez; döküm eksik olabilir."
            ),
            "covered": None,
        }

        result = {
            "term": term,
            "term_label": term_entry.get("label"),
            "term_source": term_entry.get("source"),
            "branch": branch.upper(),
            "course_code": target_code,
            "instructor_filter": instructor,
            "coverage": coverage,
            "branch_section_count": len(sections),
            "match_count": len(matched),
            "enrolment_not_captured": bool(sections) and not captured,
            "sections": matched[:limit],
            "truncated": len(matched) > limit,
            "untrusted_external_content": True,
        }
        self._attach_enrolment_note(result)
        note = coverage_notes[coverage]
        if note:
            result["coverage_note"] = note
        elif not matched:
            result["coverage_note"] = (
                f"{branch.upper()} {term} dökümünde var ({len(sections)} şube) ama filtreye "
                "uyan şube yok."
            )
        return result

    def archive_fill_rate(
        self,
        crn: str | int | None = None,
        course_code: str | None = None,
        term: str | int | None = None,
    ) -> dict[str, Any]:
        """Report how full a section was, by CRN or across a course's history.

        With a CRN, reads the quota time series for that term. With a course
        code, reports the historical capacity/enrolment of every past section,
        which is the practical way to judge whether a course fills up; a term
        given next to a course code narrows the answer to that term.
        """
        from .archive import course_history, fill_summary, snapshot_freshness
        from .archive_tools import known_archive_term, term_given

        term_slug = known_archive_term(self.archive, term)[0] if term_given(term) else None
        crn = str(crn).strip() if crn is not None and not isinstance(crn, bool) else ""

        if crn:
            # Already read (and cached) to resolve the term: no extra request.
            index = self.archive.get_index()
            current = index.get("currentSlug")
            resolved_term = term_slug
            if resolved_term is None:
                if not current:
                    raise ItuArchiveError("term çözümlenemedi; term parametresi verin.")
                resolved_term = known_archive_term(self.archive, current)[0]
            quota = self.archive.get_quota(resolved_term)
            entry = fill_summary(quota, crn)
            if entry is None:
                raise ItuArchiveError(
                    f"CRN {crn} {resolved_term} kontenjan kaydında yok. Kontenjan serisi "
                    "yalnızca arşivin canlı izlediği dönemler için var."
                )
            freshness = snapshot_freshness(
                quota.get("last"),
                scraped_at=index.get("scrapedAt"),
                is_current_term=resolved_term == str(current or ""),
            )
            return {
                "crn": str(crn),
                "term": resolved_term,
                "snapshots": quota.get("snapshots"),
                "first_snapshot": quota.get("first"),
                "last_snapshot": quota.get("last"),
                "last_snapshot_age_days": freshness["last_snapshot_age_days"],
                "archive_scraped_at": index.get("scrapedAt"),
                "scan_active": freshness["scan_active"],
                "quota": entry,
                "note": freshness["note"],
                "untrusted_external_content": True,
            }

        if not course_code:
            raise ItuArchiveError("crn veya course_code verilmeli.")

        canonical, entry = self._archive_course_entry(course_code)
        history = course_history(entry, limit_terms=None)
        offered_terms = [offering["term"] for offering in history["offerings"]]
        if term_slug is not None:
            # A term next to a course code is a filter. It used to be read
            # only on the CRN path and silently dropped here.
            history["offerings"] = [o for o in history["offerings"] if o["term"] == term_slug]
            history["enrolment_not_captured_terms"] = [
                t for t in history["enrolment_not_captured_terms"] if t == term_slug
            ]
        ratios = [
            section["fill_ratio"]
            for offering in history["offerings"]
            for section in offering["sections"]
            if section["fill_ratio"] is not None
        ]
        result = {
            "course_code": canonical,
            "course_name": history["course_name"],
            "terms_offered": history["terms_offered"],
            "seasonality": history["seasonality"],
            "sections_with_quota_data": len(ratios),
            "average_fill_ratio": round(sum(ratios) / len(ratios), 3) if ratios else None,
            "max_fill_ratio": max(ratios) if ratios else None,
            "always_fills": bool(ratios) and all(ratio >= 1.0 for ratio in ratios),
            "enrolment_not_captured_terms": history["enrolment_not_captured_terms"],
            "term_filter": term_slug,
            "offerings": history["offerings"],
        }
        if term_slug is not None:
            result["term_filter_note"] = (
                f"Yalnızca {term_slug} dönemi gösteriliyor; doluluk sayıları bu dönemin "
                "şubelerinden. terms_offered ve seasonality dersin tüm geçmişini anlatır."
            )
            if not history["offerings"]:
                result["coverage"] = "term_absent_for_course"
                result["available_terms"] = offered_terms
                result["coverage_note"] = (
                    f"{canonical} için {term_slug} döneminde arşivde şube kaydı yok. Kayıtlı "
                    "dönemler available_terms alanında. Bu, dersin o dönem açılmadığını tek "
                    "başına göstermez; archive_term_sections coverage alanına bakın."
                )
        self._attach_enrolment_note(result)
        self._attach_other_branch_codes(result, canonical, entry.get("name"))
        result["untrusted_external_content"] = True
        return result

    def archive_search_courses(self, query: str, limit: int = 20) -> dict[str, Any]:
        """Search the archive's full course index by code or name fragment.

        Unlike ``obs_search_courses`` (active term only), this searches every
        course the archive has ever seen, so a name fragment like "sayısal
        yöntemler" resolves to a code even when the course isn't offered right
        now — ``archive_course_history`` needs the exact code this returns.
        """
        from .archive import course_search_hint, search_courses

        codes = self.archive.get_course_codes()
        matches = search_courses(codes, query, limit=limit)
        result: dict[str, Any] = {
            "query": query,
            "match_count": len(matches),
            "matches": matches,
        }
        # A bare zero reads as "no such course"; say what the index can match.
        result.update(course_search_hint(codes, query, matches) or {})
        result["untrusted_external_content"] = True
        return result

    def archive_list_branches(self, term: str | int) -> dict[str, Any]:
        """List every branch present in one term's archive dump, with section counts.

        Answers "does this branch even have a döküm for this term?" directly,
        instead of probing with ``archive_term_sections`` and reading its
        ``coverage`` field branch by branch.
        """
        from .archive_tools import known_archive_term

        term, term_entry = known_archive_term(self.archive, term)
        if term_entry.get("missing"):
            return {
                "term": term,
                "coverage": "term_missing",
                "branches": [],
                "note": f"{term} hiçbir kaynakta yok; branş listesi bulunmuyor.",
                "untrusted_external_content": True,
            }

        meta = self.archive.get_term_meta(term)
        branches = sorted(
            (meta.get("branches") or []), key=lambda item: str(item.get("code") or "")
        )
        return {
            "term": term,
            "term_label": term_entry.get("label"),
            "term_source": term_entry.get("source"),
            "coverage": "covered",
            "branch_count": len(branches),
            "total_sections": meta.get("sections"),
            "total_courses": meta.get("courses"),
            "branches": branches,
            "untrusted_external_content": True,
        }

    def archive_compare_terms(
        self,
        course_code: str,
        term_a: str | int,
        term_b: str | int,
    ) -> dict[str, Any]:
        """Diff one course's sections between two archived terms.

        Names what actually changed — instructor turnover, section count,
        capacity/fill movement — rather than leaving two ``archive_term_sections``
        calls to be compared by eye.
        """
        from .archive import diff_term_offerings, split_course_code, summarize_section
        from .archive_tools import known_archive_term
        from .parsing import normalize_lookup_text as _norm

        try:
            branch, number = split_course_code(course_code)
        except ValueError as exc:
            raise ItuArchiveError(str(exc)) from exc
        canonical = f"{branch} {number}"
        term_a = known_archive_term(self.archive, term_a)[0]
        term_b = known_archive_term(self.archive, term_b)[0]

        def _sections_for(term: str) -> tuple[str, list[dict[str, Any]], bool]:
            coverage, _, meta, sections = self._resolve_archive_term_branch(term, branch)
            captured = self._archive_enrolment_captured(meta, sections)
            matched = [
                summarize_section(section, enrolment_captured=captured)
                for section in sections
                if _norm(section.get("code")) == _norm(canonical)
            ]
            return coverage, matched, captured or not sections

        coverage_a, sections_a, captured_a = _sections_for(term_a)
        coverage_b, sections_b, captured_b = _sections_for(term_b)

        result: dict[str, Any] = {
            "course_code": canonical,
            "term_a": term_a,
            "term_b": term_b,
            "coverage_a": coverage_a,
            "coverage_b": coverage_b,
            "untrusted_external_content": True,
        }
        if coverage_a != "covered" or coverage_b != "covered":
            result["comparable"] = False
            result["note"] = (
                "Bir veya iki dönem/branş arşivde tam kapsanmıyor "
                f"(term_a: {coverage_a}, term_b: {coverage_b}); karşılaştırma güvenilir olmayabilir."
            )
        else:
            result["comparable"] = True
        diff = diff_term_offerings(
            sections_a, sections_b,
            enrolment_captured_a=captured_a, enrolment_captured_b=captured_b,
        )
        result["diff"] = diff
        not_captured = [
            term for term, captured in ((term_a, captured_a), (term_b, captured_b)) if not captured
        ]
        if not_captured:
            reason = (
                f"{', '.join(not_captured)}: kayıtlı öğrenci sayıları arşive alınmamış (dönem "
                "dökümündeki bütün şubelerde 0). Kayıtlı sayısı ve doluluk karşılaştırılamaz; şube "
                "sayısı, kontenjan ve öğretim üyeleri karşılaştırıldı."
            )
        elif not diff["enrolment_comparable"]:
            reason = "En az bir dönemde bu dersin kayıtlı öğrenci sayısı yok."
        else:
            reason = None
        result["enrolment_comparison"] = {
            "comparable": diff["enrolment_comparable"],
            "not_captured_terms": not_captured,
            "reason": reason,
        }
        result["sections_a"] = sections_a
        result["sections_b"] = sections_b
        return result
