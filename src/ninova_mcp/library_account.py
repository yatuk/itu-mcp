"""Read-only patron account for the current İTÜ Library catalog (Sirsi Portfolio).

The library account is separate from Ninova: it signs in with the university
number and a library PIN (``NINOVA_LIBRARY_ID`` / ``NINOVA_LIBRARY_PIN``). The
PIN is sent only to ``katalog.kutuphane.itu.edu.tr`` and only to the catalog's
own sign-in form. This client owns a private session so account cookies never
reach the anonymous public client, and it reads the account page only: it never
renews, reserves or changes anything.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import requests

from .client import DEFAULT_HEADERS, DEFAULT_TIMEOUT, NinovaError
from .http_security import request_with_safe_redirects, require_https_host
from .parsing_common import clean_text, make_soup

CATALOG_HOST = "katalog.kutuphane.itu.edu.tr"
ROOT = f"https://{CATALOG_HOST}/client/tr_TR/default"
ACCOUNT_URL = f"{ROOT}/search/account?dt=list"
LOGIN_ACTION_MARKER = "patronloginform.loginpageform"
MAX_ITEMS = 100
ROW_TEXT_CHARS = 300
_RECORD_RE = re.compile(r"SD_ILS:(\d{1,12})")
_NUMBER_RE = re.compile(r"\d+")
_AMOUNT_RE = re.compile(r"\d[\d.,]*\s*[A-Za-z]{0,3}")
_STATUS_RE = re.compile(r"Stat(?:ü|u)n(?:ü|u)z:\s*(\S+)", re.IGNORECASE)
_DATE_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b")


class LibraryAccountError(NinovaError):
    """Library account sign-in or read error."""


def _validate_url(url: str) -> None:
    try:
        require_https_host(url, {CATALOG_HOST})
    except ValueError as exc:
        raise LibraryAccountError("Only the official İTÜ Library catalog host is allowed.") from exc
    path = urlsplit(url).path
    if path != "/client/tr_TR/default" and not path.startswith("/client/tr_TR/default/"):
        raise LibraryAccountError("Only the catalog's own pages are allowed.")


def record_id_from_href(href: str) -> str | None:
    match = _RECORD_RE.search(unquote(href))
    return f"SD_ILS:{match.group(1)}" if match else None


class LibraryAccountClient:
    def __init__(self, *, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        # A private, anonymous-by-default session: no ambient proxies, .netrc
        # or inherited cookies. Cookies set by the catalog itself are kept.
        self.session.trust_env = False
        self.session.headers.update(DEFAULT_HEADERS)
        self._logged_in = False
        self._fresh_page: tuple[str, str] | None = None

    @staticmethod
    def _credentials() -> tuple[str, str]:
        university_id = (os.getenv("NINOVA_LIBRARY_ID") or "").strip()
        pin = os.getenv("NINOVA_LIBRARY_PIN") or ""
        if not university_id or not pin:
            raise LibraryAccountError(
                "Set NINOVA_LIBRARY_ID (university number) and NINOVA_LIBRARY_PIN (library PIN). "
                "The library account does not use the Ninova password."
            )
        return university_id, pin

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        try:
            return request_with_safe_redirects(
                self.session, method, url, validate_url=_validate_url, body_hosts={CATALOG_HOST}, **kwargs
            )
        except requests.RequestException as exc:
            raise LibraryAccountError(f"Library catalog request failed: {exc.__class__.__name__}") from exc

    @staticmethod
    def _is_account_page(html: str) -> bool:
        return 'id="accountTabs"' in html or 'id="accountMain"' in html

    def login(self) -> None:
        university_id, pin = self._credentials()
        self.session.cookies.clear()
        page = self._request("GET", ACCOUNT_URL)
        if self._is_account_page(page.text):
            self._logged_in = True
            self._fresh_page = (page.text, page.url)
            return
        soup = make_soup(page.text)
        form = soup.select_one("form#loginPageForm")
        if form is None:
            raise LibraryAccountError("Library sign-in form not found; the catalog page may have changed.")
        action = urljoin(page.url, form.get("action") or "")
        if LOGIN_ACTION_MARKER not in action:
            raise LibraryAccountError("Unexpected library sign-in form target; nothing was sent.")
        payload: dict[str, str] = {}
        for node in form.select("input[name]"):
            if (node.get("type") or "text").lower() in {"button", "checkbox", "radio"}:
                continue
            payload[str(node["name"])] = str(node.get("value") or "")
        payload["j_username"] = university_id
        payload["j_password"] = pin
        self._request("POST", action, data=payload, headers={"Referer": page.url})
        account = self._request("GET", ACCOUNT_URL)
        if not self._is_account_page(account.text):
            error = make_soup(account.text).select_one(".loginError")
            detail = clean_text(error.get_text(" ", strip=True)) if error else ""
            raise LibraryAccountError(
                "Library sign-in failed" + (f": {detail}" if detail else "") +
                ". Check the university number and library PIN."
            )
        self._logged_in = True
        self._fresh_page = (account.text, account.url)

    def _account_html(self) -> tuple[str, str]:
        for attempt in (0, 1):
            if not self._logged_in:
                self.login()
            if self._fresh_page is not None:
                # Signing in just fetched the account page; use it instead of asking again.
                page, self._fresh_page = self._fresh_page, None
                return page
            response = self._request("GET", ACCOUNT_URL)
            if self._is_account_page(response.text):
                return response.text, response.url
            self._logged_in = False
            if attempt == 1:
                break
        raise LibraryAccountError("Library session could not be established.")

    @staticmethod
    def _summary(soup: Any, zone_id: str, *, amount: bool = False) -> Any:
        zone = soup.select_one(f"#{zone_id}")
        text = clean_text(zone.get_text(" ", strip=True)) if zone else ""
        if amount:
            match = _AMOUNT_RE.search(text.split(":", 1)[-1])
            return match.group(0).strip() if match else None
        match = _NUMBER_RE.search(text)
        return int(match.group(0)) if match else None

    @staticmethod
    def _items(soup: Any, tab_id: str, base: str) -> list[dict[str, Any]]:
        tab = soup.select_one(f"#{tab_id}")
        if tab is None:
            return []
        items: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for link in tab.select("a[href*='SD_ILS'], a[href*='detailnonmodal']"):
            href = urljoin(base, link.get("href") or "")
            record = record_id_from_href(href)
            title = clean_text(link.get_text(" ", strip=True))
            if not record or not title or (record, title) in seen:
                continue
            seen.add((record, title))
            row = link.find_parent("tr") or link.find_parent(
                "div", class_=re.compile(r"checkout|hold|fine|item|row", re.I)
            )
            row_text = clean_text(row.get_text(" ", strip=True)) if row else title
            items.append(
                {
                    "record_id": record,
                    "title": title,
                    "dates": _DATE_RE.findall(row_text)[:4],
                    "details": row_text[:ROW_TEXT_CHARS],
                }
            )
            if len(items) >= MAX_ITEMS:
                break
        return items

    def get_account(self) -> dict[str, Any]:
        html, url = self._account_html()
        soup = make_soup(html)
        status = _STATUS_RE.search(soup.get_text(" ", strip=True))
        loans = self._items(soup, "checkoutsTab", url)
        holds = self._items(soup, "holdsTab", url)
        loan_count = self._summary(soup, "checkoutsSummaryZone")
        hold_count = self._summary(soup, "holdsSummaryZone")
        result: dict[str, Any] = {
            "status": status.group(1) if status else None,
            "loan_count": loan_count if loan_count is not None else len(loans),
            "loans": loans,
            "hold_count": hold_count if hold_count is not None else len(holds),
            "holds": holds,
            "fines_total": self._summary(soup, "finesSummaryZone", amount=True),
            "url": ACCOUNT_URL,
            "source": CATALOG_HOST,
            "untrusted_external_content": True,
        }
        warnings = []
        # The catalog fills these lists with a later request from the page's
        # JavaScript. When only the loading placeholder is in the page, an
        # empty list says nothing, so the count is left unknown.
        for key, tab_id in (("loan_count", "checkoutsTab"), ("hold_count", "holdsTab")):
            tab = soup.select_one(f"#{tab_id}")
            if tab is not None and tab.select_one(".myAccountLoadingHolder") and not result[key.replace("_count", "s")]:
                result[key] = None
                warnings.append(
                    f"The catalog loads the {key.split('_')[0]} list separately; it could not be read, "
                    "so the count is unknown. Check the account page."
                )
        if isinstance(loan_count, int) and loan_count > len(loans):
            warnings.append("Fewer loan rows were read than the account reports; open the account page for the rest.")
        if isinstance(hold_count, int) and hold_count > len(holds):
            warnings.append("Fewer hold rows were read than the account reports; open the account page for the rest.")
        if warnings:
            result["parse_warning"] = " ".join(warnings)
        return result

    def list_loans(self) -> dict[str, Any]:
        account = self.get_account()
        keys = ("loan_count", "loans", "fines_total", "url", "source", "untrusted_external_content")
        result = {key: account[key] for key in keys}
        if "parse_warning" in account:
            result["parse_warning"] = account["parse_warning"]
        return result
