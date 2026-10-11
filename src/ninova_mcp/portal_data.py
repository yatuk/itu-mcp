"""Read the data used to populate the Portal's card and storage widgets."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

import requests

from .client import NinovaError
from .parsing import clean_text

_PORTAL = "https://portal.itu.edu.tr/apps/default/"
_PORTAL_HOST = "portal.itu.edu.tr"
# Operations whose payload is a list. A bare array is placed under this key.
_LIST_KEYS = {"GetNotification": "NotificationInformationList", "GetYardim": "YardimInformationList"}


class PortalUnreachableError(NinovaError):
    """The Portal host could not be contacted at all (DNS, connection, timeout)."""


def _unreachable(exc: BaseException) -> PortalUnreachableError | None:
    """Recognize a transport failure anywhere in an exception's cause chain."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, PortalUnreachableError):
            return current
        if isinstance(current, (requests.ConnectionError, requests.Timeout)):
            named = re.search(r"host='([a-z0-9.-]+\.itu\.edu\.tr)'", str(current))
            host = named.group(1) if named else _PORTAL_HOST
            return PortalUnreachableError(
                f"Could not reach {host} (network or DNS failure). Nothing was read; try again shortly."
            )
        current = current.__cause__ or current.__context__
    return None


def portal_call(get_json: Callable[..., dict[str, Any]], operation: str) -> dict[str, Any]:
    """Run one Portal read, turning a transport failure into a short error."""
    try:
        return get_json(operation)
    except (NinovaError, requests.RequestException) as exc:
        unreachable = _unreachable(exc)
        if unreachable is None:
            raise
        if unreachable is exc:
            raise
        raise unreachable from exc


def _body_prefix(text: str) -> str:
    """First 200 characters of an unreadable body, safe to show in an error."""
    prefix = " ".join(text[:200].split())
    prefix = re.sub(r"[^\s@<>\"']+@[^\s@<>\"']+", "[address]", prefix)
    prefix = re.sub(r"[0-9]{6,}", "[number]", prefix)
    return prefix


def decode_portal_response(operation: str, response: Any) -> dict[str, Any]:
    """Decode a Portal WebMethod reply into the object the widget script reads.

    The usual shape is ``{"d": {...}}``. Also accepted: a byte order mark, an
    object without the ``d`` wrapper, JSON text carried inside a JSON string,
    raw control characters inside strings, and a bare array for the operations
    that return a list. Anything else raises with the status code, the content
    type and the start of the body, which is what is needed to diagnose it.
    """
    status = getattr(response, "status_code", None)
    content_type = clean_text(str((getattr(response, "headers", None) or {}).get("Content-Type") or "")) or "none"
    raw = getattr(response, "content", None)
    if isinstance(raw, (bytes, bytearray)):
        try:
            text = bytes(raw).decode("utf-8-sig")
        except UnicodeDecodeError:
            text = bytes(raw).decode("latin-1")
    else:
        text = str(getattr(response, "text", "") or "")
    text = text.lstrip("\ufeff").strip()

    def fail(kind: str) -> NinovaError:
        return NinovaError(
            f"Portal {operation} JSON yanıtı ayrıştırılamadı ({kind}; HTTP {status}; "
            f"content type: {content_type[:80]}; body starts: {_body_prefix(text)!r})."
        )

    if not text:
        raise fail("empty body")
    if text[:1] == "<":
        raise fail("HTML page instead of JSON")
    try:
        payload = json.loads(text, strict=False)
    except ValueError as exc:
        raise fail("invalid JSON") from exc
    data = payload["d"] if isinstance(payload, dict) and "d" in payload else payload
    for _ in range(2):
        if not isinstance(data, str):
            break
        try:
            data = json.loads(data.lstrip("\ufeff").strip(), strict=False)
        except ValueError as exc:
            raise fail("a string that is not JSON") from exc
        if isinstance(data, dict) and "d" in data and len(data) == 1:
            data = data["d"]
    if isinstance(data, list) and operation in _LIST_KEYS:
        data = {_LIST_KEYS[operation]: data}
    if not isinstance(data, dict):
        raise NinovaError(
            f"Portal {operation} beklenen 'd' nesnesini döndürmedi "
            f"(got {type(data).__name__}; HTTP {status}; content type: {content_type[:80]})."
        )
    return data


def notifications_from_api(data: Any) -> list[dict[str, Any]]:
    """Normalize GetNotification. Raise when the reply does not carry a list.

    A failed status or a missing list is a failed read, not an empty inbox.
    """
    from .parsing import make_soup

    if not isinstance(data, dict):
        raise NinovaError("Portal GetNotification returned an unrecognized response.")
    status = data.get("StatusCode")
    if status is not None and (isinstance(status, bool) or status not in (0, "0")):
        raise NinovaError("Portal GetNotification reported a failed status.")
    rows = data.get("NotificationInformationList")
    if not isinstance(rows, list):
        raise NinovaError("Portal GetNotification did not include a notification list.")
    notifications = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        content_html = str(item.get("ContentText") or "")
        notifications.append({
            "id": str(item.get("ObjectId") or ""),
            "title": clean_text(str(item.get("Title") or "")),
            "content": clean_text(make_soup(content_html).get_text(" ", strip=True)),
            "created_at": item.get("CreateDate"),
            "age": item.get("BeforeCreateDate"),
            "unread": item.get("IsRead") in {0, "0", False},
        })
    return notifications


