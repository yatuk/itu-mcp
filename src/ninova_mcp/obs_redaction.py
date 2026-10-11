"""Redaction of personal fields in OBS profile and contact payloads."""

from __future__ import annotations

import copy
import re
from typing import Any

from .parsing import clean_text, normalize_lookup_text

REDACTED = "***REDACTED***"
# Field names are matched without case or diacritics, so one pattern covers
# kimlikNo, tcKimlikNo, ituEPosta, cepTelefonu, dogumTarihi and the like.
_SENSITIVE_KEY = re.compile(
    r"kimlikno|tckimlik|tckn|pasaport|passport"
    r"|eposta|email|mail"
    r"|telefon|phone|gsm|mobil|cepno|ceptel|tel(?:no)?$"
    r"|adres|address|ikamet"
    r"|dogum|birth"
    r"|anaadi|anneadi|babaadi|mothername|fathername"
)
_SENSITIVE_CONTACT_TYPE = (
    "telefon", "phone", "cep", "mobile", "gsm", "adres", "address", "ikamet",
    "e-posta", "eposta", "e-mail", "email", "mail",
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_REDACTION_NOTE = (
    "The fields named in redacted_fields exist and are hidden. They are returned only when the "
    "operator set NINOVA_ALLOW_SENSITIVE_OUTPUT and include_sensitive=true is passed."
)
_CONTACT_LIST_KEY = "iletisimBilgiList"


def redact_obs_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Hide personal fields in OBS profile and contact payloads.

    Hidden: national and passport numbers, e-mail addresses, phone numbers,
    addresses, birth date and place, parents' names. The student's own name
    and student number stay visible. The result names what was hidden in
    ``redacted_fields`` so a reader knows the fields exist.
    """
    data = copy.deepcopy(payload)
    if not isinstance(data, dict):
        return data
    hidden: list[str] = []
    _scrub(data, "", hidden)
    data["redacted_fields"] = hidden
    if hidden:
        data["redaction_note"] = _REDACTION_NOTE
    return data


def _note_hidden(hidden: list[str], name: str) -> None:
    if name not in hidden:
        hidden.append(name)


def _scrub(node: Any, path: str, hidden: list[str]) -> Any:
    """Redact everything below ``node`` (records in place) and return its redacted form."""
    if isinstance(node, dict):
        for key in list(node):
            here = f"{path}.{key}" if path else str(key)
            node[key] = _scrub_field(key, node[key], here, hidden)
        return node
    if isinstance(node, list):
        return [_scrub(item, path, hidden) for item in node]
    if isinstance(node, str) and _EMAIL.search(node):
        _note_hidden(hidden, path)
        return _EMAIL.sub(REDACTED, node)
    return node


def _scrub_field(key: Any, value: Any, path: str, hidden: list[str]) -> Any:
    """Redacted value of one record field, decided by the field's name."""
    if key == _CONTACT_LIST_KEY:
        return _scrub_contacts(value, hidden)
    if not _SENSITIVE_KEY.search(normalize_lookup_text(str(key))):
        return _scrub(value, path, hidden)
    if value in (None, "", [], {}):
        return value
    _note_hidden(hidden, path)
    return REDACTED


def _scrub_contacts(node: Any, hidden: list[str]) -> Any:
    """Redact contact rows, which carry their kind in a label and not in the field name."""
    if isinstance(node, list):
        return [_scrub_contacts(item, hidden) for item in node]
    if not isinstance(node, dict):
        return node
    if _CONTACT_LIST_KEY in node:
        node[_CONTACT_LIST_KEY] = _scrub_contacts(node[_CONTACT_LIST_KEY], hidden)
        return node
    label = str(node.get("iletisimTipiAdiTR") or node.get("iletisimTipiAdiEN") or "")
    value = node.get("degeri")
    if value and _is_sensitive_contact(label, value):
        node["degeri"] = REDACTED
        _note_hidden(hidden, f"{_CONTACT_LIST_KEY}: {clean_text(label) or 'unlabelled'}")
    return node


def _is_sensitive_contact(label: str, value: Any) -> bool:
    kind = label.casefold()
    if any(token in kind for token in _SENSITIVE_CONTACT_TYPE):
        return True
    return isinstance(value, str) and bool(_EMAIL.search(value))
