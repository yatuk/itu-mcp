"""Read the data used to populate the Portal's card and storage widgets."""

from __future__ import annotations

from typing import Any, Callable

from .client import NinovaError
from .parsing import clean_text

_PORTAL = "https://portal.itu.edu.tr/apps/default/"


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
            "amounts": [f"₺ {value}" for value in (remaining, amount) if value is not None],
            "description": _text(row.get("TypeName")),
        })
    return {
        "url": _PORTAL,
        "balance": f"₺ {balance}",
        "transactions": transactions[:20],
        "transaction_count": len(transactions) if history_available else None,
        "transactions_available": history_available,
        "checked_at": _text(info.get("BalanceGetDate")),
        "source": _PORTAL + "service/service.aspx/GetBalance",
        "untrusted_external_content": True,
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
            info = _information(get_json(operation), "KotaInformation")
            percent = _text(info.get("KotaYuzde"))
            used, total = _text(info.get("KotaKullanilan")), _text(info.get("KotaTotal"))
            item.update({
                "usage_percent": f"%{percent.lstrip('%')}" if percent is not None else None,
                "details": f"Storage used: {used} / {total}" if used is not None and total is not None else None,
                "used": used, "total": total, "checked_at": _text(info.get("KotaGetDate")),
                "available": all(value is not None for value in (percent, used, total)),
            })
            if not item["available"]:
                item["error"] = "The Portal returned incomplete storage quota data."
        except NinovaError:
            item["error"] = "The Portal could not retrieve this storage quota."
        result[name] = item
    return result
