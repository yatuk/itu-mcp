"""Join authenticated OBS registration checks with public course requirements."""
from __future__ import annotations

from typing import Any
import re

import requests

from .archive import normalize_course_code, split_course_code
from .client import NinovaError
from .elective_groups import enrich_elective_group, fetch_elective_group
from .graduation import completed_course_history, summarize_graduation_plan
from .parsing import normalize_lookup_text
from .obs_client import ObsError
from .registration_draft import DRAFT_PATH, _now, normalize_crns, require_success
from .registration_plan import validate_registration_plan

_READ_ERRORS = (NinovaError, requests.RequestException)
_MAX_ELECTIVE_VALIDATIONS = 24


def _canonical(value: Any) -> str | None:
    try:
        return normalize_course_code(str(value or ''))
    except ValueError:
        return None


def _schedules(public: Any, codes: list[str]) -> tuple[dict[str, Any], list[str]]:
    branches = sorted({split_course_code(code)[0] for code in codes if _canonical(code)})
    result, errors = {}, []
    for branch in branches:
        try:
            result[branch] = public.get_course_schedule('LS', branch)
        except _READ_ERRORS:
            errors.append(f'The current {branch} course schedule could not be read.')
    return result, errors


def get_elective_group(app: Any, group_id: int) -> dict[str, Any]:
    """Check section eligibility independently, not as one combined elective plan."""
    group = fetch_elective_group(app.obs_public, group_id)
    schedules, errors = _schedules(app.obs_public, [row['course_code'] for row in group['courses']])
    result = enrich_elective_group(group, schedules)
    checked = 0
    unavailable = False
    try:
        _period, term_keys, period_errors = _registration_period(app.obs)
        errors.extend(period_errors)
    except _READ_ERRORS:
        term_keys = set()
        errors.append('The current student registration term could not be verified.')
    for course in result['courses']:
        if term_keys and _term_key(course.get('semester')) not in term_keys:
            course['offered_in_public_term'] = course.get('offered_this_term')
            course['offered_this_term'] = None
            course['schedule_status'] = 'unknown'
            course['schedule_note'] = 'The public schedule term does not match the student registration term.'
            errors.append(course['schedule_note'])
        verdicts = []
        for section in course['sections']:
            crn = str(section['crn'])
            verdict = {'eligible': None, 'error_reason': 'This section has not been checked against the student record.'}
            if (not unavailable and checked < _MAX_ELECTIVE_VALIDATIONS
                    and _term_key(course.get('semester')) in term_keys):
                try:
                    verdict = app.obs.validate_registration_crns([crn])[crn]
                    checked += 1
                    if verdict.get('course_code') and _canonical(verdict['course_code']) != course['course_code']:
                        verdict = {'eligible': None, 'error_reason': 'Public and authenticated OBS course codes disagree for this CRN.'}
                except _READ_ERRORS:
                    unavailable = True
                    errors.append('Student-specific OBS validation is unavailable. Remaining section eligibility is unknown.')
            section['eligibility'] = verdict
            verdicts.append(verdict.get('eligible'))
        eligible = True if True in verdicts else None if not verdicts or None in verdicts else False
        course['eligibility'] = {
            'eligible': eligible,
            'scope': 'each_section_independently',
            'note': 'An eligible section can still conflict with other courses in a proposed plan.',
        }
    if sum(len(course['sections']) for course in result['courses']) > _MAX_ELECTIVE_VALIDATIONS:
        errors.append(f'At most {_MAX_ELECTIVE_VALIDATIONS} sections are checked per group call. Use obs_validate_registration_plan for unchecked CRNs.')
    result['offered_course_count'] = sum(course['offered_this_term'] is True for course in result['courses'])
    result['schedule_unknown_count'] = sum(course['offered_this_term'] is None for course in result['courses'])
    result.update({'eligibility_checked_section_count': checked, 'errors': list(dict.fromkeys(errors)), 'retrieved_at': _now()})
    return result