def _text(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return clean_text(str(value)) or None


def _information(data: dict[str, Any], key: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise NinovaError("The Portal returned an unrecognized widget response.")
    status = data.get("StatusCode")
    if type(status) not in (int, str) or status not in (0, "0"):
        raise NinovaError("The Portal could not retrieve this widget's data.")
    info = data.get(key)
    if not isinstance(info, dict):
        raise NinovaError("The Portal returned an unrecognized widget response.")
    return info


def campus_card(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize GetBalance without returning account identifiers."""
    info = _information(data, "BalanceInformation")
    balance = _text(info.get("Balance"))
    if balance is None:
        raise NinovaError("The Portal did not return a campus card balance.")
    raw = info.get("Transitions")
    history_available = isinstance(raw, list) and all(isinstance(row, dict) for row in raw)
    transactions = []
    for row in raw if history_available else []:
        remaining, amount = _text(row.get("Balance")), _text(row.get("TransactionAmount"))
        type_id = _text(row.get("TypeId"))
        transactions.append({
            "type": "spending" if type_id in ("1", "10", "20") else "credit" if type_id else None,
            "balance_after": f"₺ {remaining}" if remaining is not None else None,
            "amount": f"₺ {amount}" if amount is not None else None,
            # Kept for older callers: balance after the transaction, then its amount.
            "amounts": [f"₺ {value}" for value in (remaining, amount) if value is not None],
            "description": _text(row.get("TypeName")),
        })
    return {
        "url": _PORTAL,
        "balance": f"₺ {balance}",
        "transactions": transactions[:20],
        "transaction_count": len(transactions) if history_available else None,
        "transactions_available": history_available,
        "transaction_fields_note": "balance_after is the card balance after the transaction; amount is the transaction itself.",
        "checked_at": _text(info.get("BalanceGetDate")),
        "source": _PORTAL + "service/service.aspx/GetBalance",
        "untrusted_external_content": True,
    }


_UNIT_BYTES = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
# The mail widget prints bare numbers. Mailbox quotas are counted in GB; the
# Portal does not say so, which is why this is reported as an assumption.
_ASSUMED_UNITS = {"mail": "GB"}


def _number(value: str | None) -> float | None:
    if value is None or not re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?", value.strip().lstrip("%").strip()):
        return None
    return float(value.strip().lstrip("%").strip().replace(",", "."))


def _amount(value: str | None) -> tuple[float | None, str | None]:
    """Split ``"12 MB"`` or ``"0,18"`` into a number and a stated unit."""
    match = re.fullmatch(r"([0-9]+(?:[.,][0-9]+)?)\s*([KMGT]?B)?", (value or "").strip(), re.IGNORECASE)
    if not match:
        return None, None
    return float(match.group(1).replace(",", ".")), (match.group(2).upper() if match.group(2) else None)


def _quota_units(name: str, used: str | None, total: str | None) -> dict[str, Any]:
    used_value, used_unit = _amount(used)
    total_value, total_unit = _amount(total)
    stated = used_unit is not None and total_unit is not None
    bare = used_unit is None and total_unit is None and used_value is not None and total_value is not None
    assumed = _ASSUMED_UNITS.get(name) if bare else None
    note = None
    if not stated:
        note = "The Portal does not state a unit for this quota."
        if bare:
            note += " Both numbers use the same unit."
        if assumed:
            note += f" {assumed} is assumed for this service."
    return {
        "used_value": used_value, "used_unit": used_unit,
        "total_value": total_value, "total_unit": total_unit,
        "used_bytes": int(used_value * _UNIT_BYTES[used_unit]) if used_value is not None and used_unit else None,
        "total_bytes": int(total_value * _UNIT_BYTES[total_unit]) if total_value is not None and total_unit else None,
        "assumed_unit": assumed, "unit_note": note,
    }


def storage_quota(get_json: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    """Read each storage service independently so one failure preserves the other."""
    result: dict[str, Any] = {"url": _PORTAL, "untrusted_external_content": True}
    for name, operation in (("mail", "GetQuota"), ("cloud", "GetQuotaEski")):
        item: dict[str, Any] = {
            "usage_percent": None, "details": None, "available": False,
            "source": _PORTAL + "service/service.aspx/" + operation,
        }
        try:
            info = _information(portal_call(get_json, operation), "KotaInformation")
            percent = _text(info.get("KotaYuzde"))
            used, total = _text(info.get("KotaKullanilan")), _text(info.get("KotaTotal"))
            units = _quota_units(name, used, total)
            suffix = ""
            if units["unit_note"]:
                suffix = f" ({units['assumed_unit']} assumed, unit not stated)" if units["assumed_unit"] else " (unit not stated)"
            item.update(units)
            item.update({
                "usage_percent": f"%{percent.lstrip('%')}" if percent is not None else None,
                "usage_percent_value": _number(percent),
                "details": f"Storage used: {used} / {total}{suffix}" if used is not None and total is not None else None,
                "used": used, "total": total, "checked_at": _text(info.get("KotaGetDate")),
                "available": all(value is not None for value in (percent, used, total)),
            })
            if not item["available"]:
                item["error"] = "The Portal returned incomplete storage quota data."
        except PortalUnreachableError:
            raise
        except NinovaError:
            item["error"] = "The Portal could not retrieve this storage quota."
        result[name] = item
    return result
