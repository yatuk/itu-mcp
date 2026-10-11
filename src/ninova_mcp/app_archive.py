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

    def _archive_course_entry(self, course_code: str) -> tuple[str, dict[str, Any]]:
        """Resolve a course code to its archive history entry."""
        from .archive import split_course_code

        try:
            branch, number = split_course_code(course_code)
        except ValueError as exc:
            raise ItuArchiveError(str(exc)) from exc
        canonical = f"{branch} {number}"
        history = self.archive.get_course_history(branch)
        entry = history.get(canonical)
        if entry is None:
            raise ItuArchiveError(
                f"{canonical} arşivde bulunamadı. Arşiv 2016-2017 Yaz'dan itibaren "
                "açılmış şubeleri kapsıyor; hiç açılmamış dersler yer almaz."
            )
        return canonical, entry

    def archive_list_terms(self) -> dict[str, Any]:
        """List every term the archive holds, with coverage and gaps."""
        index = self.archive.get_index()
        terms = index.get("terms") or []
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
        result["untrusted_external_content"] = True
        return result

    def archive_instructor_courses(
        self,
        instructor: str,
        limit: int = 40,
    ) -> dict[str, Any]:
        """List the courses one instructor has taught across archived terms."""
        from .archive import instructor_courses
        from .parsing import normalize_lookup_text as _norm

        target = _norm(instructor)
        if not target:
            raise ItuArchiveError("instructor boş olamaz.")

        names = self.archive.get_instructor_names()
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
        self, term: str, branch: str
    ) -> tuple[str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        """Resolve coverage + sections for one (term, branch) pair.

        Shared by every tool that reads a term's section list, so
        "term never captured" vs "branch absent from this term's dump" is
        computed and worded consistently everywhere, not just in one tool.
        """
        index_terms = {
            str(entry.get("slug")): entry
            for entry in (self.archive.get_index().get("terms") or [])
        }
        if term not in index_terms:
            raise ItuArchiveError(
                f"Dönem arşivde yok: {term!r}. archive_list_terms ile geçerli dönemleri görün."
            )
        term_entry = index_terms[term]

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

    def archive_term_sections(
        self,
        term: str,
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
        from .parsing import normalize_lookup_text as _norm

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

        matched = []
        for section in sections:
            if target_code and _norm(section.get("code")) != _norm(target_code):
                continue
            if instructor and _norm(instructor) not in _norm(section.get("instructor")):
                continue
            matched.append(summarize_section(section))

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
            "sections": matched[:limit],
            "truncated": len(matched) > limit,
            "untrusted_external_content": True,
        }
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
        crn: str | None = None,
        course_code: str | None = None,
        term: str | None = None,
    ) -> dict[str, Any]:
        """Report how full a section was, by CRN or across a course's history.

        With a CRN, reads the quota time series for that term. With a course
        code, reports the historical capacity/enrolment of every past section,
        which is the practical way to judge whether a course fills up.
        """
        from .archive import course_history, fill_summary

        resolved_term = term or self.archive.get_index().get("currentSlug")

        if crn:
            if not resolved_term:
                raise ItuArchiveError("term çözümlenemedi; term parametresi verin.")
            quota = self.archive.get_quota(resolved_term)
            entry = fill_summary(quota, crn)
            if entry is None:
                raise ItuArchiveError(
                    f"CRN {crn} {resolved_term} kontenjan kaydında yok. Kontenjan serisi "
                    "yalnızca arşivin canlı izlediği dönemler için var."
                )
            return {
                "crn": str(crn),
                "term": resolved_term,
                "snapshots": quota.get("snapshots"),
                "first_snapshot": quota.get("first"),
                "last_snapshot": quota.get("last"),
                "quota": entry,
                "note": "Kontenjan günde bir kez tazeleniyor; anlık değil.",
                "untrusted_external_content": True,
            }

        if not course_code:
            raise ItuArchiveError("crn veya course_code verilmeli.")

        canonical, entry = self._archive_course_entry(course_code)
        history = course_history(entry, limit_terms=None)
        ratios = [
            section["fill_ratio"]
            for offering in history["offerings"]
            for section in offering["sections"]
            if section["fill_ratio"] is not None
        ]
        return {
            "course_code": canonical,
            "course_name": history["course_name"],
            "terms_offered": history["terms_offered"],
            "seasonality": history["seasonality"],
            "sections_with_quota_data": len(ratios),
            "average_fill_ratio": round(sum(ratios) / len(ratios), 3) if ratios else None,
            "max_fill_ratio": max(ratios) if ratios else None,
            "always_fills": bool(ratios) and all(ratio >= 1.0 for ratio in ratios),
            "offerings": history["offerings"],
            "untrusted_external_content": True,
        }

    def archive_search_courses(self, query: str, limit: int = 20) -> dict[str, Any]:
        """Search the archive's full course index by code or name fragment.

        Unlike ``obs_search_courses`` (active term only), this searches every
        course the archive has ever seen, so a name fragment like "sayısal
        yöntemler" resolves to a code even when the course isn't offered right
        now — ``archive_course_history`` needs the exact code this returns.
        """
        from .archive import search_courses

        codes = self.archive.get_course_codes()
        matches = search_courses(codes, query, limit=limit)
        return {
            "query": query,
            "match_count": len(matches),
            "matches": matches,
            "untrusted_external_content": True,
        }

    def archive_list_branches(self, term: str) -> dict[str, Any]:
        """List every branch present in one term's archive dump, with section counts.

        Answers "does this branch even have a döküm for this term?" directly,
        instead of probing with ``archive_term_sections`` and reading its
        ``coverage`` field branch by branch.
        """
        index_terms = {
            str(entry.get("slug")): entry
            for entry in (self.archive.get_index().get("terms") or [])
        }
        if term not in index_terms:
            raise ItuArchiveError(
                f"Dönem arşivde yok: {term!r}. archive_list_terms ile geçerli dönemleri görün."
            )
        term_entry = index_terms[term]
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
        term_a: str,
        term_b: str,
    ) -> dict[str, Any]:
        """Diff one course's sections between two archived terms.

        Names what actually changed — instructor turnover, section count,
        capacity/fill movement — rather than leaving two ``archive_term_sections``
        calls to be compared by eye.
        """
        from .archive import diff_term_offerings, split_course_code, summarize_section
        from .parsing import normalize_lookup_text as _norm

        try:
            branch, number = split_course_code(course_code)
        except ValueError as exc:
            raise ItuArchiveError(str(exc)) from exc
        canonical = f"{branch} {number}"

        def _sections_for(term: str) -> tuple[str, list[dict[str, Any]]]:
            coverage, _, _, sections = self._resolve_archive_term_branch(term, branch)
            matched = [
                summarize_section(section)
                for section in sections
                if _norm(section.get("code")) == _norm(canonical)
            ]
            return coverage, matched

        coverage_a, sections_a = _sections_for(term_a)
        coverage_b, sections_b = _sections_for(term_b)

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
        result["diff"] = diff_term_offerings(sections_a, sections_b)
        result["sections_a"] = sections_a
        result["sections_b"] = sections_b
        return result