def _history(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str | None]]:
    info = payload.get('mezuniyetimeNeKaldiBilgi')
    if not isinstance(info, dict) or not isinstance(info.get('checkMetMezuniyetList'), list):
        raise ObsError('OBS graduation records did not contain a degree plan.')
    if any(not isinstance(row, dict) for row in info['checkMetMezuniyetList']):
        raise ObsError('OBS graduation records contain unrecognized course entries.')
    unused = info.get('unusedSinifOgrenciList') or []
    if not isinstance(unused, list) or any(not isinstance(row, dict) for row in unused):
        raise ObsError('OBS graduation records contain unrecognized unused course entries.')
    summary = summarize_graduation_plan(payload)
    summary.pop('note', None)
    summary['open_elective_slots'] = [{
        'slot': row.get('grupName'), 'group_id': row.get('grupId'),
        'credit': row.get('kredisiDec'), 'semester_number': row.get('donemNo'),
    } for row in info['checkMetMezuniyetList'] if row.get('grupName') and not row.get('isMet')]
    for key in ('remaining_required_courses', 'filled_elective_slots'):
        for row in summary.get(key) or []:
            # The existing summary uses a localized display label; this API
            # carries the original term and does not depend on that label.
            row.pop('semester', None)
    plan = info.get('dersPlaniVM') or {}
    summary['english_credits_required'] = plan.get('gerekliIngilizceKredi')
    summary['english_credits_earned'] = info.get('metIngKrediTotal')
    return summary, completed_course_history(info)


def _rules(public: Any, codes: set[str]) -> tuple[dict[str, Any], list[str]]:
    result, sources = {}, []
    branches = sorted({split_course_code(code)[0] for code in codes})
    for branch in branches:
        try:
            data = public.get_branch_prerequisites(branch)
        except _READ_ERRORS:
            continue
        if data.get('url'):
            sources.append(data['url'])
        for code in codes:
            if split_course_code(code)[0] != branch:
                continue
            rule = (data.get('rules') or {}).get(code)
            if rule:
                # Keep the structured rule and exact official text. Localized
                # display strings from older tools do not enter this response.
                result[code] = {key: rule.get(key) for key in (
                    'requirement_tree', 'credit_requirement', 'credit_requirement_text',
                )}
            elif data.get('table_parsed'):
                result[code] = {'requirement_tree': None, 'credit_requirement': None}
    return result, sources


def _term_key(label: Any) -> tuple[str, str] | None:
    text = normalize_lookup_text(str(label or ''))
    years = re.search(r'(20[0-9]{2})\s*[-/]\s*(20[0-9]{2})', text)
    if not years:
        return None
    seasons = {'guz': 'fall', 'fall': 'fall', 'autumn': 'fall',
               'bahar': 'spring', 'spring': 'spring', 'yaz': 'summer', 'summer': 'summer'}
    season = next((value for word, value in seasons.items() if re.search(r'\b' + word + r'\b', text)), None)
    return (years.group(1) + '-' + years.group(2), season) if season else None


def _registration_period(obs: Any) -> tuple[dict[str, Any] | None, set[tuple[str, str]], list[str]]:
    errors = []
    period = None
    try:
        period_body = require_success(obs.api_get(DRAFT_PATH + 'KayitZamaniKontrolu'), 'registration period')
        period = period_body.get('kayitZamanKontrolResult')
        if not isinstance(period, dict) or not period.get('akademikDonemKodu'):
            raise ObsError('The registration period is unavailable.')
    except _READ_ERRORS:
        period = None
        errors.append('The current registration term and class year could not be verified.')
    term_labels = []
    if period:
        term_labels = [period.get('akademikDonemAdi'), period.get('akademikDonemAdiEN')]
        try:
            semesters = require_success(obs.list_semesters(), 'semester list')
            rows = semesters.get('ogrenciDonemListesi')
            for semester in rows if isinstance(rows, list) else []:
                if not isinstance(semester, dict):
                    continue
                if str(semester.get('donemKodu')) == str(period.get('akademikDonemKodu')):
                    term_labels.extend([semester.get('akademikDonemAdi'), semester.get('akademikDonemAdiEN')])
        except _READ_ERRORS:
            pass
    term_keys = {key for label in term_labels if (key := _term_key(label))}
    return period, term_keys, errors


