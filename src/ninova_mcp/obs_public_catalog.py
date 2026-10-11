"""Public OBS course catalog: branch index, course search, code resolution."""

from __future__ import annotations

from typing import Any

from .obs_errors import ObsError
from .obs_public_transport import ObsPublicTransport
from .parsing import (
    clean_text,
    extract_course_search_results,
    extract_course_select_options,
    normalize_lookup_text,
)


def _branch_index_match(index: dict[str, int], raw: str, target: str) -> dict[str, Any] | None:
    """Resolve through the branch index: the key itself, or the first key the code starts with."""
    if target in index:
        return {
            "code": raw.upper(),
            "brans_kodu_id": index[target],
            "source": "dept_index",
        }
    # Also try prefix match (e.g. "BBF 201E" → department code "bbf").
    for key, value in index.items():
        if len(key) >= 2 and target.startswith(key):
            return {
                "code": raw.upper(),
                "brans_kodu_id": value,
                "matched_key": key,
                "source": "dept_index_prefix",
            }
    return None


def _catalog_match(results: list[dict[str, Any]], raw: str, target: str) -> dict[str, Any]:
    """Resolve through catalog search results: the exact code, else the first result."""
    exact = next(
        (item for item in results if normalize_lookup_text(item.get("code") or "") == target), None,
    )
    best = exact if exact is not None else results[0]
    return {
        "code": best.get("code") or raw.upper(),
        "name": best.get("name"),
        "brans_kodu_id": None,
        "url": best.get("url"),
        "source": "search_exact" if exact is not None else "search_fuzzy",
    }


class ObsPublicCatalog(ObsPublicTransport):
    """Branch index and course lookups of the public catalog."""

    # -- course index -----------------------------------------------------

    def _build_course_index(self) -> dict[str, int]:
        """Fetch and parse the master course-select dropdown.

        Returns a mapping of normalised course code → ``DersBransKoduId``.
        Cached for the session.
        """
        cached = self._cache.get("course_index")
        if cached is not None:
            return cached  # type: ignore[return-value]

        html, url = self._get_html("/public/GenelTanimlamalar/DersOnsartList")
        options = extract_course_select_options(html, url)
        index: dict[str, int] = {}
        for opt in options:
            text = opt.get("text") or ""
            value_str = opt.get("value") or ""
            try:
                numeric_id = int(value_str)
            except (ValueError, TypeError):
                continue

            # The option text is usually "DEPARTMENT_CODE - Department Name".
            # Store multiple variations so lookups succeed with or without spaces,
            # trailing letters, etc.
            parts = text.split("-", 1)
            dept_code = clean_text(parts[0]) if parts else ""
            if dept_code:
                index[normalize_lookup_text(dept_code)] = numeric_id

            # Also index the raw value as key for partial-match lookups later.
            index[value_str] = numeric_id

        self._cache.set("course_index", index)
        self._course_index = index
        return index

    # -- course search ----------------------------------------------------

    def search_courses(self, query: str) -> list[dict[str, Any]]:
        """Read the public course information form's actual result endpoint."""
        from .archive import split_course_code

        try:
            branch, number = split_course_code(query)
        except ValueError as exc:
            raise ObsError("A full course code is required for an OBS catalog lookup.") from exc
        html, url = self._get_html(
            "/public/DersBilgi/DersBilgiSearch",
            params={"bransKodu": branch, "dersNo": number},
        )
        results = extract_course_search_results(html, url)
        if not results:
            raise ObsError("OBS did not return recognizable course information for this code.")
        exact = f"{branch} {number}"
        results.sort(key=lambda row: row.get("code") != exact)
        return results

    # -- course info (public) ---------------------------------------------

    def get_course_info_public(self, course_code: str) -> dict[str, Any]:
        """Return public catalog metadata for a course code."""
        results = self.search_courses(course_code)
        target = normalize_lookup_text(course_code)
        best: dict[str, Any] | None = None
        for item in results:
            item_code = normalize_lookup_text(item.get("code") or "")
            if target == item_code:
                best = item
                break
            if target in item_code and best is None:
                best = item
        if best is None and results:
            best = results[0]
        return {
            "query": course_code,
            "found": best is not None,
            "course": best,
            "all_results_count": len(results),
        }

    # -- code resolution ---------------------------------------------------

    def resolve_course_code(self, course_code: str) -> dict[str, Any]:
        """Resolve a course code like ``"BBF 201E"`` to an OBS bransKoduId.

        Returns ``{"code": str, "brans_kodu_id": int, "name": str, ...}``.
        Raises ``ObsError`` if no match is found.
        """
        raw = course_code.strip()
        target = normalize_lookup_text(raw)
        # Try the department-level index first (quick).
        from_index = _branch_index_match(self._build_course_index(), raw, target)
        if from_index is not None:
            return from_index
        # Fallback: search the catalog.
        results = self.search_courses(raw)
        if not results:
            raise ObsError(
                f"Course not found in OBS public catalog: {raw}. "
                "Try a different code format (e.g. 'BBF 201E' vs 'BBF201E')."
            )
        return _catalog_match(results, raw, target)
