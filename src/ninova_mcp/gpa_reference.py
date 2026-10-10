"""Match calculated term averages to explicit official OBS records."""
from __future__ import annotations

import math
from typing import Any


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(',', '.'))
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def official_term_reference(payload: dict[str, Any], term_code: str | None) -> dict[str, Any]:
    """Do not guess between concurrent programs or substitute another term."""
    candidates = []
    if not isinstance(payload, dict) or type(payload.get('statusCode')) is not int or payload['statusCode'] != 0:
        return {'status': 'unavailable', 'gpa': None, 'credits': None,
                'reason': 'No successful official status response was received.',
                'source': 'obs.itu.edu.tr/api/ogrenci/KayitDurumu'}
    programs = payload.get('kayitDurumuList')
    if not isinstance(programs, list) or any(not isinstance(p, dict) for p in programs):
        return {'status': 'unavailable', 'gpa': None, 'credits': None,
                'reason': 'The official program list has an unrecognized shape.',
                'source': 'obs.itu.edu.tr/api/ogrenci/KayitDurumu'}
    if len(programs) > 1:
        return {'status': 'ambiguous', 'gpa': None, 'credits': None,
                'reason': 'Registered courses do not identify a matching program.',
                'program_count': len(programs),
                'source': 'obs.itu.edu.tr/api/ogrenci/KayitDurumu'}
    if term_code:
        for program in programs:
            terms = program.get('kayitDurumuDonemList')
            if not isinstance(terms, list) or any(not isinstance(t, dict) for t in terms):
                return {'status': 'unavailable', 'gpa': None, 'credits': None,
                        'reason': 'The official term list has an unrecognized shape.',
                        'source': 'obs.itu.edu.tr/api/ogrenci/KayitDurumu'}
            matching_terms = [t for t in terms if str(t.get('akademikDonemKodu')) == str(term_code)]
            if len(matching_terms) > 1:
                return {'status': 'ambiguous', 'gpa': None, 'credits': None,
                        'candidate_count': len(matching_terms),
                        'source': 'obs.itu.edu.tr/api/ogrenci/KayitDurumu'}
            for term in matching_terms:
                if str(term.get('akademikDonemKodu')) != str(term_code):
                    continue
                gpa = _number(term.get('donemlikNotOrtalamasi'))
                if gpa is None or not 0 <= gpa <= 4:
                    continue
                # OBS can report 0 for a term that has no awarded credit yet.
                # That is an unfinished term, not an official average of zero.
                if gpa == 0 and not _number(term.get('verilenKredi')):
                    continue
                candidates.append({
                    'term_code': str(term_code),
                    'program': program.get('akademikProgramAdi') or program.get('akademikBolumAdi'),
                    'gpa': gpa, 'credits': _number(term.get('verilenKredi')),
                })
    if len(candidates) != 1:
        return {'status': 'ambiguous' if candidates else 'unavailable', 'gpa': None,
                'credits': None, 'candidate_count': len(candidates),
                'source': 'obs.itu.edu.tr/api/ogrenci/KayitDurumu'}
    return {'status': 'reported', **candidates[0],
            'source': 'obs.itu.edu.tr/api/ogrenci/KayitDurumu',
            'finality': 'not_reported_by_source'}


def attach_official_reference(result: dict[str, Any], reference: dict[str, Any], *, projection: bool) -> None:
    calculated = result.get('gpa')
    official = reference.get('gpa')
    result['calculated_term_gpa'] = calculated
    result['official_term_gpa'] = official
    result['official_term_credits'] = reference.get('credits')
    result['official_reference'] = reference
    result['is_projection'] = projection
    # Keep the existing calculator field and course arithmetic intact. Consumers
    # answering an official-average question have an explicit preferred value.
    projection_complete = result.get('projection_complete', True) and result.get('calculation_complete', True)
    preferred = calculated if projection or official is None else official
    if projection and not projection_complete:
        preferred = None
    result['preferred_term_gpa'] = preferred
    result['preferred_term_gpa_source'] = ('unavailable' if preferred is None else
        'calculated' if projection or official is None else 'official_obs')
    result['comparison_status'] = (
        ('projection' if projection_complete and calculated is not None else 'projection_incomplete')
        if projection else 'unavailable' if calculated is None or official is None
        else 'match' if abs(calculated - official) < 0.005 else 'mismatch'
    )
    result['gpa_difference'] = round(calculated - official, 4) if (
        not projection and calculated is not None and official is not None
    ) else None
    if result['comparison_status'] == 'mismatch':
        result['calculation_warning'] = (
            'The course-credit estimate differs from the official term average. '
            'Use official_term_gpa for the reported OBS average. Degree-plan counted '
            'credit has not been assumed to be the GPA weight.'
        )
