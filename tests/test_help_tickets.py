from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ninova_mcp.client import NinovaError
from ninova_mcp.help_tickets import date_value, help_url, parse_detail, summary
from ninova_mcp.parsing import extract_help_tickets
from ninova_mcp.server import NinovaMcpApp, REMOTE_TOOL_NAMES, register_tools

ID = '1234567'
URL = f'https://yardim.itu.edu.tr/itubilet.aspx?id={ID}'


def detail_html(messages=None, identifier=ID):
    if messages is None:
        messages = [('Example Student', '03.10.2026 12:00', 'Please provide a document.')]
    rows = ''.join(f'<tr><td><strong>{author}</strong><br>{date}</td><td>{body}</td></tr>'
                   for author, date, body in reversed(messages))
    return f'''<div id="sub"><div class="results"><div class="panel-body">
        <h1 class="text-info">Example document request</h1>
        <div id="ctl00_ctl00_ctl00_cphMain_pageMain_pageDetailMain_AForm">
        <table><thead><tr><th>Yardım Bileti Bilgileri</th></tr></thead><tbody>
        <tr><td>Bilet No</td><td>{identifier}</td></tr>
        <tr><td>İlgili Birim</td><td>Example Office</td></tr>
        <tr><td>Kategori</td><td>Öğrenci İşleri</td></tr>
        <tr><td>Alt Kategori</td><td></td></tr>
        <tr><td>Durumu</td><td class="red">Yeni</td></tr>
        </tbody></table><table><thead><tr><th>Yapılan İşlemler</th></tr></thead>
        <tbody>{rows}</tbody></table>
        <h2>Cevap Yaz</h2><p>Instruction about answering</p>
        <textarea>Draft must not be included.</textarea><input type="submit" value="Send">
        </div></div></div></div>'''


