"""Literal extraction, bounded work, DOCX and authenticated worker transport."""
import base64
import hashlib
import http.client
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from api import create_server, openapi_spec
from local_extract import handle_local_extract

TEXT = 'ООО «Тест», ИНН 7707083893, ОГРН 1027700132195. Цена 1\u202f000,50 руб. Оплата до 01.02.2026.'


class LocalExtractionTests(unittest.TestCase):
    def test_real_local_mode_has_exact_unicode_spans_and_no_network(self):
        document = '  📝 ' + TEXT + '  '
        with patch.object(socket, 'create_connection', side_effect=AssertionError('network')), patch('extract.OpenAICompatibleModel', side_effect=AssertionError('model')):
            result = handle_local_extract({'document': document})
        resolved = document.strip()
        self.assertEqual({r['kind'] for r in result['matches']}, {'инн', 'огрн', 'amount', 'date'})
        for row in result['matches']:
            self.assertEqual(resolved[row['span']['start']:row['span']['end']], row['quote'])
            self.assertFalse(row['verified'])
        self.assertEqual(result['text_sha256'], hashlib.sha256(resolved.encode()).hexdigest())
        self.assertEqual(result['span_unit'], 'unicode_code_points')
        self.assertFalse(result['demo'])
        self.assertFalse(result['external_requests_attempted'])
        self.assertFalse(result['document_persisted'])

    def test_no_party_names_or_deadlines_are_invented(self):
        result = handle_local_extract({'document': TEXT})
        self.assertNotIn('parties', result)
        self.assertNotIn('deadline', result)
        self.assertTrue(all(row.get('registry_checked') is False for row in result['matches'] if row['kind'] in {'инн', 'огрн'}))

    def test_identifiers_require_labels_and_checksum_is_not_registry_lookup(self):
        result = handle_local_extract({'document': 'Реквизиты: 7707083893 и 1027700132195 без обозначения вида.'})
        self.assertEqual(result['matches'], [])
        self.assertFalse(result['truncated'])

    def test_bad_checksums_and_wrong_identifier_lengths_are_visible(self):
        text = 'Реквизиты: ИНН 123456789. ОГРН 304500116000157. ОГРНИП 1027700132195.'
        result = handle_local_extract({'document': text})
        self.assertEqual(len(result['matches']), 3)
        self.assertTrue(all(not row['checksum_matches'] for row in result['matches']))
        self.assertEqual(len(result['warnings']), 3)

    def test_invalid_date_retained_as_unresolved_and_iso_date_supported(self):
        result = handle_local_extract({'document': 'Даты в документе: 31.02.2026 и 2024-02-29.'})
        first, second = result['matches']
        self.assertFalse(first['calendar_valid'])
        self.assertIsNone(first['normalized_date'])
        self.assertEqual(second['normalized_date'], '2024-02-29')
        self.assertEqual(result['warnings'][0]['code'], 'invalid_calendar_date')

    def test_currency_and_nonbreaking_thousands_separators_preserved(self):
        result = handle_local_extract({'document': 'Суммы: 1\u00a0000,50 ₽; -20.25 USD; 50 EUR; 60 RUB; 70 €; 80 $.'})
        self.assertEqual([r['currency'] for r in result['matches']], ['RUB', 'USD', 'EUR', 'RUB', 'EUR', 'USD'])
        self.assertEqual(result['matches'][0]['quote'], '1\u00a0000,50 ₽')
        self.assertEqual(result['matches'][1]['value'], '-20.25 USD')

    def test_output_limit_keeps_earliest_matches_across_types(self):
        text = 'Сумма 50 руб. Дата 01.02.2026. ИНН 7707083893.'
        result = handle_local_extract({'document': text, 'max_matches': 2})
        self.assertEqual([row['kind'] for row in result['matches']], ['amount', 'date'])
        self.assertTrue(result['truncated'])
        result = handle_local_extract({'document': text, 'max_matches': 3})
        self.assertFalse(result['truncated'])

    def test_output_remains_bounded_on_many_short_matches(self):
        result = handle_local_extract({'document': 'Сумма 1 ₽. ' * 10000, 'max_matches': 5})
        self.assertEqual(len(result['matches']), 5)
        self.assertTrue(result['truncated'])
        self.assertLess(len(json.dumps(result)), 10000)

    def test_input_ambiguity_types_and_unknown_fields_rejected(self):
        for extra in [{'document_base64': 'abc'}, {'max_matches': True}, {'max_matches': 0},
                      {'max_matches': 501}, {'max_matches': '5'}, {'url': 'https://example.org'},
                      {'demo': True}, {'document': []}, {'document': 'x' * 200001}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                handle_local_extract({'document': TEXT, **extra})

    def test_docx_input_has_positions_in_resolved_text(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+TEXT+'</w:t></w:r></w:p></w:body></w:document>')
        result = handle_local_extract({'document_base64': base64.b64encode(archive.getvalue()).decode()})
        self.assertEqual(len(result['matches']), 4)

    def test_http_auth_isolation_and_no_document_files(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server('127.0.0.1', 0, directory, 'local-token')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for token, expected in [('', 401), ('local-token', 200)]:
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    try:
                        connection.request('POST', '/extract/local', json.dumps({'document': TEXT}),
                                           {'Content-Type': 'application/json', 'Authorization': 'Bearer '+token})
                        response = connection.getresponse()
                        result = json.load(response)
                        self.assertEqual(response.status, expected)
                        if expected == 200:
                            self.assertEqual(result['mode'], 'local_patterns')
                            self.assertFalse(result['external_requests_attempted'])
                            self.assertTrue(response.getheader('X-Request-ID'))
                    finally:
                        connection.close()
                self.assertEqual(list(Path(directory).iterdir()), [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_schema_exposes_worker_deadline_and_auth(self):
        operation = openapi_spec()['paths']['/extract/local']['post']
        self.assertTrue(operation['security'])
        self.assertIn('504', operation['responses'])
