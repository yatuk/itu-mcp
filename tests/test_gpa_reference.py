"""Official semester values and projections must remain distinguishable."""
import unittest
from unittest.mock import Mock

from ninova_mcp.gpa_reference import official_term_reference, attach_official_reference
from ninova_mcp.server import NinovaMcpApp


def status(gpa=3.5, credits=10, term='202620'):
    return {'kayitDurumuList': [{'akademikProgramAdi': 'Example program',
        'kayitDurumuDonemList': [{'akademikDonemKodu': term,
            'donemlikNotOrtalamasi': gpa, 'verilenKredi': credits}]}]}


class OfficialGpaTests(unittest.TestCase):
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
                        {'kayitDurumuList': status()['kayitDurumuList'] + status(term='202610')['kayitDurumuList']},
                        {'kayitDurumuList': status()['kayitDurumuList'] * 2}]:
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
