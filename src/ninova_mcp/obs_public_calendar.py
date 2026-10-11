"""The İTÜ academic calendar pages, read through the public OBS transport."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .client import DEFAULT_HEADERS
from .obs_errors import ObsError
from .obs_public_transport import ObsPublicTransport


class ObsPublicCalendar(ObsPublicTransport):
    """Academic calendar by academic year, in English and Turkish."""

    # -- academic calendar (takvim.sis.itu.edu.tr) -----------------------

    # The page lists one whole academic year. The Turkish address is the one
    # the English page links to as its other language.
    ACADEMIC_CALENDAR_URLS = {
        "en": "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/EN/academic-calendar/index.php",
        "tr": "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/TR/akademik-takvim/index.php",
    }
    # Each academic year costs two requests (English and Turkish) when not cached.
    MAX_CALENDAR_ACADEMIC_YEARS = 2

    def get_academic_calendar(self, date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
        """Fetch the İTÜ academic calendar (public, no auth).

        Without dates the academic year the site shows by default is returned.
        With dates, the academic years those dates fall in are read instead,
        at most ``MAX_CALENDAR_ACADEMIC_YEARS`` of them; what could not be
        read is named in ``coverage_warning`` and ``missing_academic_years``.
        Every page is cached per language and year.
        """
        from datetime import date as _date

        from .academic_calendar import TIMEZONE, academic_years_for_range, combine_calendars, merge_calendar_languages
        from zoneinfo import ZoneInfo

        current = self._calendar_page("en", None)
        if current.get("coverage", {}).get("scope") != "academic_year" or not current.get("academic_year"):
            # No year list on the page: only what the page itself shows is known.
            return current
        today = datetime.now(ZoneInfo(TIMEZONE)).date()
        try:
            start = _date.fromisoformat(date_from) if date_from else None
            end = _date.fromisoformat(date_to) if date_to else None
        except ValueError:
            start = end = None
        wanted = [current["academic_year"]]
        if start or end:
            first, last = start or min(end, today), end or max(start, today)  # type: ignore[type-var]
            wanted = academic_years_for_range(first, last) if first <= last else wanted
        warnings: list[str] = []
        chosen = wanted
        if len(wanted) > self.MAX_CALENDAR_ACADEMIC_YEARS:
            def distance(label: str) -> int:
                return abs((_date(int(label[:4]) + 1, 2, 1) - today).days)
            chosen = sorted(sorted(wanted, key=distance)[: self.MAX_CALENDAR_ACADEMIC_YEARS])
            warnings.append(
                f"The requested dates span {len(wanted)} academic years; at most {self.MAX_CALENDAR_ACADEMIC_YEARS} "
                f"are read per call. Not read: {', '.join(y for y in wanted if y not in chosen)}."
            )
        ids = {item["academic_year"]: item["id"] for item in current.get("available_academic_years") or []}
        pages: list[dict[str, Any]] = []
        for label in chosen:
            if label == current["academic_year"]:
                english = current
                year_id = None
            elif label in ids:
                year_id = ids[label]
                try:
                    english = self._calendar_page("en", year_id)
                except ObsError as exc:
                    warnings.append(f"The {label} calendar could not be read: {exc}")
                    continue
                if english.get("academic_year") != label:
                    warnings.append(f"The site did not return the {label} calendar.")
                    continue
            else:
                warnings.append(f"The {label} academic year is not offered by the calendar page (older years are in its archive).")
                continue
            try:
                turkish = self._calendar_page("tr", year_id)
            except ObsError:
                turkish = {}  # reported through "languages" and "language_note"
            if turkish.get("academic_year") == label:
                english = merge_calendar_languages(english, turkish)
            pages.append(english)
        return combine_calendars(pages, warnings=warnings, requested_years=wanted, today=today)

    def _calendar_page(self, language: str, year_id: str | None) -> dict[str, Any]:
        from .parsing import extract_academic_calendar

        if year_id is not None and not year_id.isdigit():
            raise ObsError("Academic calendar year id must be numeric")
        cache_key = f"academic_calendar:{language}:{year_id or 'current'}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        url = self.ACADEMIC_CALENDAR_URLS[language]
        if year_id is not None:
            # The same address the page's own year selector navigates to.
            url += f"?akademikyil={year_id}&takvimadi=&katagori=&baslangic=&bitis="
        self._throttle()
        resp = self._safe_request("GET", url, timeout=30, headers={
            "User-Agent": DEFAULT_HEADERS.get("User-Agent", "itu-mcp"),
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
        })
        if resp.status_code >= 400:
            raise ObsError(f"Academic calendar returned HTTP {resp.status_code}")
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or resp.encoding
        result = extract_academic_calendar(resp.text, resp.url)
        return self._cache.set(cache_key, result)
