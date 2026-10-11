"""Public OBS prerequisite rules: per course, per branch and reverse lookup."""

from __future__ import annotations

from typing import Any, Callable

from .obs_errors import ObsError
from .obs_public_catalog import ObsPublicCatalog
from .parsing import extract_prerequisite_list, normalize_lookup_text


class ObsPublicPrerequisites(ObsPublicCatalog):
    """Prerequisites of a course and the courses that require it."""

    # -- prerequisites ----------------------------------------------------

    def get_prerequisites(self, course_id: int | str) -> dict[str, Any]:
        """Read an exact course rule, preserving its AND/OR expression.

        Numeric identifiers refer to branches, not individual courses. Legacy
        numeric calls return branch data with an explicit unknown course verdict.
        """
        from .archive import split_course_code

        cid = str(course_id).strip()
        if cid.isascii() and cid.isdigit():
            html, url = self._get_html(
                "/public/GenelTanimlamalar/OnsartAra",
                params={"DersBransKoduId": cid},
            )
            parsed = extract_prerequisite_list(html, url, base_url=self.base_url)
            parsed.update({
                "course_id": cid,
                "scope": "branch",
                "available": bool(parsed.get("prerequisites") or parsed.get("raw_tables")),
                "prerequisite_status": "unknown",
                "prerequisite_status_note": "A branch ID cannot identify a course's prerequisites. Supply the full course code.",
            })
            return parsed
        try:
            branch, number = split_course_code(cid)
        except ValueError as exc:
            raise ObsError("A full course code such as BLG 223E is required.") from exc
        return self.get_prerequisite_detail(branch, number)

    def get_postrequisites(
        self, course_id: int | str,
        progress: Callable[[float, float | None, str | None], None] | None = None,
    ) -> dict[str, Any]:
        """Find exact course references in official branch prerequisite rules.

        ``progress`` is an optional ``(done, total, message)`` callback, called
        once per branch of a cold scan. Its failures are ignored.
        """
        from .archive import normalize_course_code
        from .prerequisites import flatten_courses

        try:
            target_code = normalize_course_code(str(course_id))
        except ValueError as exc:
            raise ObsError("A full course code is required for postrequisite lookup.") from exc
        cache_key = f"postreq:{target_code}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        index = self._build_course_index()
        branches = sorted({key.upper() for key in index if not key.isdigit()})
        max_scan = 200
        postrequisites: list[dict[str, Any]] = []
        unknown_branches: list[str] = []
        sources: list[str] = []
        scanned_courses = 0
        scan_total = len(branches[:max_scan])
        for position, branch in enumerate(branches[:max_scan]):
            if progress is not None:
                try:
                    progress(position, scan_total, f"Ön şart tablosu taranıyor ({position + 1}/{scan_total}): {branch}")
                except Exception:  # nosec B110
                    # Progress is advisory. The callback is supplied by the
                    # caller and must never change the outcome of the scan.
                    pass
            try:
                data = self.get_branch_prerequisites(branch)
            except ObsError:
                unknown_branches.append(branch)
                continue
            if not data.get("table_parsed"):
                unknown_branches.append(branch)
                continue
            if data.get("url"):
                sources.append(data["url"])
            for code, rule in (data.get("rules") or {}).items():
                scanned_courses += 1
                matches = [leaf for leaf in flatten_courses(rule.get("requirement_tree"))
                           if leaf.get("code") == target_code]
                if matches:
                    postrequisites.append({
                        "code": code,
                        "course_id": code,
                        "name": rule.get("course_name"),
                        "prerequisite_type": "prerequisite_reference",
                        "group": None,
                        "requirement_tree": rule.get("requirement_tree"),
                        "expression": rule.get("expression"),
                        "minimum_grades": rule.get("minimum_grades"),
                        "credit_requirement": rule.get("credit_requirement"),
                        "credit_requirement_text": rule.get("credit_requirement_text"),
                    })
        if progress is not None:
            try:
                progress(scan_total, scan_total, "Ön şart taraması tamamlandı")
            except Exception:  # nosec B110
                # Advisory, as above.
                pass
        complete = bool(branches) and not unknown_branches and len(branches) <= max_scan
        result: dict[str, Any] = {
            "course_id": target_code,
            "course_code": target_code,
            "available": bool(sources),
            "complete": complete,
            "postrequisite_status": "has_postrequisites" if postrequisites else "no_postrequisites" if complete else "unknown",
            "postrequisites": sorted(postrequisites, key=lambda row: row["code"]),
            "scanned_courses": scanned_courses,
            "scanned_branches": len(branches[:max_scan]),
            "unknown_branches": unknown_branches,
            "unscanned_branches": branches[max_scan:],
            "sources": sources,
            "note": "These courses reference the selected course in their prerequisite expressions. Other alternatives and minimum grades still apply. "
                    + ("All listed branches were checked." if complete else "Coverage is incomplete; an empty result does not prove no postrequisites."),
            "untrusted_external_content": True,
        }
        self._cache.set(cache_key, result)
        return result

    def get_prerequisite_detail(
        self,
        brans_kodu: str,
        ders_no: str,
    ) -> dict[str, Any]:
        """Read the authoritative branch rule for an exact course code."""
        from .archive import normalize_course_code, split_course_code
        from .prerequisites import flatten_courses

        try:
            code = normalize_course_code(f"{brans_kodu} {ders_no}")
            branch, number = split_course_code(code)
        except ValueError as exc:
            raise ObsError("A valid branch and course number are required, such as BLG and 223E.") from exc
        cache_key = f"prereq_detail:{code}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        parsed: dict[str, Any] = {
            "course_id": code,
            "course_code": code,
            "brans_kodu": branch,
            "ders_no": number,
            "title": code,
            "prerequisites": [],
            "requirement_tree": None,
            "prerequisite_status": "unknown",
            "available": False,
            "parse_warnings": None,
            "raw_tables": [],
            "text_excerpt": "",
            "untrusted_external_content": True,
        }
        try:
            branch_rules = self.get_branch_prerequisites(branch)
        except ObsError as exc:
            parsed["prerequisite_status_note"] = (
                f"The official branch prerequisite table could not be read: {exc}. An empty result is not evidence of no prerequisites."
            )
        else:
            rule = branch_rules.get("rules", {}).get(code)
            parsed["url"] = branch_rules.get("url")
            parsed["prerequisite_source"] = branch_rules.get("url")
            if rule is not None:
                leaves = flatten_courses(rule.get("requirement_tree"))
                seen: set[tuple[str, str | None]] = set()
                for leaf in leaves:
                    identity = (leaf["code"], leaf.get("min_grade"))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    parsed["prerequisites"].append({
                        "code": leaf["code"], "name": None,
                        "min_grade": leaf.get("min_grade"),
                        "type": "prerequisite_reference", "group": None,
                    })
                parsed["available"] = True
                parsed["prerequisite_status"] = "has_prerequisites" if leaves or rule.get("credit_requirement") is not None else "unknown"
                parsed["official_prerequisite"] = rule
                parsed["requirement_tree"] = rule.get("requirement_tree")
                parsed["credit_requirement"] = rule.get("credit_requirement")
                parsed["credit_requirement_text"] = rule.get("credit_requirement_text")
                parsed["text_excerpt"] = rule.get("expression") or ""
                parsed["prerequisite_status_note"] = "The prerequisite list contains references, not a list of courses that are all mandatory. Use the full requirement_tree for AND/OR alternatives and minimum grades."
            elif branch_rules.get("table_parsed"):
                # An invalid code is also absent from the prerequisite table.
                # Verify its catalog identity before claiming it has no rule.
                try:
                    catalog = self.search_courses(code)
                except ObsError:
                    catalog = []
                if any(row.get("code") == code for row in catalog):
                    parsed["available"] = True
                    parsed["prerequisite_status"] = "no_prerequisites"
                    parsed["prerequisite_status_note"] = f"{code} exists in the official catalog and has no entry in the parsed {branch} prerequisite table."
                else:
                    parsed["prerequisite_status_note"] = "The course is absent from the prerequisite table, but its exact catalog identity could not be verified."
            else:
                parsed["prerequisite_status_note"] = "The official branch prerequisite table could not be parsed; an empty result is not evidence of no prerequisites."
        if parsed["prerequisite_status"] != "unknown":
            self._cache.set(cache_key, parsed)
        return parsed

    def get_branch_prerequisites(self, branch: str) -> dict[str, Any]:
        """Return the official prerequisite rules for every course in a branch.

        Reads ``/public/GenelTanimlamalar/OnsartAra``, the same table the OBS
        "Önşartlar" page renders. This is the only public source that gives the
        full Ve/Veya expression with minimum grades and the credit requirement,
        and it covers 4-digit capstone codes that the per-course page does not.
        """
        key = branch.strip().upper()
        cache_key = f"branch_prereq:{key}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        index = self._build_course_index()
        branch_id = index.get(normalize_lookup_text(key))
        if branch_id is None:
            raise ObsError(f"Branş kodu OBS önşart listesinde bulunamadı: {branch!r}")

        html, url = self._get_html(
            "/public/GenelTanimlamalar/OnsartAra",
            params={"DersBransKoduId": branch_id},
        )

        from .prerequisites import extract_branch_prerequisites

        parsed = extract_branch_prerequisites(html, url, key)
        parsed["brans_kodu_id"] = branch_id
        self._cache.set(cache_key, parsed)
        return parsed
