"""Normalize OBS saved drafts and independent CRN validation responses."""
from __future__ import annotations

import re
import requests
from typing import Any

from .archive import normalize_course_code
from .client import NinovaError
from .obs_client import ObsError

DRAFT_PATH = '/api/ogrenci/DersKayitTaslak/'
VALIDATION_PATH = '/api/TaslakKontrolAPI/v1/'

# Meanings published in the OBS registration application's English messages.
REASONS = {
    'VAL01': 'A student enrollment hold applies.',
    'VAL02': 'The enrollment time window is closed.',
    'VAL03': 'The course is already registered this term.',
    'VAL04': 'The course is outside the student degree plan.',
    'VAL05': 'The term credit limit would be exceeded.',
    'VAL06': 'The section has no remaining quota.',
    'VAL07': 'The course was already completed with an AA grade.',
    'VAL08': 'The student program is not eligible for this section.',
    'VAL09': 'The section conflicts with another course.',
    'VAL10': 'The student is not registered for this course.',
    'VAL11': 'The course prerequisites are not satisfied.',
    'VAL12': 'The section is not offered in the registration term.',
    'VAL13': 'The section is temporarily disabled.',
    'VAL14': 'The registration system is temporarily disabled.',
    'VAL15': 'The validation request exceeds twelve CRNs.',
    'VAL16': 'Another registration operation is in progress.',
    'VAL17': 'The registration system is under maintenance.',
    'VAL18': 'A course attribute restriction applies.',
    'VAL19': 'An undergraduate course restriction applies.',
    'VAL20': 'The course drop limit would be exceeded.',
    'VAL21': 'The OBS validation rate limit was exceeded.',
    'VAL22': 'The course cannot be repeated for grade improvement in this period.',
    'CRNListEmpty': 'The CRN is unavailable in the registration period.',
    'CRNNotFound': 'The CRN was not found in the registration period.',
}


def normalize_crns(crns: list[str]) -> list[str]:
    """Validate before any network request, preserving order and rejecting duplicates."""
    if not isinstance(crns, list) or not 1 <= len(crns) <= 12:
        raise ObsError('Provide between one and twelve CRNs.')
    result = []
    for crn in crns:
        if not isinstance(crn, str) or not re.fullmatch(r'[0-9]{4,5}', crn):
            raise ObsError('Each CRN must be a string of four or five digits.')
        if crn in result:
            raise ObsError('Duplicate CRNs are not allowed.')
        result.append(crn)
    return result


def require_success(payload: Any, scope: str) -> dict[str, Any]:
    """Distinguish an OBS business error or unrecognized body from an empty result."""
    if not isinstance(payload, dict) or type(payload.get('statusCode')) is not int:
        raise ObsError(f'OBS {scope} returned an unrecognized response.')
    if payload['statusCode'] != 0:
        code = payload.get('resultCode')
        code = code if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', code) else 'unknown'
        raise ObsError(f'OBS {scope} failed ({code}).')
    return payload


def normalize_reasons(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return [{'code': 'unknown', 'message': 'OBS did not provide readable validation details.'}]
    result = []
    for item in items:
        if not isinstance(item, dict):
            result.append({'code': 'unknown', 'message': 'OBS returned an unrecognized validation detail.'})
            continue
        code = item.get('resultCode')
        if not isinstance(code, str):
            code = 'unknown'
        result.append({
            'code': code,
            'message': REASONS.get(code, 'OBS returned an unrecognized restriction.'),
            'source_details': item.get('resultData'),
        })
    return result


def _clock(value: Any) -> str | None:
    # Calendar times are minutes after midnight, not HHMM integers.
    if type(value) is not int or not 0 <= value < 1440:
        return None
    return f'{value // 60:02d}:{value % 60:02d}'


def normalize_calendar(payload: Any) -> list[dict[str, Any]]:
    body = require_success(payload, 'draft calendar')
    rows = body.get('kayitSinifResultList')
    if not isinstance(rows, list):
        raise ObsError('OBS draft calendar did not contain a section list.')
    result = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('sinifYerZaman'), list):
            raise ObsError('OBS draft calendar contained an unrecognized section.')
        sessions = []
        for entry in row['sinifYerZaman']:
            if not isinstance(entry, dict):
                raise ObsError('OBS draft calendar contained an unrecognized meeting.')
            start, end = _clock(entry.get('baslangicSaati')), _clock(entry.get('bitisSaati'))
            if start and end and start >= end:
                start = end = None
            sessions.append({
                'day': entry.get('gunAdiTR') or entry.get('gunAdiEN'),
                'day_en': entry.get('gunAdiEN'),
                'time': f'{start}/{end}' if start and end else None,
                'start': start, 'end': end,
                'room': entry.get('mekanAdi'), 'building': entry.get('binaAdi'),
                'campus': entry.get('kampusAdi'),
            })
        result.append({'crn': str(row.get('crn') or ''), 'code': row.get('dersKodu'),
                       'name': row.get('dersAdiEN') or row.get('dersAdiTR'), 'sessions': sessions})
    return result


