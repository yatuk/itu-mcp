"""Official semester values and projections must remain distinguishable."""
import unittest
from unittest.mock import Mock

from ninova_mcp.gpa_reference import official_term_reference, attach_official_reference
from ninova_mcp.server import NinovaMcpApp


def status(gpa=3.5, credits=10, term='202620'):
    return {'statusCode': 0, 'kayitDurumuList': [{'akademikProgramAdi': 'Example program',
        'kayitDurumuDonemList': [{'akademikDonemKodu': term,
            'donemlikNotOrtalamasi': gpa, 'verilenKredi': credits}]}]}


class OfficialGpaTests(unittest.TestCase):
    def test_fallback_matches_compact_code_and_requested_attempt_only(self):
        app = NinovaMcpApp.__new__(NinovaMcpApp)
        info = {'checkMetMezuniyetList': [
            {'bransKodu': 'AAA101', 'donem': '202610', 'kredisiDec': 4, 'harfNotu': 'FF'},
            {'bransKodu': 'AAA 101', 'donem': '202620', 'kredisiDec': 3, 'harfNotu': 'BA+'},
            {'bransKodu': 'BBB 101', 'kredisiDec': 2},
            {'bransKodu': 'CCC 101', 'donem': '202610', 'kredisiDec': 5},
        ]}
        self.assertEqual(app._plan_credit_lookup(info, '202620'), {'AAA 101': 3, 'BBB 101': 2})
        self.assertEqual(app._plan_grade_lookup(info, '202620'), {'AAA 101': 'BA+'})
        app._obs = Mock()
        app._obs.resolve_semester.return_value = {'akademikDonemId': 1, 'donemKodu': '202620'}
        app._obs.list_registered_courses.return_value = {'kayitSinifResultList': [
            {'bransKodu': 'AAA', 'dersKodu': '101', 'kredi': None, 'harfNotu': None}]}
        app._fetch_graduation_info = Mock(return_value=info)
        result = app.obs_calculate_gpa(include_official=False)
        self.assertEqual(result['gpa'], 3.75)
        self.assertEqual(result['courses'][0]['credit'], 3)

    def test_conflicting_fallback_attempts_are_not_resolved_by_row_order(self):
        app = NinovaMcpApp.__new__(NinovaMcpApp)
        rows = [
            {'bransKodu': 'AAA101', 'donem': '202620', 'kredisiDec': 4, 'harfNotu': 'FF'},
            {'bransKodu': 'AAA 101', 'donem': '202620', 'kredisiDec': 3, 'harfNotu': 'BA+'},
            {'bransKodu': 'BBB 101', 'kredisiDec': 'NaN'},
            {'bransKodu': 'CCC 101', 'kredisiDec': True},
        ]
        for order in (rows, rows[::-1]):
            info = {'checkMetMezuniyetList': order}
            self.assertEqual(app._plan_credit_lookup(info, '202620'), {})
            self.assertEqual(app._plan_grade_lookup(info, '202620'), {})

    def test_mismatch_is_explicit_and_does_not_change_estimate_arithmetic(self):
        result = {'gpa': 3.4, 'total_credits': 9.5}
        attach_official_reference(result, official_term_reference(status(), '202620'), projection=False)
        self.assertEqual(result['gpa'], 3.4)
        self.assertEqual(result['calculated_term_gpa'], 3.4)
        self.assertEqual(result['preferred_term_gpa'], 3.5)
        self.assertEqual(result['preferred_term_gpa_source'], 'official_obs')
        self.assertEqual(result['comparison_status'], 'mismatch')
        self.assertEqual(result['gpa_difference'], -.1)

    def test_projection_keeps_calculated_value_and_official_baseline(self):
        result = {'gpa': 4.0}
        attach_official_reference(result, official_term_reference(status(), '202620'), projection=True)
        self.assertEqual(result['preferred_term_gpa'], 4)
        self.assertEqual(result['official_term_gpa'], 3.5)
        self.assertEqual(result['comparison_status'], 'projection')
        self.assertIsNone(result['gpa_difference'])
        self.assertNotIn('calculation_warning', result)

    def test_wrong_term_invalid_number_or_ambiguous_program_cannot_be_preferred(self):
        for payload in [status(term='202610'), status(gpa='NaN'), status(gpa=True),
                        {'statusCode': 0, 'kayitDurumuList': status()['kayitDurumuList'] + status(term='202610')['kayitDurumuList']},
                        {'statusCode': 0, 'kayitDurumuList': status()['kayitDurumuList'] * 2},
                        {**status(), 'statusCode': 1}]:
            with self.subTest(payload=payload):
                ref = official_term_reference(payload, '202620')
                self.assertIsNone(ref['gpa'])
                result = {'gpa': 3.4}
                attach_official_reference(result, ref, projection=False)
                self.assertEqual(result['preferred_term_gpa'], 3.4)
                self.assertEqual(result['comparison_status'], 'unavailable')

    def test_zero_is_an_explicit_official_value_and_comma_decimal_is_supported(self):
        self.assertEqual(official_term_reference(status(gpa=0), '202620')['gpa'], 0)
        self.assertEqual(official_term_reference(status(gpa='3,5'), '202620')['gpa'], 3.5)

    def test_incomplete_calculation_or_unused_projection_is_not_preferred(self):
        result = {'gpa': None, 'calculation_complete': False, 'known_courses_gpa': 4}
        attach_official_reference(result, official_term_reference({}, '202620'), projection=False)
        self.assertIsNone(result['preferred_term_gpa'])
        self.assertEqual(result['preferred_term_gpa_source'], 'unavailable')
        result = {'gpa': 3, 'projection_complete': False}
        attach_official_reference(result, official_term_reference(status(), '202620'), projection=True)
        self.assertIsNone(result['preferred_term_gpa'])
        self.assertEqual(result['official_term_gpa'], 3.5)
        self.assertEqual(result['comparison_status'], 'projection_incomplete')

    def test_server_reads_only_matching_term_and_can_skip_extra_read(self):
        app = NinovaMcpApp.__new__(NinovaMcpApp)
        app._obs = Mock()
        app._obs.resolve_semester.return_value = {'akademikDonemId': 1, 'donemKodu': '202620'}
        app._obs.list_registered_courses.return_value = {'kayitSinifResultList': [
            {'bransKodu': 'AAA', 'dersKodu': '101', 'kredi': 3, 'harfNotu': 'BB'}]}
        app._obs.get_registration_status.return_value = status()
        app._fetch_graduation_info = Mock(return_value={})
        result = app.obs_calculate_gpa()
        self.assertEqual(result['gpa'], 3)
        self.assertEqual(result['official_term_gpa'], 3.5)
        app._obs.get_registration_status.reset_mock()
        self.assertEqual(app.obs_calculate_gpa(include_official=False)['official_reference']['status'], 'not_requested')
        app._obs.get_registration_status.assert_not_called()
