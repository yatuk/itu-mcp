"""Match calculated term averages to explicit official OBS records."""
from __future__ import annotations

import math
from typing import Any

from .gpa_common import finite_number as _number
from .gpa_common import reproduces_official

_SOURCE = 'obs.itu.edu.tr/api/ogrenci/KayitDurumu'


def _no_reference(status: str, **details: Any) -> dict[str, Any]:
    """A reference without an official average, saying why there is none."""
    return {'status': status, 'gpa': None, 'credits': None, **details, 'source': _SOURCE}


def _is_successful_response(payload: Any) -> bool:
    return isinstance(payload, dict) and type(payload.get('statusCode')) is int and payload['statusCode'] == 0


def _is_list_of_objects(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _zero_average_means_term_not_final(gpa: float, term: dict[str, Any], zero_average_is_final: bool) -> bool:
    """Whether an official average of 0 is a placeholder and not a result.

    OBS can report 0 for a term that has no awarded credit yet. That is an
    unfinished term, not an official average of zero. verilenKredi is earned
    credit, so a term with only failed courses looks the same; the caller
    tells the two apart with ``zero_average_is_final``.
    """
    return gpa == 0 and not _number(term.get('verilenKredi')) and not zero_average_is_final


def _reported_average(term: dict[str, Any], zero_average_is_final: bool) -> float | None:
    """The official average of a term row, or None when the row does not state a usable one."""
    gpa = _number(term.get('donemlikNotOrtalamasi'))
    if gpa is None or not 0 <= gpa <= 4:
        return None
    if _zero_average_means_term_not_final(gpa, term, zero_average_is_final):
        return None
    return gpa


def official_term_reference(
    payload: dict[str, Any],
    term_code: str | None,
    *,
    zero_average_is_final: bool = False,
) -> dict[str, Any]:
    """Do not guess between concurrent programs or substitute another term.

    ``zero_average_is_final``: the caller knows every course of the term has a
    failing grade, so an average of 0 with 0 earned credits is the real record.
    """
    if not _is_successful_response(payload):
        return _no_reference('unavailable', reason='No successful official status response was received.')
    programs = payload.get('kayitDurumuList')
    if not _is_list_of_objects(programs):
        return _no_reference('unavailable', reason='The official program list has an unrecognized shape.')
    if len(programs) > 1:
        return _no_reference('ambiguous', reason='Registered courses do not identify a matching program.',
                             program_count=len(programs))
    candidates = []
    # Without a term code there is no term to look for, and the term lists are not read.
    for program in programs if term_code else []:
        terms = program.get('kayitDurumuDonemList')
        if not _is_list_of_objects(terms):
            return _no_reference('unavailable', reason='The official term list has an unrecognized shape.')
        matching_terms = [t for t in terms if str(t.get('akademikDonemKodu')) == str(term_code)]
        if len(matching_terms) > 1:
            return _no_reference('ambiguous', candidate_count=len(matching_terms))
        for term in matching_terms:
            gpa = _reported_average(term, zero_average_is_final)
            if gpa is not None:
                candidates.append({
                    'term_code': str(term_code),
                    'program': program.get('akademikProgramAdi') or program.get('akademikBolumAdi'),
                    'gpa': gpa, 'credits': _number(term.get('verilenKredi')),
                })
    if len(candidates) != 1:
        return _no_reference('ambiguous' if candidates else 'unavailable', candidate_count=len(candidates))
    return {'status': 'reported', **candidates[0], 'source': _SOURCE, 'finality': 'not_reported_by_source'}


def _preferred_average(
    calculated: float | None, official: float | None, *, projection: bool, projection_complete: Any,
) -> tuple[float | None, str]:
    """The term average to quote and its source.

    A what-if is always the calculated value, and only when every grade was
    applied. Otherwise the official record wins whenever there is one.
    """
    use_calculated = projection or official is None
    preferred = calculated if use_calculated else official
    if projection and not projection_complete:
        preferred = None
    if preferred is None:
        return None, 'unavailable'
    return preferred, 'calculated' if use_calculated else 'official_obs'


def _unrounded_average(result: dict[str, Any]) -> float | None:
    """The calculated average before rounding, rebuilt from the counted courses of the result.

    The official value is compared with this one by the tolerance both GPA
    tools share, so an exact half matches whichever way OBS rounded it. A
    result that does not list its courses is compared by its rounded average.
    """
    rounded = result.get('gpa')
    if rounded is None:
        return None
    try:
        weights = [(float(course['credit']), float(course['coefficient'])) for course in result.get('courses') or []]
    except (KeyError, TypeError, ValueError):
        return rounded
    credits = math.fsum(credit for credit, _ in weights)
    if not credits > 0:
        return rounded
    return math.fsum(credit * coefficient for credit, coefficient in weights) / credits


def _comparison_status(
    calculated: float | None, official: float | None, *, projection: bool, projection_complete: Any,
) -> str:
    if projection:
        return 'projection' if projection_complete and calculated is not None else 'projection_incomplete'
    if calculated is None or official is None:
        return 'unavailable'
    return 'match' if reproduces_official(calculated, official) else 'mismatch'


def _mismatch_warning(ambiguous: list[dict[str, Any]]) -> str:
    """Why the estimate differs from OBS, naming every course whose plan row carries two credit values."""
    warning = (
        'The course-credit estimate differs from the official term average. '
        'Use official_term_gpa for the reported OBS average. Degree-plan counted '
        'credit has not been assumed to be the GPA weight.'
    )
    if ambiguous:
        warning += ' Ambiguous credit: ' + '; '.join(
            f"{course.get('code')} ({_credit_text(course.get('kredisi'))} or "
            f"{_credit_text(course.get('sayilanKredi'))})" for course in ambiguous
        ) + '.'
    return warning


def attach_official_reference(
    result: dict[str, Any],
    reference: dict[str, Any],
    *,
    projection: bool,
    ambiguous_credits: list[dict[str, Any]] | None = None,
) -> None:
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
    result['preferred_term_gpa'], result['preferred_term_gpa_source'] = _preferred_average(
        calculated, official, projection=projection, projection_complete=projection_complete)
    result['comparison_status'] = _comparison_status(
        _unrounded_average(result), official, projection=projection, projection_complete=projection_complete)
    result['gpa_difference'] = round(calculated - official, 4) if (
        not projection and calculated is not None and official is not None
    ) else None
    if result['comparison_status'] == 'mismatch':
        ambiguous = ambiguous_credits or []
        result['calculation_warning'] = _mismatch_warning(ambiguous)
        result['mismatch_ambiguous_credit_courses'] = [course.get('code') for course in ambiguous]


def _credit_text(value: Any) -> str:
    number = _number(value)
    return '?' if number is None else f'{number:g}'