class HelpParserTests(unittest.TestCase):
    def test_real_portal_field_names_and_legacy_http_url(self):
        item = summary({'URL': f'http://yardim.itu.edu.tr/bilet.aspx?id={ID}',
                        'Title': 'Example', 'TicketStateName': 'Yeni', 'BeforeCreateDate': '2 dk'})
        self.assertEqual(item['id'], ID)
        self.assertEqual(item['status'], 'Yeni')
        self.assertEqual(item['url'], f'https://yardim.itu.edu.tr/bilet.aspx?id={ID}')
        self.assertIsNone(item['created_at'])
        self.assertIsNone(item['has_reply'])
        self.assertEqual(item['age'], '2 dk')

    def test_url_id_takes_precedence_over_unrelated_object_id(self):
        self.assertEqual(summary({'ObjectId': '99', 'URL': URL})['id'], ID)

    def test_widget_extracts_id_status_and_excludes_badge_from_title(self):
        html = f'''<ul data-placement="yardim-list"><li class="help__list-item">
            <a href="{URL}"><span class="pull-left">Example title<span class="panel-red">Arşiv</span></span>
            <span class="pull-right">4 g</span></a></li></ul>'''
        item = extract_help_tickets(html, 'https://portal.itu.edu.tr/apps/default/')['tickets'][0]
        self.assertEqual(item['id'], ID)
        self.assertEqual(item['title'], 'Example title')
        self.assertEqual(item['status'], 'Arşiv')
        self.assertTrue(item['archived'])
        self.assertEqual(item['date'], '4 g')

    def test_widget_does_not_truncate_before_tool_query(self):
        html = '<ul data-placement="yardim-list">' + ''.join(
            f'<li class="help__list-item"><a href="/itubilet.aspx?id={i}"><span class="pull-left">Ticket {i}</span></a></li>'
            for i in range(30)) + '</ul>'
        result = extract_help_tickets(html, 'https://portal.itu.edu.tr/apps/default/')
        self.assertEqual(result['count'], 30)
        self.assertEqual(len(result['tickets']), 30)

    def test_description_history_dates_and_no_reply(self):
        item = parse_detail(detail_html(), URL, ID)
        self.assertEqual(item['id'], ID)
        self.assertEqual(item['unit'], 'Example Office')
        self.assertEqual(item['category'], 'Öğrenci İşleri')
        self.assertEqual(item['description'], 'Please provide a document.')
        self.assertEqual(item['created_at'], '2026-10-03T12:00:00')
        self.assertEqual(item['updated_at'], item['created_at'])
        self.assertFalse(item['has_reply'])
        self.assertIsNone(item['institution_reply'])
        self.assertNotIn('Draft', str(item))
        self.assertNotIn('Cevap Yaz', str(item))

    def test_followup_is_not_reply_and_staff_reply_lists_safe_attachments(self):
        html = detail_html([
            ('Example Student', '03.10.2026 12:00', 'Please provide a document.'),
            ('Example Student', '03.10.2026 12:05', 'Additional information.'),
            ('Example Office', '03.10.2026 13:10:20', '<p>Your document.</p><a href="/files/example.pdf">example.pdf</a><a href="https://outside.example/x.pdf">external</a><script>ignore</script>'),
        ])
        item = parse_detail(html, URL, ID)
        self.assertTrue(item['has_reply'])
        self.assertEqual(len(item['replies']), 1)
        self.assertFalse(item['messages'][1]['is_reply'])
        self.assertEqual(item['updated_at'], '2026-10-03T13:10:20')
        self.assertEqual(item['attachments'], [{'name': 'example.pdf', 'url': 'https://yardim.itu.edu.tr/files/example.pdf'}])
        self.assertNotIn('ignore', item['institution_reply'])

    def test_archiving_event_updates_timestamp_without_becoming_a_reply(self):
        html = detail_html([
            ('Example Student', '07/23/2026 15:16:31', 'Request'),
            ('Example Office', '07/24/2026 11:07:00', 'Institution answer'),
        ])
        event = '<tr><td><strong><i class="archive"></i></strong><td>Bilet <strong>Example Student</strong> tarafından @07/27/2026 10:17:42 arşive alındı.</td></tr>'
        html = html.replace('<tbody><tr><td><strong>Example Office', '<tbody>' + event + '<tr><td><strong>Example Office')
        item = parse_detail(html, URL, ID)
        self.assertEqual(item['description'], 'Request')
        self.assertEqual(item['created_at'], '2026-07-23T15:16:31')
        self.assertEqual(item['updated_at'], '2026-07-27T10:17:42')
        self.assertEqual(item['institution_reply'], 'Institution answer')
        self.assertEqual(item['messages'][-1]['kind'], 'status_change')
        self.assertFalse(item['messages'][-1]['is_reply'])
        self.assertTrue(item['has_reply'])

    def test_missing_history_does_not_claim_no_reply(self):
        item = parse_detail(detail_html([]), URL, ID)
        self.assertIsNone(item['has_reply'])
        self.assertIsNone(item['created_at'])
        self.assertIn('parse_warning', item)

    def test_unidentified_author_keeps_reply_unknown(self):
        item = parse_detail(detail_html().replace('<strong>Example Student</strong>', ''), URL, ID)
        self.assertIsNone(item['has_reply'])

    def test_unknown_layout_wrong_ticket_and_empty_fields(self):
        self.assertIn('parse_warning', parse_detail('<h1>Not found</h1>', URL, ID))
        self.assertIn('parse_warning', parse_detail(detail_html(identifier='9'), URL, ID))
        self.assertIsNone(parse_detail(detail_html(), URL, ID)['subcategory'])

    def test_malformed_archived_cell_is_read_without_losing_body(self):
        html = detail_html().replace('</strong><br>03.10.2026 12:00</td><td>', '</strong><br>03.10.2026 12:00<td>')
        item = parse_detail(html, URL, ID)
        self.assertEqual(item['description'], 'Please provide a document.')
        self.assertEqual(item['created_at'], '2026-10-03T12:00:00')

    def test_help_url_rejects_untrusted_and_credentialed_links(self):
        for url in ['https://evil.example/bilet.aspx?id=1', 'javascript:alert(1)', '//evil.example/x',
                    'https://user:password@yardim.itu.edu.tr/bilet.aspx?id=1', 'https://yardim.itu.edu.tr:8000/x']:
            self.assertIsNone(help_url(url))
        self.assertIsNone(summary({'URL': 'https://evil.example/bilet.aspx?id=123'})['id'])

    def test_date_only_iso_and_relative_age(self):
        self.assertEqual(date_value('03.10.2026'), '2026-10-03')
        self.assertEqual(date_value('10/03/2026 17:51:47'), '2026-10-03T17:51:47')
        self.assertEqual(date_value('08/20/2026 18:40:35'), '2026-08-20T18:40:35')
        self.assertEqual(date_value('2026-10-03T12:00:00+03:00'), '2026-10-03T12:00:00+03:00')
        self.assertIsNone(date_value('4 g'))
        self.assertIsNone(date_value('99.99.2026'))


class HelpToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict('os.environ', {'NINOVA_STATE_DIR': self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = NinovaMcpApp()
        self.app._client = Mock()
        self.app._client.get_html.return_value = (detail_html(), SimpleNamespace(url=URL))
        self.rows = [{'URL': URL, 'Title': 'Example document request', 'TicketStateName': 'Yeni'}]

    def test_detail_reads_fixed_url_and_caches_without_mutation_or_download(self):
        result = self.app.get_help_ticket(ID)
        self.assertEqual(result['ticket']['id'], ID)
        self.assertTrue(result['untrusted_external_content'])
        self.assertEqual(self.app.get_help_ticket(ID), result)
        self.app._client.get_html.assert_called_once_with(URL)
        self.assertFalse(self.app._client.post.called)

    def test_invalid_ids_are_rejected_before_any_client_read(self):
        for identifier in ['../x', '1&foo=2', '', '-1', '１２３', '1' * 21, 'https://example.org']:
            with self.subTest(identifier=identifier), self.assertRaises(NinovaError):
                self.app.get_help_ticket(identifier)
        self.app._client.get_html.assert_not_called()

    def test_login_redirect_and_wrong_ticket_are_errors(self):
        self.app._client.get_html.return_value = (detail_html(), SimpleNamespace(url='https://girisv3.itu.edu.tr/Login.aspx'))
        with self.assertRaises(NinovaError):
            self.app.get_help_ticket(ID)
        self.app._client.get_html.return_value = (detail_html(identifier='9'), SimpleNamespace(url=URL))
        with self.assertRaises(NinovaError):
            self.app.get_help_ticket(ID)

    def test_list_filters_by_id_and_enriches_selected_rows_only(self):
        rows = self.rows + [{'Id': '9', 'Title': 'Other', 'Status': 'Open'}]
        with patch.object(self.app, '_get_portal_json', return_value={'YardimInformationList': rows}):
            result = self.app.obs_get_help_tickets(query=ID, limit=1)
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['total_matching_count'], 1)
        self.assertEqual(result['tickets'][0]['unit'], 'Example Office')
        self.assertFalse(result['tickets'][0]['has_reply'])
        self.assertTrue(result['metadata_complete'])
        self.app._client.get_html.assert_called_once_with(URL)

    def test_portal_only_mode_has_stable_null_fields_and_does_not_read_detail(self):
        with patch.object(self.app, '_get_portal_json', return_value={'YardimInformationList': self.rows}):
            result = self.app.obs_get_help_tickets(include_details=False)
        item = result['tickets'][0]
        self.assertIsNone(item['unit'])
        self.assertIn('unit', item['missing_fields'])
        self.assertFalse(result['metadata_complete'])
        self.app._client.get_html.assert_not_called()

    def test_http_failure_is_reported_and_list_retains_metadata(self):
        from requests import HTTPError

        self.app._client.get_html.side_effect = HTTPError('404')
        with self.assertRaises(NinovaError):
            self.app.get_help_ticket(ID)
        with patch.object(self.app, '_get_portal_json', return_value={'YardimInformationList': self.rows}):
            result = self.app.obs_get_help_tickets()
        self.assertEqual(result['tickets'][0]['id'], ID)
        self.assertFalse(result['tickets'][0]['detail_available'])

    def test_detail_failure_retains_portal_fields(self):
        self.app._client.get_html.side_effect = NinovaError('Unavailable')
        with patch.object(self.app, '_get_portal_json', return_value={'YardimInformationList': self.rows}):
            result = self.app.obs_get_help_tickets()
        self.assertEqual(result['tickets'][0]['id'], ID)
        self.assertEqual(result['tickets'][0]['status'], 'Yeni')
        self.assertFalse(result['tickets'][0]['detail_available'])
        self.assertIn('detail_warning', result['tickets'][0])

    def test_all_fallback_causes_filter_after_parsing_and_limit(self):
        html = '<ul data-placement="yardim-list">' + ''.join(
            f'<li class="help__list-item"><a href="/itubilet.aspx?id={i}"><span class="pull-left">Example {i}</span></a></li>'
            for i in range(30)) + '</ul>'
        for kwargs in [{'return_value': {}}, {'side_effect': NinovaError('unavailable')}]:
            with patch.object(self.app, '_get_portal_json', **kwargs), patch.object(self.app, '_get_portal_page', return_value=(html, 'https://portal.itu.edu.tr/apps/default/')):
                result = self.app.obs_get_help_tickets(query='Example 29', limit=1, include_details=False)
                self.assertEqual(result['count'], 1)
                self.assertEqual(result['tickets'][0]['id'], '29')
                self.assertTrue(result['untrusted_external_content'])
                result = self.app.obs_get_help_tickets(limit=2, include_details=False)
                self.assertEqual(result['count'], 2)
                self.assertEqual(result['total_matching_count'], 30)
                self.assertEqual(self.app.obs_get_help_tickets(query='ABSENT', include_details=False)['count'], 0)

    def test_reply_flag_false_is_present_not_missing(self):
        with patch.object(self.app, '_get_portal_json', return_value={'YardimInformationList': [{'Id': ID, 'Title': 'Example', 'HasReply': False}]}):
            item = self.app.obs_get_help_tickets(include_details=False)['tickets'][0]
        self.assertFalse(item['has_reply'])
        self.assertNotIn('has_reply', item['missing_fields'])

    def test_detail_is_registered_remotely_with_readonly_annotations(self):
        self.assertIn('get_help_ticket', REMOTE_TOOL_NAMES)
        mcp = Mock()
        register_tools(mcp, self.app, ['get_help_ticket'])
        kwargs = mcp.add_tool.call_args.kwargs
        self.assertTrue(kwargs['annotations'].readOnlyHint)
        self.assertFalse(kwargs['annotations'].destructiveHint)
        self.assertTrue(kwargs['annotations'].idempotentHint)


if __name__ == '__main__':
    unittest.main()
