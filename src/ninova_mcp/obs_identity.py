"""The OBS "Kimlik Seçim" page shown to accounts with more than one record.

A student who transferred, or who studies in two programmes, lands on a page
of identity cards after signing in, and OBS hands out no JWT until one card is
opened. Each card carries a small table (Seviye, Bölüm, Öğrenci No, Durum) and
a link to ``/login/SetIdentity``. Staff roles of the same person appear as
cards too, with a different ``data-key``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import Tag

from .obs_errors import ObsError
from .parsing_common import make_soup

STUDENT_NUMBER_ENV = "NINOVA_OBS_STUDENT_NUMBER"
STUDENT_KEY = "ogrenci"
_STUDENT_NUMBER_RE = re.compile(r"(?<!\d)\d{9}(?!\d)")
_OUTLINE_LIMIT = 1800
_FOLD = str.maketrans("İIıŞşĞğÜüÖöÇç", "iiissgguuoocc")


def _fold(text: str) -> str:
    return " ".join(text.translate(_FOLD).lower().split())


@dataclass(frozen=True)
class IdentityOption:
    student_number: str | None
    status: str
    program: str
    href: str | None
    active: bool


def is_identity_page(html: object) -> bool:
    if not isinstance(html, str) or "<" not in html:
        return False
    return "kimlik secim" in _fold(html[:4000]) or "data-select-identity" in html


def _card_fields(card: Tag) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in card.find_all("tr"):
        header, value = row.find("th"), row.find("td")
        if isinstance(header, Tag) and isinstance(value, Tag):
            fields[_fold(header.get_text(" ", strip=True)).rstrip(":").strip()] = " ".join(
                value.get_text(" ", strip=True).split()
            )
    return fields


def identity_options(html: str, page_url: str) -> list[IdentityOption]:
    """The student records on the page, in page order. Other roles are left out."""
    options: list[IdentityOption] = []
    for card in make_soup(html).select(".identity-card"):
        if not isinstance(card, Tag):
            continue
        key = str(card.get("data-key") or "").strip().lower()
        if key and key != STUDENT_KEY:
            continue
        fields = _card_fields(card)
        number_match = _STUDENT_NUMBER_RE.search(fields.get("ogrenci no", "") or card.get_text(" ", strip=True))
        link = card.select_one("a[data-select-identity][href]") or card.find("a", href=True)
        raw = str(link["href"]).strip() if isinstance(link, Tag) else ""
        usable = bool(raw) and not raw.lower().startswith(("#", "javascript:"))
        status = fields.get("durum", "")
        options.append(
            IdentityOption(
                student_number=number_match.group(0) if number_match else None,
                status=status,
                program=fields.get("bolum", ""),
                href=urljoin(page_url, raw) if usable else None,
                active=_fold(status) == "aktif",
            )
        )
    return options


def _listing(options: list[IdentityOption]) -> str:
    shown = [f"{option.student_number or '?'} ({option.status or 'durum yok'})" for option in options]
    return ", ".join(shown) or "okunamadı"


def choose_identity(options: list[IdentityOption], wanted: str | None) -> IdentityOption:
    """The record to open: the configured student number, else the only active one."""
    wanted = (wanted or "").strip()
    if wanted:
        matches = [option for option in options if option.student_number == wanted]
        if len(matches) == 1:
            return matches[0]
        raise ObsError(
            f"{STUDENT_NUMBER_ENV}={wanted} OBS kimlik seçim ekranındaki kayıtlarla eşleşmedi. "
            f"Ekrandaki öğrenci kayıtları: {_listing(options)}."
        )
    active = [option for option in options if option.active]
    if len(active) == 1:
        return active[0]
    raise ObsError(
        "OBS bu hesap için birden fazla öğrenci kaydı gösteriyor ve hangisinin kullanılacağı "
        f"belirlenemedi. {STUDENT_NUMBER_ENV} ayarına kullanmak istediğiniz öğrenci numarasını yazın. "
        f"Ekrandaki öğrenci kayıtları: {_listing(options)}."
    )


def same_site(url: str, base_url: str) -> bool:
    target, base = urlparse(url), urlparse(base_url)
    return target.scheme == "https" and target.hostname == base.hostname


def page_outline(html: str) -> str:
    """Tags, classes and link shapes of the page body with every letter and digit of text masked."""

    def mask(value: str) -> str:
        return re.sub(r"[^\W\d_]", "x", re.sub(r"\d", "9", value))

    lines: list[str] = []
    body = make_soup(html).find("body")
    for tag in body.find_all(True) if isinstance(body, Tag) else []:
        if tag.name in {"script", "style", "svg", "path", "link", "meta", "i", "button"}:
            continue
        parts = [tag.name]
        classes = tag.get("class")
        if classes:
            parts.append("." + ".".join(classes))
        for attribute in ("href", "action", "onclick"):
            if tag.has_attr(attribute):
                shape = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", "UUID", str(tag[attribute]))
                parts.append(f"{attribute}={re.sub(r'[0-9]', '9', shape)[:90]}")
        for attribute in ("name", "type", "method", "data-key"):
            if tag.has_attr(attribute):
                parts.append(f"{attribute}={tag[attribute]}")
        own_text = " ".join(tag.find_all(string=True, recursive=False)).strip()
        if own_text:
            parts.append(f'"{mask(own_text)[:40]}"')
        lines.append(" ".join(parts))
    return "\n".join(lines)[:_OUTLINE_LIMIT]
