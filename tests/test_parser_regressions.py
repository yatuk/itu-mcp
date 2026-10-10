"""Regression cases for real page layouts with synthetic, non-personal data."""
import unittest
from unittest.mock import patch

from ninova_mcp.client import NinovaError
from ninova_mcp.compact import compact_value
from ninova_mcp.parsing import extract_named_table, extract_remote_learning
from ninova_mcp.public_parsing import extract_directory_detail
from ninova_mcp.server import NinovaMcpApp


class ParserRegressionTests(unittest.TestCase):
    def test_nested_date_tables_are_part_of_three_assignment_rows(self):
        html = '<h2>Son Ödevler</h2><table><tr><th>Ödev</th><th>Tarih</th></tr>'
        html += ''.join(f'<tr><td>Assignment {i}</td><td><table><tr><td>01 Eylül</td>'
                        '</tr><tr><td>00:00</td></tr></table></td></tr>' for i in range(3))
        rows = extract_named_table(html + '</table>', 'Son Ödevler')
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {'Ödev': 'Assignment 0', 'Tarih': '01 Eylül 00:00'})

    def test_empty_remote_messages_with_or_without_headers_are_not_sessions(self):
        for headers in ['', '<tr><th>Başlık</th><th>Bağlantı</th></tr>']:
            with self.subTest(headers=bool(headers)):
                html = ''.join(f'<h2>{heading}</h2><table>{headers}<tr><td colspan="2">'
                               'Sınıfınıza eklenmiş herhangi bir uzaktan eğitim oturumu bulunmamaktadır.'
                               '</td></tr></table>' for heading in
                               ['Aktif Uzaktan Eğitim Oturumlarınız', 'Sınıfın Geçmiş Uzaktan Eğitim Oturumları'])
                result = extract_remote_learning(html, 'https://ninova.itu.edu.tr/page', 'https://ninova.itu.edu.tr')
                for value in [result, compact_value(result)]:
                    self.assertEqual(value['active_count'], 0)
                    self.assertEqual(value['past_count'], 0)
                    self.assertEqual(value['active_sessions'], [])
                    self.assertEqual(value['past_sessions'], [])

    def test_real_remote_title_time_and_url_survive_compact(self):
        html = '''<h2>Aktif Uzaktan Eğitim Oturumlarınız</h2><table>
        <tr><th>Başlık</th><th>Başlangıç</th><th>Bağlantı</th></tr>
        <tr><td>Example lecture</td><td>17 Eylül 2026 10:00</td>
        <td><a href="/join/1">Katıl</a></td></tr></table>'''
        result = compact_value(extract_remote_learning(html, 'https://ninova.itu.edu.tr/page', 'https://ninova.itu.edu.tr'))
        self.assertEqual(result['active_count'], 1)
        session = result['active_sessions'][0]
        self.assertEqual(session['title'], 'Example lecture')
        self.assertEqual(session['start_at'], '2026-09-17T10:00:00+03:00')
        self.assertEqual(session['meeting_url'], 'https://ninova.itu.edu.tr/join/1')

    def test_directory_four_columns_and_multiple_public_contact_rows(self):
        html = '''<h1>İstanbul Teknik Üniversitesi Rehber Servisi</h1><div class="white-box">
        <h2>Dr. Example Person</h2><table><thead><tr><th>Birim</th><th>Bölüm</th>
        <th>İş Telefonu *</th><th>E-posta Adresi</th></tr></thead><tbody>
        <tr><td>Unit</td><td>Department</td><td>1234</td><td>example@example.org</td></tr>
        <tr><td>Other Unit</td><td>Other Department</td><td></td><td>other@example.org</td></tr>
        </tbody></table><label>Kişinin detaylı bilgilerini görebilmek için giriş yapmalısınız.</label></div>'''
        result = extract_directory_detail(html, 'https://rehber.itu.edu.tr/example')
        self.assertEqual(result['full_name'], 'Dr. Example Person')
        self.assertEqual(result['phone'], '1234')
        self.assertEqual(result['email'], 'example@example.org')
        self.assertEqual(len(result['contacts']), 2)
        self.assertEqual(result['contacts'][0]['primary_fields'], ['phone'])
        self.assertTrue(result['additional_details_require_login'])

    def test_directory_masthead_alone_is_not_person(self):
        result = extract_directory_detail('<h1>İstanbul Teknik Üniversitesi Rehber Servisi</h1>', 'https://rehber.itu.edu.tr/')
        self.assertNotIn('full_name', result)
        self.assertIn('parse_warning', result)

    def test_directory_login_heading_does_not_invent_a_person(self):
        for wrapper in ('<main>{}</main>', '<div class="white-box">{}</div>'):
            html = '<h1>İTÜ Rehber</h1>' + wrapper.format('<h2>Giriş Yapmalısınız</h2>')
            result = extract_directory_detail(html, 'https://rehber.itu.edu.tr/example')
            self.assertNotIn('full_name', result)
            self.assertTrue(result['additional_details_require_login'])
            self.assertIn('parse_warning', result)

    def test_global_directory_heading_needs_contact_evidence(self):
        html = '<h1>Example Person</h1>'
        result = extract_directory_detail(html, 'https://rehber.itu.edu.tr/example')
        self.assertNotIn('full_name', result)
        self.assertIn('parse_warning', result)
        result = extract_directory_detail(html + '<table><tr><td>Telefon</td><td>1234</td></tr></table>',
                                          'https://rehber.itu.edu.tr/example')
        self.assertEqual(result['full_name'], 'Example Person')
        self.assertEqual(result['phone'], '1234')

    def test_ambiguous_profile_headings_do_not_choose_a_name(self):
        html = '<div class="white-box"><h2>Example Person</h2><h2>Another Person</h2></div>'
        result = extract_directory_detail(html, 'https://rehber.itu.edu.tr/example')
        self.assertNotIn('full_name', result)
        self.assertIn('parse_warning', result)