def validate_plan(app: Any, crns: list[str]) -> dict[str, Any]:
    selected = normalize_crns(crns)
    errors: list[str] = []
    period, term_keys, period_errors = _registration_period(app.obs)
    errors.extend(period_errors)
    try:
        eligibility = app.obs.validate_registration_crns(selected)
    except _READ_ERRORS:
        eligibility = {}
        errors.append('The official OBS CRN validation could not be completed.')
    codes = {code for item in eligibility.values() if (code := _canonical(item.get('course_code')))}
    schedules, schedule_errors = _schedules(app.obs_public, sorted(codes))
    errors.extend(schedule_errors)
    sections = []
    sources = []
    for schedule in schedules.values():
        if schedule.get('url'):
            sources.append(schedule['url'])
        if (_term_key(schedule.get('semester')) not in term_keys
                or schedule.get('parse_warning') or schedule.get('parse_warnings') or schedule.get('error')):
            errors.append('A public schedule is unreadable or its term does not match the verified registration term.')
            continue
        for section in schedule.get('courses') or []:
            crn = str(section.get('crn') or '')
            if crn not in selected:
                continue
            if _canonical(section.get('code')) != _canonical(eligibility.get(crn, {}).get('course_code')):
                errors.append(f'Public and authenticated OBS course codes disagree for CRN {crn}.')
                continue
            sections.append({**section, 'credit': eligibility[crn].get('credit')})
    # The live verdict can identify a course even when its schedule is missing.
    resolved = {str(section.get('crn')) for section in sections}
    for crn in selected:
        if crn not in resolved and eligibility.get(crn, {}).get('course_code'):
            item = eligibility[crn]
            sections.append({'crn': crn, 'code': item['course_code'], 'name': item.get('course_name'),
                             'credit': item.get('credit'), 'sessions': []})
    graduation = completed = None
    try:
        raw = require_success(app.obs.get_graduation_remaining(app.obs.default_program_id()), 'graduation')
        graduation, completed = _history(raw)
    except _READ_ERRORS:
        errors.append('The official graduation plan and completed course history could not be read.')
    groups = {}
    for slot in (graduation or {}).get('open_elective_slots') or []:
        gid = slot.get('group_id')
        if gid is not None and str(gid) not in groups:
            try:
                groups[str(gid)] = fetch_elective_group(app.obs_public, gid)
                if groups[str(gid)].get('url'):
                    sources.append(groups[str(gid)]['url'])
            except _READ_ERRORS:
                errors.append(f'Elective group {gid} could not be read.')
    future_codes = {code for item in (graduation or {}).get('remaining_required_courses') or []
                    if (code := _canonical(item.get('course_code')))}
    rules, rule_sources = _rules(app.obs_public, codes | future_codes)
    result = validate_registration_plan(
        sections, requested_crns=selected, completed_courses=completed,
        prerequisite_rules=rules, eligibility=eligibility, graduation=graduation,
        elective_groups=groups, class_year=(period or {}).get('sinif'),
        dependency_rules={code: rule for code, rule in rules.items() if code in future_codes},
        source_errors=errors,
    )
    result.update({
        'term_code': (period or {}).get('akademikDonemKodu'), 'checked_at': _now(),
        'eligibility_source': 'independent_obs_crn_validation', 'obs_results': eligibility,
        'sections': sections, 'sources': sorted(set(sources + rule_sources)),
        'untrusted_external_content': True,
    })
    return result
