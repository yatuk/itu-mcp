"""Redaction of personal fields in OBS profile and contact payloads."""

from __future__ import annotations

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


def redact_obs_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Hide personal fields in OBS profile and contact payloads.

    Hidden: national and passport numbers, e-mail addresses, phone numbers,
    addresses, birth date and place, parents' names. The student's own name
    and student number stay visible. The result names what was hidden in
    ``redacted_fields`` so a reader knows the fields exist.
    """
    import copy

    data = copy.deepcopy(payload)
    if not isinstance(data, dict):
        return data
    hidden: list[str] = []

    def note(name: str) -> None:
        if name not in hidden:
            hidden.append(name)

    def scrub(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            for key in list(node):
                value = node[key]
                here = f"{path}.{key}" if path else str(key)
                if key == "iletisimBilgiList":
                    node[key] = scrub_contacts(value)
                elif _SENSITIVE_KEY.search(normalize_lookup_text(str(key))):
                    if value not in (None, "", [], {}):
                        node[key] = REDACTED
                        note(here)
                else:
                    node[key] = scrub(value, here)
            return node
        if isinstance(node, list):
            return [scrub(item, path) for item in node]
        if isinstance(node, str) and _EMAIL.search(node):
            note(path)
            return _EMAIL.sub(REDACTED, node)
        return node

    def scrub_contacts(node: Any) -> Any:
        if isinstance(node, list):
            return [scrub_contacts(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "iletisimBilgiList" in node:
            node["iletisimBilgiList"] = scrub_contacts(node["iletisimBilgiList"])
            return node
        label = str(node.get("iletisimTipiAdiTR") or node.get("iletisimTipiAdiEN") or "")
        kind = label.casefold()
        value = node.get("degeri")
        if value and (
            any(token in kind for token in _SENSITIVE_CONTACT_TYPE)
            or (isinstance(value, str) and _EMAIL.search(value))
        ):
            node["degeri"] = REDACTED
            note(f"iletisimBilgiList: {clean_text(label) or 'unlabelled'}")
        return node

    scrub(data, "")
    data["redacted_fields"] = hidden
    if hidden:
        data["redaction_note"] = _REDACTION_NOTE
    return data