class TicketFallbackTests(unittest.TestCase):
    def setUp(self):
        self.app = NinovaMcpApp()
        self.html = '<ul data-placement="yardim-list">' + ''.join(
            f'<li class="help__list-item"><a href="/ticket/{i}"><span class="pull-left">'
            f'Example {i}</span><span class="pull-right">Today</span></a></li>' for i in range(30)) + '</ul>'

    def test_both_fallback_causes_apply_filter_and_limit_after_parsing(self):
        for cause in [{}, NinovaError('unavailable')]:
            with self.subTest(cause=type(cause).__name__), patch.object(self.app, '_get_portal_page', return_value=(self.html, 'https://portal.itu.edu.tr/apps/default/')):
                with patch.object(self.app, '_get_portal_json', **({'side_effect': cause} if isinstance(cause, Exception) else {'return_value': cause})):
                    self.assertEqual(self.app.obs_get_help_tickets(query='NOT_PRESENT', limit=1)['count'], 0)
                    result = self.app.obs_get_help_tickets(query='Example 29', limit=1)
                    self.assertEqual(result['count'], 1)
                    self.assertEqual(result['tickets'][0]['url'], 'https://portal.itu.edu.tr/ticket/29')
                    self.assertFalse(result['metadata_complete'])
                    result = self.app.obs_get_help_tickets(limit=2)
                    self.assertEqual(result['count'], 2)
                    self.assertEqual(result['total_matching_count'], 30)

    def test_json_and_html_ticket_limits_have_same_contract(self):
        rows = [{'Id': str(i), 'Title': f'Example {i}', 'Status': 'Open', 'Url': f'/ticket/{i}'} for i in range(3)]
        with patch.object(self.app, '_get_portal_json', return_value={'YardimInformationList': rows}):
            result = self.app.obs_get_help_tickets(query='Open', limit=1)
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['total_matching_count'], 3)
        self.assertTrue(result['metadata_complete'])
        self.assertEqual(result['tickets'][0]['url'], 'https://portal.itu.edu.tr/ticket/0')
