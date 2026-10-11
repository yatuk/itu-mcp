"""Public OBS catalog tools: course search, prerequisites, schedules, exams, calendar.

None of these need credentials.
"""

from __future__ import annotations

from typing import Any

from .archive_client import ItuArchiveClient
from .obs_client import ObsError, ObsPublicClient
from .public_client import ItuPublicClient

# Link columns of the OBS course information table. Their cell text is only
# "... İçin Tıklayınız"; the link is the value.
_CATALOG_LINK_COLUMNS = {
    "ders katalog bilgisi": "catalog_url",
    "ders not dagilimi": "grade_distribution_url",
}


def _text_id(value: str | int | None) -> str | None:
    """A CRN or course number as text; clients send them as JSON numbers too."""
    if value is None or isinstance(value, bool):
        return None
    return str(value).strip() or None


def _shape_catalog_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn raw OBS course information rows into one clean entry per course code.

    OBS prints a Turkish/English pair in one row ("BLG335 -BLG335E",
    "Algoritma Analizi I / Analysis of Algorithms I", "Türkçe/English"). The
    parser repeats that row once per code, so each entry here takes its own
    half of the name and language, and a link is kept only when it points at
    this entry's course number.
    """
    from urllib.parse import parse_qs, urlparse

    from .archive import COURSE_CODE_PATTERN, normalize_course_code, split_course_code
    from .parsing import normalize_lookup_text

    shaped: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = row.get("code")
        if not code or code in shaped:
            continue
        code_cell = str(row.get("Ders Kodu") or row.get("Course Code") or "")
        row_codes = list(dict.fromkeys(
            normalize_course_code(match.group(0)) for match in COURSE_CODE_PATTERN.finditer(code_cell)
        )) or [code]
        position = row_codes.index(code) if code in row_codes else 0

        def own_part(value: Any, separator: str) -> str | None:
            cleaned = str(value or "").strip()
            parts = [part.strip() for part in cleaned.split(separator)]
            if len(row_codes) > 1 and len(parts) == len(row_codes):
                return parts[position] or None
            return cleaned or None

        entry: dict[str, Any] = {
            "code": code,
            "name": own_part(row.get("name"), " / "),
            "language": own_part(row.get("Dil") or row.get("Language"), "/"),
            "paired_codes": [other for other in row_codes if other != code],
        }
        try:
            number = split_course_code(code)[1].upper()
        except ValueError:
            number = ""
        for key, value in row.items():
            target = _CATALOG_LINK_COLUMNS.get(normalize_lookup_text(key.removesuffix("_url")))
            if not target or not key.endswith("_url"):
                continue
            linked = (parse_qs(urlparse(str(value)).query).get("dersNo") or [""])[0].upper()
            if linked == number:
                entry[target] = value
        shaped[code] = {key: value for key, value in entry.items() if value not in (None, [])}
    return list(shaped.values())


class PublicCatalogToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies the public clients."""

    obs_public: ObsPublicClient
    itu_public: ItuPublicClient
    archive: ItuArchiveClient

    def obs_search_courses(self, query: str, limit: int = 15) -> dict[str, Any]:
        """Look up exact codes in OBS and name/code fragments in the labeled archive."""
        from .archive import search_courses, split_course_code

        term = query.strip()
        if len(term) < 2:
            raise ObsError("Enter at least two characters of a course code or name.")
        bounded_limit = max(1, min(int(limit), 50))
        try:
            split_course_code(term)
        except ValueError:
            matches = search_courses(self.archive.get_course_codes(), term, limit=bounded_limit)
            results = [{"code": row["course_code"], "name": row["course_name"],
                        "branch": row["branch"], "terms_seen": row["term_count"]} for row in matches]
            source = "course_archive"
            source_url = self.archive.base_url + "/history/codes.json"
        else:
            results = _shape_catalog_rows(self.obs_public.search_courses(term))[:bounded_limit]
            source = "official_obs_course_information"
            source_url = "https://obs.itu.edu.tr/public/DersBilgi"
        result: dict[str, Any] = {
            "query": query,
            "count": len(results),
            "courses": results,
            "limit": bounded_limit,
            "source": source,
            "source_url": source_url,
            "offering_note": "Catalog or archive membership does not establish a current-term offering.",
            "untrusted_external_content": True,
        }
        if bounded_limit != limit:
            result["limit_note"] = f"limit must be between 1 and 50; {limit} was changed to {bounded_limit}."
        return result

    def obs_get_course_prerequisites(
        self,
        course_code: str,
        direction: str = "prerequisites",
        max_depth: int = 1,
    ) -> dict[str, Any]:
        """Query OBS public prerequisite / postrequisite relationships.

        ``direction``: ``"prerequisites"`` (what must be taken before),
        ``"postrequisites"`` (what this course unlocks), or ``"both"``.
        ``max_depth`` > 1 builds a recursive chain (adjacency list).
        """
        from .archive import normalize_course_code

        try:
            course_code = normalize_course_code(course_code)
        except ValueError as exc:
            raise ObsError("A full course code such as BLG 223E is required.") from exc
        direction = direction.lower()
        if direction not in ("prerequisites", "postrequisites", "both"):
            raise ObsError(
                "direction must be 'prerequisites', 'postrequisites', or 'both'."
            )
        max_depth = max(1, min(max_depth, 10))

        resolved = self.obs_public.resolve_course_code(course_code)
        if resolved.get("code") != course_code:
            # A legacy fuzzy match must not replace the requested course or
            # attach another language variant's name to its prerequisite tree.
            resolved = {"code": course_code, "source": "requested_course_code"}

        result: dict[str, Any] = {
            "course": resolved,
            "direction": direction,
            "max_depth": max_depth,
        }

        if direction in ("prerequisites", "both"):
            raw = self.obs_public.get_prerequisites(
                course_code
            )
            result["prerequisites"] = raw.get("prerequisites") or []
            for key in ("prerequisite_status", "prerequisite_status_note", "requirement_tree",
                        "official_prerequisite", "credit_requirement", "credit_requirement_text",
                        "prerequisite_source"):
                if key in raw:
                    result[key] = raw[key]
            if raw.get("parse_warnings"):
                result["prereq_parse_warnings"] = raw["parse_warnings"]
            if raw.get("raw_tables") and not raw.get("prerequisites"):
                result["prereq_raw_tables"] = raw["raw_tables"]
            if max_depth > 1 and raw.get("prerequisites"):
                result["prerequisite_chain"] = self._build_prereq_chain(
                    resolved, max_depth
                )

        if direction in ("postrequisites", "both"):
            from .interaction import progress_kwargs

            post = self.obs_public.get_postrequisites(
                course_code, **progress_kwargs()
            )
            result["postrequisites"] = post.get("postrequisites") or []
            for key in ("postrequisite_status", "complete", "scanned_courses", "scanned_branches",
                        "unknown_branches", "unscanned_branches", "sources"):
                if key in post:
                    result["postreq_" + key if key != "postrequisite_status" else key] = post[key]
            if post.get("note"):
                result["postreq_note"] = post["note"]
            if max_depth > 1 and post.get("postrequisites"):
                result["postrequisite_chain"] = self._build_postreq_chain(
                    resolved, post.get("postrequisites") or [], max_depth
                )

        result["untrusted_external_content"] = True
        return result

    def _build_prereq_chain(
        self,
        start_course: dict[str, Any],
        max_depth: int,
    ) -> dict[str, Any]:
        """Recursively build a prerequisite adjacency list."""
        nodes: set[str] = set()
        edges: list[dict[str, str]] = []
        visited: set[str] = set()
        unknown_courses: set[str] = set()
        requirements: dict[str, Any] = {}
        unexpanded_courses: set[str] = set()

        start_code = start_course.get("code") or "?"
        queue: list[tuple[str, int]] = [(start_code, 0)]
        nodes.add(start_code)

        while queue:
            current_code, depth = queue.pop(0)
            if current_code in visited:
                continue
            if depth >= max_depth:
                unexpanded_courses.add(current_code)
                continue
            visited.add(current_code)

            try:
                prereq_data = self.obs_public.get_prerequisites(current_code)
            except ObsError:
                unknown_courses.add(current_code)
                continue
            if prereq_data.get("prerequisite_status") == "unknown" or prereq_data.get("available") is False:
                unknown_courses.add(current_code)
            requirements[current_code] = prereq_data.get("requirement_tree")

            for prereq in prereq_data.get("prerequisites") or []:
                prereq_code = prereq.get("code")
                if not prereq_code:
                    continue
                nodes.add(prereq_code)
                edges.append({
                    "from": prereq_code,
                    "to": current_code,
                    "type": prereq.get("type") or "prerequisite",
                })
                if prereq_code not in visited:
                    queue.append((prereq_code, depth + 1))

        return {
            "nodes": sorted(nodes),
            "edges": edges,
            "source": "prerequisite_chain",
            "requirements": requirements,
            "complete": not unknown_courses and not unexpanded_courses,
            "complete_within_requested_depth": not unknown_courses,
            "depth_limited": bool(unexpanded_courses),
            "unexpanded_courses": sorted(unexpanded_courses),
            "unknown_courses": sorted(unknown_courses),
            "max_depth": max_depth,
            "note": "Edges reference courses in the requirement expressions; they are not all mandatory. Coverage is limited to max_depth.",
        }

    def _build_postreq_chain(
        self,
        start_course: dict[str, Any],
        direct_postreqs: list[dict[str, Any]],
        max_depth: int,
    ) -> dict[str, Any]:
        """Recursively build a postrequisite adjacency list."""
        nodes: set[str] = set()
        edges: list[dict[str, Any]] = []
        visited: set[str] = set()
        unknown_courses: set[str] = set()
        unexpanded_courses: set[str] = set()

        start_code = start_course.get("code") or "?"
        queue: list[tuple[str, int]] = [(start_code, 0)]
        nodes.add(start_code)

        while queue:
            current_code, depth = queue.pop(0)
            if current_code in visited:
                continue
            if depth >= max_depth:
                unexpanded_courses.add(current_code)
                continue
            visited.add(current_code)

            try:
                post_data = self.obs_public.get_postrequisites(current_code)
            except ObsError:
                unknown_courses.add(current_code)
                continue
            if post_data.get("complete") is False or post_data.get("available") is False:
                unknown_courses.add(current_code)

            for postreq in post_data.get("postrequisites") or []:
                postreq_code = postreq.get("code")
                if not postreq_code:
                    continue
                nodes.add(postreq_code)
                edges.append({
                    "from": current_code,
                    "to": postreq_code,
                    "type": postreq.get("prerequisite_type") or "postrequisite",
                    "requirement_tree": postreq.get("requirement_tree"),
                })
                if postreq_code not in visited:
                    queue.append((postreq_code, depth + 1))

        return {
            "nodes": sorted(nodes),
            "edges": edges,
            "source": "postrequisite_chain",
            "complete": not unknown_courses and not unexpanded_courses,
            "complete_within_requested_depth": not unknown_courses,
            "depth_limited": bool(unexpanded_courses),
            "unexpanded_courses": sorted(unexpanded_courses),
            "unknown_courses": sorted(unknown_courses),
            "max_depth": max_depth,
            "note": "Edges show references in full prerequisite expressions, not guaranteed eligibility. Coverage is limited to max_depth and the reported branch scans.",
        }

    def get_academic_calendar(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        category: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        """Read the İTÜ academic calendar (public, no login needed)."""
        from datetime import date, datetime
        from zoneinfo import ZoneInfo

        from .academic_calendar import TIMEZONE, default_event_window
        from .planning import filter_academic_calendar

        for value in (date_from, date_to):
            if value:
                date.fromisoformat(value)  # reject a bad date before any request
        calendar = self.obs_public.get_academic_calendar(date_from=date_from, date_to=date_to)
        result = filter_academic_calendar(
            calendar,
            date_from=date_from,
            date_to=date_to,
            category=category,
            query=query,
        )
        fetch_warning = calendar.get("coverage_warning")
        if fetch_warning and fetch_warning not in str(result.get("coverage_warning") or ""):
            result["coverage_warning"] = f"{fetch_warning} {result.get('coverage_warning') or ''}".strip()
        if not (date_from or date_to or category or query) and calendar.get("coverage", {}).get("scope") == "academic_year":
            # A whole academic year is several hundred rows; with no filter
            # only the recent and upcoming ones are listed.
            start, end = default_event_window(datetime.now(ZoneInfo(TIMEZONE)).date())
            result["events"] = [
                event for event in result["events"]
                if event["start_date"] <= end.isoformat() and event["end_date"] >= start.isoformat()
            ]
            result["event_count"] = len(result["events"])
            result["default_window"] = {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "note": "No filter was given, so only events in this window are listed. Pass date_from/date_to, category or query to search the whole academic year.",
            }
        return result

    def get_public_course_schedule(
        self,
        program_type: str,
        department_code: str,
        crn: str | int | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Read the public OBS course schedule for a department (no login needed).

        ``program_type``: ``"LS"`` / ``"Lisans"``, ``"LU"`` / ``"Lisansüstü"``,
        ``"ÖL"`` / ``"Önlisans"``, ``"LUİ"``.
        ``department_code``: e.g. ``"BLG"``, ``"BBF"``, ``"EHB"``.
        ``crn``: optional CRN to filter a single course; text or number.
        ``query``: case/accent-insensitive course name or code fragment.
        Filtered/paged calls default to 50 sections. Unfiltered calls are unchanged.
        """
        from .planning import filter_course_schedule

        paging = query is not None or limit is not None or offset != 0
        if paging:
            # Validate before issuing any OBS requests.
            filter_course_schedule({}, query=query, limit=limit, offset=offset)
        crn_text = _text_id(crn)
        if crn_text:
            schedule = self.obs_public.get_course_schedule_by_crn(
                program_type, department_code, crn_text
            )
        else:
            schedule = self.obs_public.get_course_schedule(program_type, department_code)
        if paging:
            return filter_course_schedule(schedule, query=query, limit=limit, offset=offset)
        return schedule

    def get_public_course_prerequisites(
        self,
        brans_kodu: str,
        ders_no: str | int,
    ) -> dict[str, Any]:
        """Read one course's prerequisite rule from the official branch prerequisite table.

        ``brans_kodu``: department code, e.g. ``"BLG"``.
        ``ders_no``: course number, e.g. ``"223E"``; a plain number such as 4901 is accepted.

        No login required. The rule comes from the public OBS prerequisite
        table of the branch (``ObsPublicClient.get_branch_prerequisites``),
        not from the course's ``/public/DersBilgi`` page.
        """
        number = _text_id(ders_no)
        if not number:
            raise ObsError("ders_no boş olamaz; '223E' ya da 4901 gibi bir ders numarası verin.")
        return self.obs_public.get_prerequisite_detail(brans_kodu, number)

    def get_public_exam_schedule(self, department_code: str) -> dict[str, Any]:
        """Read the current public final-exam schedule for a course branch."""
        return self.itu_public.get_final_exam_schedule(department_code)

    def find_open_course_sections(
        self,
        department_codes: list[str],
        program_type: str = "LS",
        min_available_seats: int = 1,
        query: str | None = None,
        program_code: str | None = None,
    ) -> dict[str, Any]:
        """Find sections with seats the student can take, across public OBS schedules.

        A section's free seats can be reserved for named programmes. Without
        ``program_code`` only the general pool counts; with it (``"CEN_LS"``
        or ``"CEN"``) the programme's own remaining quota is added.
        """
        departments = list(dict.fromkeys(code.strip().upper() for code in department_codes if code.strip()))
        if not departments:
            raise ObsError("department_codes boş olamaz.")
        if len(departments) > 25:
            raise ObsError("Tek çağrıda en fazla 25 bölüm taranabilir.")
        schedules = [self.obs_public.get_course_schedule(program_type, code) for code in departments]
        from .planning import find_open_sections

        try:
            return find_open_sections(
                schedules,
                min_available_seats=min_available_seats,
                query=query,
                program_code=program_code,
            )
        except ValueError as exc:
            raise ObsError(str(exc)) from exc

    def find_empty_classrooms(
        self,
        department_codes: list[str],
        day: str,
        time: str | int,
        program_type: str = "LS",
        building: str | None = None,
    ) -> dict[str, Any]:
        """Estimate empty rooms from selected public department schedules.

        ``day``: Turkish or English weekday name or abbreviation.
        ``time``: ``10:00``, ``9:45``, ``10.00``, ``0945`` or just ``10``.
        """
        from .schedule_utils import normalize_day, parse_time_point

        try:
            # Checked before any OBS request, so a typo costs no network call.
            normalize_day(day)
            parse_time_point(time)
        except ValueError as exc:
            raise ObsError(str(exc)) from exc
        departments = list(dict.fromkeys(code.strip().upper() for code in department_codes if code.strip()))
        if not departments:
            raise ObsError("department_codes boş olamaz.")
        if len(departments) > 25:
            raise ObsError("Tek çağrıda en fazla 25 bölüm taranabilir.")
        schedules = [self.obs_public.get_course_schedule(program_type, code) for code in departments]
        from .planning import find_empty_classrooms

        return find_empty_classrooms(
            schedules,
            day=day,
            time=str(time),
            building=building,
        )
