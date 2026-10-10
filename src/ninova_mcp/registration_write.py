"""Explicit OBS draft writes with no redirect, auth replay or transport retry."""
from __future__ import annotations

import copy
import os
import re
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .client import DEFAULT_HEADERS, NinovaError
from .obs_client import ObsError
from .registration_draft import DRAFT_PATH, normalize_crns


SAVE_DRAFT_PATH = DRAFT_PATH + "TaslakOlustur/"


def _result_code(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) else None


def _post_once(obs: Any, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitized acknowledgement, never an inferred retry decision."""
    if obs.base_url != "https://obs.itu.edu.tr" or path != SAVE_DRAFT_PATH:
        raise ObsError("The OBS write destination is not explicitly allowed.")
    # Obtain/refresh credentials through existing reads BEFORE the write. The
    # session used for this single POST has no automatic authentication replay.
    headers = {name: DEFAULT_HEADERS[name] for name in ("User-Agent", "Accept-Language")}
    headers.update(obs._headers())
    cookies = copy.copy(obs.session.cookies)
    session = requests.Session()
    session.trust_env = False
    session.headers.clear()
    session.cookies = cookies
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=0, connect=0, read=0, redirect=0, status=0, other=0,
    )))
    try:
        try:
            response = session.request(
                "POST", obs.base_url + path, json=payload, headers=headers,
                timeout=(10, 45), allow_redirects=False, verify=True,
            )
        except requests.RequestException:
            return {"status": "uncertain", "reason": "transport_error", "http_status": None}
        try:
            status = response.status_code
            if status != 200:
                # An error or lost response does not prove that a write never
                # reached the backend. Do not follow even same-host redirects.
                return {"status": "uncertain", "reason": "http_response_not_success", "http_status": status}
            try:
                data = response.json()
            except (ValueError, requests.RequestException):
                return {"status": "uncertain", "reason": "unrecognized_response", "http_status": status}
            if not isinstance(data, dict) or type(data.get("statusCode")) is not int:
                return {"status": "uncertain", "reason": "unrecognized_response", "http_status": status}
            code = _result_code(data.get("resultCode"))
            return {
                "status": "acknowledged" if data["statusCode"] == 0 else "rejected",
                "http_status": status, "status_code": data["statusCode"], "result_code": code,
            }
        finally:
            response.close()
    finally:
        session.close()


def _readback(obs: Any, term: str, selected: list[str]) -> dict[str, Any]:
    try:
        draft = obs.get_registration_draft()
    except (NinovaError, requests.RequestException, ValueError, TypeError):
        return {"status": "unavailable", "matches_requested": None}
    if not isinstance(draft, dict) or draft.get("term_code") != term:
        return {"status": "term_mismatch", "matches_requested": None}
    courses = draft.get("courses")
    if not isinstance(courses, list) or any(not isinstance(row, dict) for row in courses):
        return {"status": "unrecognized", "matches_requested": None}
    actual = [row.get("crn") for row in courses]
    if any(not isinstance(crn, str) or not re.fullmatch(r"[0-9]{4,5}", crn) for crn in actual) or len(actual) != len(set(actual)):
        return {"status": "unrecognized", "matches_requested": None}
    matches = draft.get("draft_exists") is True and set(actual) == set(selected)
    return {"status": "matched" if matches else "different", "matches_requested": matches,
            "course_count": len(actual), "_crns": actual}


def _draft_crns(draft: Any) -> list[str] | None:
    """CRNs currently in the saved draft, or None when they cannot be read."""
    if not isinstance(draft, dict):
        return None
    if draft.get("draft_exists") is False:
        return []
    courses = draft.get("courses")
    if not isinstance(courses, list):
        return None
    crns = [row.get("crn") if isinstance(row, dict) else None for row in courses]
    if any(not isinstance(crn, str) or not re.fullmatch(r"[0-9]{4,5}", crn) for crn in crns):
        return None
    return crns


def save_registration_draft(obs: Any, crns: list[str], *, allow_replace: bool = False) -> dict[str, Any]:
    """Save exactly the requested CRNs, preserving warnings and existing enrollments."""
    selected = normalize_crns(crns)
    if os.getenv("NINOVA_OBS_REGISTRATION_WRITES") != "1":
        raise ObsError("OBS registration writes are disabled by the operator.")
    before = obs.get_registration_draft()
    term = before.get("term_code") if isinstance(before, dict) else None
    if not isinstance(term, str) or not re.fullmatch(r"[0-9]{6}", term):
        raise ObsError("OBS did not identify a valid draft term before saving.")
    if before.get("draft_creation_allowed") is not True:
        raise ObsError("OBS did not explicitly allow draft creation in the current period.")
    previous = _draft_crns(before)
    removed = None if previous is None else [crn for crn in previous if crn not in selected]
    added = None if previous is None else [crn for crn in selected if crn not in previous]
    change = {"previous_crns": previous, "added_crns": added, "removed_crns": removed}
    base = {
        "term_code": term, "requested_crns": selected, **change, "retry_performed": False,
        "course_registration_performed": False,
        "note": "This operation saves a draft only. Course enrollment requires a separate explicitly authorized operation.",
        "source_url": obs.base_url + SAVE_DRAFT_PATH,
    }
    # The save replaces the whole draft, so anything not resent is lost.
    # Dropping courses, or saving over a draft that could not be read,
    # needs a second explicit choice, the same way submit_assignment
    # refuses to overwrite an uploaded file without allow_replace.
    if allow_replace is not True and (previous is None or removed):
        return {
            **base, "status": "needs_allow_replace", "saved": False, "submission": None, "readback": None,
            "reason": ("existing_draft_unreadable" if previous is None else "would_remove_courses"),
            "next_step": "Nothing was sent. Show the user previous_crns and removed_crns, then call again "
                         "with allow_replace=true only if they accept losing those courses from the draft.",
        }
    if previous is not None and set(previous) == set(selected):
        return {**base, "status": "unchanged", "saved": True, "submission": None, "readback": None,
                "reason": "draft_already_matches"}
    submission = _post_once(obs, SAVE_DRAFT_PATH, {"ecrn": selected})
    readback = _readback(obs, term, selected)
    after = readback.pop("_crns", None)
    # A readback match after an uncertain transport is useful evidence but does
    # not identify whether this request changed an already matching draft.
    saved = submission["status"] == "acknowledged" and readback["matches_requested"] is True
    status = "saved" if saved else "rejected" if submission["status"] == "rejected" else "uncertain"
    result = {
        **base, "status": status, "saved": True if saved else False if status == "rejected" else None,
        "submission": submission, "readback": readback,
    }
    # OBS said no, yet the draft no longer matches what was read before the
    # request. Report that instead of a flat "nothing changed".
    if status == "rejected" and previous is not None:
        if after is not None and set(after) != set(previous):
            result["saved"] = None
            result["status"] = "uncertain"
            result["reason"] = "draft_changed_despite_rejection"
            result["current_crns"] = after
    return result

