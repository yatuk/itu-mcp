"""Archive tools for the datasets beyond section history.

Grade distributions, catalog entries, the reverse prerequisite index, the
term-wide section search index, exam schedules and the scraper status file.
The tool methods live in a mixin and their MCP metadata in
``ARCHIVE_DATASET_TOOLS`` so that ``server.py`` only has to wire them in.

Every tool here is read-only, needs no credentials, and reads through
``ItuArchiveClient`` (host allowlist, TTL cache, safe redirects). Bad input
raises ``ItuArchiveError``; missing data never does. It comes back with a
``coverage`` value that says whether the archive holds the file and found
nothing, or never recorded the file at all.
"""

from __future__ import annotations

from typing import Any

from .archive_client import (
    ItuArchiveClient,
    ItuArchiveError,
    branch_path_segment,
    term_path_segment,
)

# The tool that evaluates the real prerequisite rule (Ve/Veya groups, minimum
# grades, credit requirement) for a single course.
PREREQUISITE_RULE_TOOL = "explain_course_eligibility"

SEARCH_SECTIONS_MAX_LIMIT = 200
EXAM_SCHEDULE_MAX_LIMIT = 500


def _clamp_limit(limit: int, maximum: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise ItuArchiveError(f"limit tam sayı olmalı: {limit!r}") from exc
    return max(1, min(value, maximum))


def _canonical_course(course_code: str) -> tuple[str, str]:
    """Return ``(branch, "BRANCH NUMBER")`` or raise ``ItuArchiveError``."""
    from .archive import split_course_code

    try:
        branch, number = split_course_code(course_code)
    except ValueError as exc:
        raise ItuArchiveError(str(exc)) from exc
    return branch, f"{branch} {number}"


class ArchiveDatasetToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``archive``."""

    archive: ItuArchiveClient

    def _archive_known_term(self, term: str) -> tuple[str, dict[str, Any]]:
        """Validate a term slug and resolve it against the archive index."""
        slug = term_path_segment(term)
        index_terms = {
            str(entry.get("slug")): entry
            for entry in (self.archive.get_index().get("terms") or [])
        }
        if slug not in index_terms:
            raise ItuArchiveError(
                f"Dönem arşivde yok: {term!r}. archive_list_terms ile geçerli dönemleri görün."
            )
        return slug, index_terms[slug]

    def archive_grade_distribution(
        self,
        course_code: str,
        term: str | None = None,
    ) -> dict[str, Any]:
        """Letter grade counts and percentages for a course, term by term."""
        from .archive import grade_distribution

        branch, canonical = _canonical_course(course_code)
        slug = term_path_segment(term) if term else None
        records = self.archive.get_grades(branch)
        if records is None:
            return {
                "course_code": canonical,
                "term_filter": slug,
                "coverage": "branch_grades_missing",
                "term_count": 0,
                "terms": [],
                "coverage_note": (
                    f"{branch} branşı için arşivde not dağılımı dosyası hiç yok. Bu, dersin "
                    "not dağılımının olmadığı anlamına gelmez; yalnızca kaydedilmemiş."
                ),
                "untrusted_external_content": True,
            }

        result = grade_distribution(records, canonical, term=slug)
        result["note"] = (
            "Yüzdeler, sayılan notların toplamına (counted_total) göre hesaplandı. Bir "
            "dönemde görünmeyen not etiketi o dönem yayınlanmamıştır; sıfır sayılmamalı."
        )
        if result["mismatch_term_count"]:
            result["total_mismatch_note"] = (
                f"{result['mismatch_term_count']} dönemde yayınlanan toplam (total) ile not "
                "sayılarının toplamı (counted_total) birbirini tutmuyor. İkisi de kaynakta "
                "olduğu gibi verildi, düzeltilmedi."
            )
        if result["related_codes"]:
            result["related_codes_note"] = (
                "Aynı numarayı taşıyan diğer kodlar arşivde ayrı kayıtlı ve bu sonuca "
                "katılmadı. same_counts_as alanı dolu olan dönemlerde arşiv birebir aynı "
                "dağılımı o kodun altında da tutuyor; bu sayıları toplamayın."
            )
        first_term = result["branch_terms"][0] if result["branch_terms"] else None
        if result["coverage"] == "course_absent_from_grades":
            result["coverage_note"] = (
                f"{canonical} için arşivde not dağılımı kaydı yok. {branch} dosyası "
                + (f"{first_term} döneminden başlıyor; " if first_term else "boş; ")
                + "daha eski dönemler ve dağılımı yayınlanmamış dersler yer almaz."
            )
        elif result["coverage"] == "term_absent_for_course":
            result["coverage_note"] = (
                f"{canonical} için {slug} döneminde not dağılımı kaydı yok. Kayıtlı dönemler "
                "available_terms alanında."
            )
        result["untrusted_external_content"] = True
        return result

    def archive_course_catalog(self, course_code: str) -> dict[str, Any]:
        """Catalog entry for a course: names, credits, description, outcomes, topics."""
        from .archive import _code_key, catalog_entry, related_course_codes

        branch, canonical = _canonical_course(course_code)
        catalog = self.archive.get_catalog(branch)
        if catalog is None:
            return {
                "course_code": canonical,
                "coverage": "branch_catalog_missing",
                "coverage_note": (
                    f"{branch} branşı için arşivde katalog dosyası hiç yok. Bu, dersin "
                    "var olmadığı anlamına gelmez; yalnızca katalog kaydı tutulmamış."
                ),
                "untrusted_external_content": True,
            }

        entry = catalog.get(canonical)
        if not isinstance(entry, dict):
            entry = next(
                (
                    value
                    for key, value in catalog.items()
                    if _code_key(key) == _code_key(canonical) and isinstance(value, dict)
                ),
                None,
            )
        related = related_course_codes(canonical, catalog)
        if entry is None:
            result: dict[str, Any] = {
                "course_code": canonical,
                "coverage": "course_absent_from_catalog",
                "coverage_note": (
                    f"{canonical} {branch} katalog dosyasında yok ({len(catalog)} ders "
                    "kayıtlı). Kod yanlış olabilir ya da ders katalogda yer almıyor olabilir."
                ),
            }
        else:
            result = catalog_entry(entry)
            result["course_code"] = canonical
            result["coverage"] = "covered"
        result["related_codes"] = related
        if related:
            result["related_codes_note"] = (
                "Aynı numarayı taşıyan diğer kodların katalog kaydı ayrıdır; içerikleri "
                "birleştirilmedi."
            )
        result["untrusted_external_content"] = True
        return result

    def archive_course_unlocks(self, course_code: str) -> dict[str, Any]:
        """Courses that list this course as a prerequisite (direct next level only)."""
        from .archive import related_course_codes

        _, canonical = _canonical_course(course_code)
        reverse = self.archive.get_prereq_reverse()
        raw = reverse.get(canonical)
        unlocks = sorted({str(code) for code in raw}) if isinstance(raw, list) else []
        related = [
            code
            for code in related_course_codes(canonical, reverse)
            if isinstance(reverse.get(code), list)
        ]
        result: dict[str, Any] = {
            "course_code": canonical,
            "coverage": "covered" if unlocks else "not_listed_as_prerequisite",
            "depth": "direct",
            "unlock_count": len(unlocks),
            "unlocks": unlocks,
            "related_codes": related,
            "rule_tool": PREREQUISITE_RULE_TOOL,
            "caveat": (
                "Bu liste yalnızca bir sonraki düzeydir: dersi ön şartlarında doğrudan anan "
                "dersler. Zincirin devamı dahil değil. Ön şart kuralları Ve/Veya "
                "seçenekleri ve en düşük harf notu içerir; bu liste onları göstermez, yani "
                f"{canonical} dersini geçmek listedeki dersleri tek başına alınabilir yapmaz. "
                f"Gerçek kural için ilgili ders adına {PREREQUISITE_RULE_TOOL} aracını çağırın."
            ),
            "untrusted_external_content": True,
        }
        if not unlocks:
            result["coverage_note"] = (
                f"{canonical} ön şart dizininde hiçbir dersin ön şartı olarak geçmiyor. "
                "Dizin yalnızca ön şart olarak anılan dersleri içerir; bu sonuç dersin var "
                "olmadığını göstermez."
            )
        if related:
            result["related_codes_note"] = (
                "Aynı numarayı taşıyan diğer kodlar dizinde ayrı kayıtlı; listeleri bu "
                "sonuca katılmadı."
            )
        return result

    def archive_search_sections(
        self,
        term: str | None = None,
        course_code: str | None = None,
        course_name: str | None = None,
        instructor: str | None = None,
        day: str | None = None,
        limit: int = 40,
    ) -> dict[str, Any]:
        """Search every branch of one term by code, name, instructor and/or day."""
        from .archive import search_sections
        from .parsing import normalize_lookup_text as _norm

        filters = {
            "course_code": course_code,
            "course_name": course_name,
            "instructor": instructor,
            "day": day,
        }
        if not any(_norm(value) for value in filters.values()):
            raise ItuArchiveError(
                "En az bir filtre verilmeli: course_code, course_name, instructor veya day."
            )
        limit = _clamp_limit(limit, SEARCH_SECTIONS_MAX_LIMIT)

        resolved = term or self.archive.get_index().get("currentSlug")
        if not resolved:
            raise ItuArchiveError("term çözümlenemedi; term parametresi verin.")
        slug, term_entry = self._archive_known_term(str(resolved))

        rows = None if term_entry.get("missing") else self.archive.get_term_search(slug)
        try:
            found = search_sections(
                rows,
                course_code=course_code,
                course_name=course_name,
                instructor=instructor,
                day=day,
                limit=limit,
            )
        except ValueError as exc:
            raise ItuArchiveError(str(exc)) from exc

        if term_entry.get("missing"):
            coverage = "term_missing"
        elif rows is None:
            coverage = "search_index_missing"
        else:
            coverage = "covered"

        result: dict[str, Any] = {
            "term": slug,
            "term_label": term_entry.get("label"),
            "term_source": term_entry.get("source"),
            "term_defaulted": term is None,
            "filters": {key: value for key, value in filters.items() if value},
            "coverage": coverage,
            **found,
            "untrusted_external_content": True,
        }
        if coverage == "term_missing":
            result["coverage_note"] = (
                f"{slug} hiçbir kaynakta yok; bu dönem için şube verisi bulunmuyor. "
                "Sonucun boş olması 'ders açılmadı' demek değildir."
            )
        elif coverage == "search_index_missing":
            result["coverage_note"] = (
                f"{slug} için dönem geneli arama dizini kaydedilmemiş. Sonucun boş olması "
                "'ders açılmadı' demek değildir; archive_term_sections ile branş bazında deneyin."
            )
        elif not found["match_count"]:
            result["coverage_note"] = (
                f"{slug} dizini var ({found['term_section_count']} şube) ama filtreye uyan "
                "şube yok."
            )
        return result

    def archive_exam_schedule(
        self,
        term: str,
        branch: str | None = None,
        course_code: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Exam schedule recorded for one term, optionally for a branch or course."""
        from .archive import filter_exams

        limit = _clamp_limit(limit, EXAM_SCHEDULE_MAX_LIMIT)
        target_branch = branch_path_segment(branch) if branch else None
        target_code = _canonical_course(course_code)[1] if course_code else None
        slug, term_entry = self._archive_known_term(term)

        payload = self.archive.get_exams(slug)
        result: dict[str, Any] = {
            "term": slug,
            "term_label": term_entry.get("label"),
            "branch": target_branch,
            "course_code": target_code,
        }
        if payload is None:
            result.update({
                "coverage": "exam_schedule_not_recorded",
                "exam_count": None,
                "match_count": 0,
                "exams": [],
                "truncated": False,
                "coverage_note": (
                    f"{slug} döneminin sınav programı arşive hiç kaydedilmemiş. Bu 'sınav "
                    "yok' demek değildir; program yalnızca tarandığı dönemler için tutuluyor."
                ),
                "untrusted_external_content": True,
            })
            return result

        exams = payload.get("exams")
        exams = exams if isinstance(exams, list) else []
        matched = filter_exams(exams, branch=target_branch, course_code=target_code)
        result.update({
            "term_label": payload.get("term") or term_entry.get("label"),
            "scraped_at": payload.get("scrapedAt"),
            "coverage": "covered",
            "exam_count": len(exams),
            "match_count": len(matched),
            "exams": matched[:limit],
            "truncated": len(matched) > limit,
            "untrusted_external_content": True,
        })
        if not matched:
            result["coverage_note"] = (
                f"{slug} sınav programı kayıtlı ({len(exams)} sınav) ama filtreye uyan "
                "sınav yok."
            )
        return result

    def archive_status(self) -> dict[str, Any]:
        """When the archive last ran and succeeded, and how old its data is."""
        from .archive import status_summary

        result = status_summary(self.archive.get_status())
        result["note"] = (
            "Yalnızca bilgi amaçlıdır. Arşiv, kayıt ve ekle-bırak haftaları dışında "
            "taramayı bilerek durdurur; data_age_days değerinin büyük olması bir hata ya "
            "da bozukluk göstergesi değildir."
        )
        result["untrusted_external_content"] = True
        return result


ARCHIVE_DATASET_TOOLS: list[dict[str, Any]] = [
    {
        "name": "archive_grade_distribution",
        "title": "Archive: Grade Distribution",
        "description": (
            "Letter grade counts for a course across the terms the archive has (from "
            "2023-2024 on): per term the counts, published total, percentages, and the OBS "
            "source URL. Only the exact code is used; a Turkish/English sibling such as "
            "'BLG 212' vs 'BLG 212E' is listed under 'related_codes', never merged, and "
            "'same_counts_as' marks terms where the archive stores identical numbers under "
            "both codes (do not add them up). Grade labels are passed through as published, "
            "including the '+' grades; a label absent from a term was not published and is "
            "not zero. 'total_mismatch' flags terms where the published total differs from "
            "the sum of the counts; both numbers are kept. Check 'coverage': "
            "'branch_grades_missing' means no grades file was ever recorded for the branch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_code": {
                    "type": "string",
                    "description": "Exact course code, e.g. 'BLG 223E'.",
                },
                "term": {
                    "type": "string",
                    "description": "Only this term slug, e.g. '2023-2024-guz' (default: all).",
                },
            },
            "required": ["course_code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "archive_course_catalog",
        "title": "Archive: Course Catalog",
        "description": (
            "Catalog entry for a course from the archive: Turkish and English name, "
            "language, credits (theory/practice/lab/local/ECTS), description, learning "
            "outcomes, weekly topics, textbooks, and the OBS source URL. Fields the "
            "catalog does not hold are named in 'missing_fields' rather than filled in. "
            "'coverage' separates 'course_absent_from_catalog' from "
            "'branch_catalog_missing' (no catalog file was ever recorded for the branch)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_code": {
                    "type": "string",
                    "description": "Exact course code, e.g. 'BLG 223E'.",
                },
            },
            "required": ["course_code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "archive_course_unlocks",
        "title": "Archive: Course Unlocks",
        "description": (
            "Which courses list the given course as a prerequisite, from the archive's "
            "reverse prerequisite index. This is the direct next level only, not the whole "
            "chain. Prerequisite rules have AND/OR alternatives and minimum grades that "
            "this flat list does not express, so passing the course does not by itself "
            "make the listed courses takeable: call explain_course_eligibility on a listed "
            "course for the real rule. 'coverage' is 'not_listed_as_prerequisite' when no "
            "course names it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_code": {
                    "type": "string",
                    "description": "Exact course code, e.g. 'BLG 223E'.",
                },
            },
            "required": ["course_code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "archive_search_sections",
        "title": "Archive: Search Sections",
        "description": (
            "Search one whole term across all branches at once by course code fragment, "
            "course name fragment, instructor, and/or day; every filter given must match. "
            "Matching is case-insensitive and Turkish-aware ('sahin' finds 'Şahin'). "
            "Defaults to the archive's current term. Use this when the branch is unknown "
            "or the question spans branches; archive_term_sections reads a single branch "
            "in more detail. Returns 'match_count' and 'truncated' alongside 'coverage'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {
                    "type": "string",
                    "description": "Term slug, e.g. '2025-2026-guz' (default: archive's current term).",
                },
                "course_code": {
                    "type": "string",
                    "description": "Course code or fragment, e.g. 'BLG 223E', 'BLG 2', 'mat'.",
                },
                "course_name": {"type": "string", "description": "Course name fragment."},
                "instructor": {"type": "string", "description": "Instructor name fragment."},
                "day": {
                    "type": "string",
                    "description": "Day the section meets, e.g. 'Salı' or 'Tuesday'.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": SEARCH_SECTIONS_MAX_LIMIT,
                    "default": 40,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "archive_exam_schedule",
        "title": "Archive: Exam Schedule",
        "description": (
            "Final exam schedule the archive recorded for a term (date, day, time, place, "
            "exam type), optionally filtered by branch or exact course code. Most terms "
            "have no recorded schedule: 'coverage' is then 'exam_schedule_not_recorded', "
            "which means the schedule was never captured, not that there are no exams. "
            "For the live current schedule use get_public_exam_schedule."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "Term slug, e.g. '2025-2026-yaz'."},
                "branch": {"type": "string", "description": "Branch code, e.g. 'BLG'."},
                "course_code": {
                    "type": "string",
                    "description": "Exact course code, e.g. 'BLG 223E'.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": EXAM_SCHEDULE_MAX_LIMIT,
                    "default": 100,
                },
            },
            "required": ["term"],
            "additionalProperties": False,
        },
    },
    {
        "name": "archive_status",
        "title": "Archive: Status",
        "description": (
            "When the archive scraper last ran and last succeeded, its section count, "
            "whether the last run was partial, which branches failed, and the data age in "
            "days. Informational only: the archive deliberately stops scraping outside "
            "registration and add/drop weeks, so an old date is expected and is not an error."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]