def read_registration_draft(obs: Any) -> dict[str, Any]:
    """Read the saved verdict without invoking the endpoint that rechecks a saved draft."""
    time_payload = require_success(obs.api_get(DRAFT_PATH + 'KayitZamaniKontrolu'), 'draft period')
    period = time_payload.get('kayitZamanKontrolResult')
    if not isinstance(period, dict):
        raise ObsError('OBS did not provide the draft registration period.')
    term = period.get('akademikDonemKodu')
    if not re.fullmatch(r'[0-9]{6}', str(term)):
        raise ObsError('OBS did not provide a valid registration term code.')
    body = require_success(obs.api_get(DRAFT_PATH + 'TaslakBilgisi/' + str(term)), 'saved draft')
    if 'taslakBilgi' not in body:
        raise ObsError('OBS saved draft response is missing draft information.')
    draft = body['taslakBilgi']
    if draft is not None and (not isinstance(draft, dict) or not isinstance(draft.get('taslakSinifListesi'), list)):
        raise ObsError('OBS saved draft has an unrecognized course list.')
    if draft and str(draft.get('akademikDonemKodu')) != str(term):
        raise ObsError('OBS returned a saved draft for a different term.')
    errors = []
    calendar = []
    if draft is not None:
        try:
            calendar = normalize_calendar(obs.api_get(DRAFT_PATH + 'TaslakTakvimi/' + str(term)))
        except (NinovaError, requests.RequestException):
            errors.append({'scope': 'calendar', 'message': 'The saved draft calendar could not be read.'})
    by_crn = {row['crn']: row for row in calendar}
    courses = []
    for row in (draft or {}).get('taslakSinifListesi', []):
        if not isinstance(row, dict) or not re.fullmatch(r'[0-9]{4,5}', str(row.get('crn') or '')):
            raise ObsError('OBS saved draft contained an invalid course row.')
        verdict = row.get('sinifAlinabilir')
        eligible = verdict if type(verdict) is bool else None
        reasons = normalize_reasons(row.get('kontrolSonucListesi'))
        if eligible is True and reasons:
            eligible = None
        if eligible is False and not reasons:
            reasons = [{'code': 'unknown', 'message': 'OBS marked this section unavailable without a reason.'}]
        crn = str(row['crn'])
        courses.append({
            'crn': crn, 'course_code': row.get('dersKodu'),
            'course_name': row.get('dersAdiEN') or row.get('dersAdiTR'),
            'eligible': eligible, 'eligibility_status': {True: 'eligible', False: 'ineligible', None: 'unknown'}[eligible],
            'reasons': reasons, 'error_reason': ' '.join(item['message'] for item in reasons) or None,
            'sessions': by_crn.get(crn, {}).get('sessions', []),
            'schedule_available': bool(by_crn.get(crn, {}).get('sessions')) and all(
                session.get('day') and session.get('time') for session in by_crn[crn]['sessions']
            ),
        })
    return {
        'term_code': str(term), 'term_name': (draft or {}).get('akademikDonemAdi'),
        'draft_exists': draft is not None, 'checked_at': (draft or {}).get('kontrolTarihi'),
        'retrieved_at': _now(), 'eligibility_source': 'saved_draft',
        'eligibility_note': 'These are saved OBS verdicts at checked_at, not a fresh registration guarantee.',
        'class_year': period.get('sinif'),
        'draft_creation_allowed': period.get('ogrenciTaslakOlusturabilir'),
        'registration_window': {'start': period.get('baslangicTarihi'), 'end': period.get('bitisTarihi')},
        'course_count': len(courses), 'courses': courses, 'calendar': calendar, 'errors': errors,
        'source_url': obs.base_url + DRAFT_PATH + 'TaslakBilgisi/' + str(term),
        'untrusted_external_content': True,
    }


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def normalize_validation(payload: Any, requested_crns: list[str]) -> dict[str, Any]:
    body = require_success(payload, 'CRN validation')
    rows = body.get('ecrnResultList')
    if not isinstance(rows, list):
        raise ObsError('OBS CRN validation did not contain a result list.')
    results = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ObsError('OBS CRN validation contained an invalid result.')
        crn = str(row.get('crn') or '')
        if crn not in requested_crns or crn in results:
            raise ObsError('OBS CRN validation returned unexpected or duplicate CRNs.')
        status = row.get('statusCode')
        details = row.get('taslakKontrolResultList')
        reasons = normalize_reasons(details)
        eligible = status == 0 if type(status) is int and status in (0, 1) else None
        if eligible:
            try:
                normalize_course_code(str(row.get('dersKodu') or ''))
            except ValueError:
                eligible = None
                reasons.append({'code': 'unknown', 'message': 'OBS omitted a recognizable course code for this CRN.'})
        if eligible and (not isinstance(details, list) or reasons):
            eligible = None
        if eligible is False and not reasons:
            reasons = [{'code': 'unknown', 'message': 'OBS rejected this CRN without a restriction reason.'}]
        codes = {reason['code'] for reason in reasons}
        results[crn] = {
            'crn': crn, 'course_code': row.get('dersKodu'), 'course_name': row.get('dersAdi'),
            'credit': row.get('kredi'), 'eligible': eligible,
            'prerequisite_eligible': True if eligible else (False if 'VAL11' in codes else None),
            'program_eligible': True if eligible else (False if 'VAL08' in codes else None),
            'class_eligible': True if eligible else None,
            'credit_eligible': True if eligible else None,
            'reasons': reasons, 'error_reason': ' '.join(r['message'] for r in reasons) or None,
            'source': 'obs_crn_validation',
        }
    for crn in requested_crns:
        results.setdefault(crn, {'crn': crn, 'eligible': None, 'error_reason': 'OBS omitted this CRN from validation.'})
    return results
