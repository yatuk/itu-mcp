"""Conservative coverage checks for Ninova tracking reads.

A successfully fetched page is not necessarily a complete inventory. These
checks leave unknown layouts and unfinished pagination explicit so they cannot
erase the last complete tracking observation.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlparse

from .parsing import _table_after_heading, make_soup, normalize_lookup_text


def coverage(status: str, reason: str | None = None, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status, **details}
    if reason:
        result["reason"] = reason
    return result


def page_coverage(html: str, page_url: str, expected_url: str) -> dict[str, Any]:
    """Reject redirects/login pages and conservatively detect unfinished paging."""
    actual, expected = urlparse(page_url), urlparse(expected_url)
    if (actual.scheme.lower(), actual.netloc.lower(), actual.path.rstrip("/")) != (
        expected.scheme.lower(), expected.netloc.lower(), expected.path.rstrip("/"),
    ):
        return coverage("failed", "unexpected_page_redirect")
    # Ninova folder identity lives in the query string. A redirect to the
    # root or a different folder can retain the path and valid table markup.
    if sorted(parse_qsl(actual.query, keep_blank_values=True)) != sorted(parse_qsl(expected.query, keep_blank_values=True)):
        return coverage("failed", "unexpected_resource_query")
    soup = make_soup(html)
    if soup.select_one('input[type="password"]'):
        return coverage("failed", "login_page")
    text = normalize_lookup_text(soup.get_text(" ", strip=True))
    if any(marker in text for marker in ("erisim yetkiniz bulunmamaktadir", "access denied", "yetkiniz yok")):
        return coverage("failed", "access_denied")
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"])
        if re.search(r"Page\$|[?&](?:page|pageindex|pageno)=", href, re.I):
            return coverage("partial", "pagination_not_traversed")
    for pager in soup.select(".pager, .pagination, [id*='Pager'], [id*='pager']"):
        if pager.find("a", href=True) or pager.find("input", type="submit"):
            return coverage("partial", "pagination_not_traversed")
    return coverage("complete")


_EMPTY_NOUNS = {
    "enrollment": r"(?:ders|sinif)",
    "announcements": r"duyuru",
    "assignments": r"odev",
    "class_files": r"dosya",
    "lesson_files": r"dosya",
    "grades": r"not",
    "message_board": r"mesaj",
    "attendance": r"(?:yoklama|devam)",
    "remote_learning": r"(?:uzaktan egitim|oturum)",
}


def _explicit_empty(text: str, scope: str) -> bool:
    noun = _EMPTY_NOUNS.get(scope)
    if not noun:
        return False
    if scope == "enrollment":
        return bool(re.search(
            r"(?:kayitli oldugunuz|katildiginiz|dahil oldugunuz|aktif)[^.!?]{0,50}"
            + noun + r"[^.!?]{0,50}(?:bulunmamaktadir|bulunamadi|yoktur)", text,
        ))
    # Require the scope and the absence statement together. A generic "no
    # records" in an unrelated sidebar is not proof that this scope is empty.
    return bool(re.search(noun + r"[^.!?]{0,100}(?:bulunmamaktadir|bulunamadi|yoktur)", text))


def enrollment_coverage(html: str, page_url: str, expected_url: str, courses: list[dict[str, Any]]) -> dict[str, Any]:
    result = page_coverage(html, page_url, expected_url)
    if result["status"] != "complete":
        return result
    soup = make_soup(html)
    text = normalize_lookup_text(soup.get_text(" ", strip=True))
    if courses:
        return coverage("unknown", "enrollment_container_not_verified")
    if _explicit_empty(text, "enrollment"):
        return coverage("complete", "explicit_empty_enrollment")
    return coverage("unknown", "course_list_not_recognized")


def scope_coverage(html: str, page_url: str, expected_url: str, scope: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = page_coverage(html, page_url, expected_url)
    if result["status"] != "complete":
        return result
    soup = make_soup(html)
    text = normalize_lookup_text(soup.get_text(" ", strip=True))
    keys = {
        "announcements": ("announcements",), "assignments": ("assignments",),
        "class_files": ("entries",), "lesson_files": ("entries",),
        "grades": ("grades",), "message_board": ("topics",),
        "attendance": ("weeks",), "remote_learning": ("active_sessions", "past_sessions"),
        "info": ("identity", "class_meta", "course_details"),
    }
    count = sum(len(payload.get(key) or []) for key in keys.get(scope, ()))
    if scope in {"announcements", "assignments"} and count > 200:
        return coverage("partial", "item_limit", observed_count=count, item_limit=200)
    # Inspect file rows as well as the successfully parsed subset. One good
    # row alongside an unsupported row must not certify a full directory.
    if scope in {"class_files", "lesson_files"}:
        for table in soup.find_all("table"):
            headers = {normalize_lookup_text(tag.get_text(" ", strip=True)) for tag in table.find_all("th")}
            if not {"dosyalar", "boyut", "tarih"} <= headers:
                continue
            data_rows = 0
            for row in table.find_all("tr"):
                if row.find_parent("table") is not table:
                    continue
                cells = row.find_all("td", recursive=False)
                if not cells:
                    continue
                row_text = normalize_lookup_text(row.get_text(" ", strip=True))
                if _explicit_empty(row_text, scope):
                    continue
                data_rows += 1
                if len(cells) < 3 or not cells[0].find("a", href=True):
                    return coverage("partial", "unparsed_file_rows")
            if data_rows != count:
                return coverage("partial", "file_row_count_mismatch")
            return coverage("complete", "empty_table" if not count else None)
        return coverage("unknown", "file_table_not_recognized")
    if scope == "assignments":
        table = soup.find("table", id=re.compile("gvOdevListesi", re.I))
        if table is None:
            table = soup.find("table", class_=re.compile(r"\bdata\b", re.I))
        if table is None:
            table = next((candidate for candidate in soup.find_all("table")
                          if candidate.select_one("h2 a[href*='Odev'], h2 a[href*='odev']")), None)
        if table is None:
            return coverage("complete", "explicit_empty_scope") if _explicit_empty(text, scope) and not count else coverage("unknown", "assignment_table_not_recognized")
        for row in table.find_all("tr"):
            if row.find_parent("table") is not table or not row.find_all("td", recursive=False):
                continue
            row_text = normalize_lookup_text(row.get_text(" ", strip=True))
            if row_text and not _explicit_empty(row_text, scope) and not row.select_one("h2 a[href]"):
                return coverage("partial", "unparsed_assignment_rows")
        # Also detect an unsupported assignment card beside a valid card in
        # the same row. Upload links refer to their parent assignment.
        def assignment_identity(url: str) -> str:
            return re.sub(r"(/Odev/[^/?#]+).*", r"\1", url, flags=re.I)
        parsed_ids = {assignment_identity(str(item.get("url") or "")) for item in payload.get("assignments") or []}
        for anchor in table.find_all("a", href=True):
            url = urljoin(page_url, anchor["href"])
            if re.search(r"/Odev/", url, re.I) and assignment_identity(url) not in parsed_ids:
                return coverage("partial", "unparsed_assignment_links")
        if len(table.select("h2 a[href]")) != count:
            return coverage("partial", "assignment_card_count_mismatch")
        return coverage("complete", "empty_table" if not count else None)
    matrix_headers = {"grades": {"not"}, "message_board": {"mesaj basligi", "son mesaj"}}.get(scope)
    if matrix_headers:
        table = next((table for table in soup.find_all("table") if matrix_headers <= {
            normalize_lookup_text(header.get_text(" ", strip=True)) for header in table.find_all("th")
        }), None)
        if table is not None:
            data_rows = 0
            for row in table.find_all("tr"):
                if row.find_parent("table") is not table:
                    continue
                cells = row.find_all("td", recursive=False)
                if not cells:
                    continue
                row_text = normalize_lookup_text(row.get_text(" ", strip=True))
                if not row_text or _explicit_empty(row_text, scope):
                    continue
                if len(cells) < 2:
                    return coverage("partial", "unparsed_matrix_rows")
                if scope == "grades" and normalize_lookup_text(cells[0].get_text(" ", strip=True)) == "agirlikli ortalamaniz":
                    continue
                data_rows += 1
            if data_rows != count:
                return coverage("partial", "matrix_row_count_mismatch")
            return coverage("complete", "empty_table" if not count else None)
    if scope == "remote_learning":
        active = _table_after_heading(soup, "Aktif Uzaktan Eğitim Oturumlarınız")
        past = _table_after_heading(soup, "Sınıfın Geçmiş Uzaktan Eğitim Oturumları")
        if active is None or past is None or active is past:
            return coverage("unknown", "remote_session_containers_not_verified")
        for table, key in ((active, "active_sessions"), (past, "past_sessions")):
            rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
            header_count = sum(len(row.find_all("th", recursive=False)) for row in rows)
            data_rows = 0
            for row in rows:
                cells = row.find_all("td", recursive=False)
                if not cells:
                    continue
                row_text = normalize_lookup_text(row.get_text(" ", strip=True))
                if not row_text or _explicit_empty(row_text, scope) or any(marker in row_text for marker in ("no remote learning sessions", "no sessions found")):
                    continue
                if len(cells) != (header_count or 1):
                    return coverage("partial", "unparsed_remote_session_rows")
                data_rows += 1
            if data_rows != len(payload.get(key) or []):
                return coverage("partial", "remote_session_row_count_mismatch")
        return coverage("complete", "empty_tables" if not count else None)
    if scope == "announcements":
        cards = soup.select("div.duyuruGoruntule")
        if cards and len(cards) != count:
            return coverage("partial", "unparsed_announcement_cards")
    if count:
        return result
    if _explicit_empty(text, scope):
        return coverage("complete", "explicit_empty_scope")
    # Known headers without body rows are also an explicit empty inventory.
    required_headers = {
        "class_files": {"dosyalar", "boyut", "tarih"},
        "lesson_files": {"dosyalar", "boyut", "tarih"},
        "grades": {"not"}, "message_board": {"mesaj basligi", "son mesaj"},
    }.get(scope)
    if required_headers:
        for table in soup.find_all("table"):
            headers = {normalize_lookup_text(tag.get_text(" ", strip=True)) for tag in table.find_all("th")}
            if required_headers <= headers and not table.find("td"):
                return coverage("complete", "empty_table")
    return coverage("unknown", "scope_content_not_recognized")
