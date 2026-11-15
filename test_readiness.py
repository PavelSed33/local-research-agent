"""Per-workflow configuration, no external probes, no secret values, authenticated GET."""
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from api import create_server, health_payload, openapi_spec
from preflight import inspect_configuration, inspect_runtime

MODEL = {'LLM_API_KEY': 'PRIVATE-KEY', 'LLM_MODEL': 'PRIVATE-MODEL',
         'ALLOW_CLOUD_DOCUMENTS': 'true'}


def workflows(report):
    return {row['id']: row for row in report['workflows']}


class WorkflowReadinessTests(unittest.TestCase):
    def test_local_features_available_without_providers_or_network(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(socket, 'create_connection', side_effect=AssertionError('external network')):
            report = inspect_runtime()
        rows = workflows(report)
        for identity in ['/diff', '/extract/local']:
            self.assertEqual(rows[identity]['status'], 'ready_locally')
        for identity in ['/privacy/check', '/legal/research', '/legal/corpus', '/legal/coverage']:
            self.assertEqual(rows[identity]['status'], 'blocked')
            self.assertFalse(rows[identity]['can_attempt'])
            self.assertTrue(rows[identity]['blockers'])
        self.assertFalse(rows['/ask']['can_attempt'])
        self.assertFalse(report['external_services_tested'])

    def test_model_only_enables_contract_skills_but_not_web_audit(self):
        with patch.dict('os.environ', MODEL, clear=True), \
                patch('preflight.evidence_report', return_value={'entries': [], 'errors': []}), \
                patch('preflight.corpus_report', return_value={'entries': [], 'errors': []}):
            rows = workflows(inspect_runtime())
        for identity in ['contract-risk', 'contract-playbook', 'nda-screen', 'fact-timeline', 'legal-search-plan', '/extract', '/review']:
            self.assertEqual(rows[identity]['status'], 'configured_unchecked')
            self.assertTrue(rows[identity]['can_attempt'])
        self.assertEqual(rows['claim-audit']['blockers'], ['search'])
        self.assertEqual(rows['/ask']['blockers'], ['search'])
        # Without accepted local statutory extracts or evidence there is no safe fallback.
        self.assertFalse(rows['/review/legal']['local_fallback_can_attempt'])
        self.assertFalse(rows['claim-audit']['local_fallback_can_attempt'])

    def test_local_model_does_not_need_cloud_document_opt_in(self):
        env = {k: v for k, v in MODEL.items() if k != 'ALLOW_CLOUD_DOCUMENTS'}
        env['LLM_BASE_URL'] = 'http://127.0.0.1:12345/v1'
        with patch.dict('os.environ', env, clear=True):
            rows = workflows(inspect_runtime())
        self.assertTrue(rows['contract-risk']['can_attempt'])
        self.assertFalse(rows['claim-audit']['can_attempt'])

    def test_cloud_policy_is_independent_of_key_configuration(self):
        env = {k: v for k, v in MODEL.items() if k != 'ALLOW_CLOUD_DOCUMENTS'}
        env['TAVILY_API_KEY'] = 'PRIVATE-SEARCH'
        with patch.dict('os.environ', env, clear=True):
            rows = workflows(inspect_runtime())
        self.assertTrue(rows['/ask']['can_attempt'])
        self.assertEqual(rows['contract-risk']['blockers'], ['document_transfer'])

    def test_invalid_search_endpoint_does_not_disable_model_only_features(self):
        with patch.dict('os.environ', {**MODEL, 'TAVILY_API_KEY': 'PRIVATE-SEARCH',
                                      'TAVILY_API_URL': 'http://example.org/search'}, clear=True):
            report = inspect_runtime()
        self.assertIn('TAVILY_API_URL', report['invalid_endpoint_configuration'])
        self.assertTrue(workflows(report)['contract-risk']['can_attempt'])
        self.assertFalse(workflows(report)['claim-audit']['can_attempt'])

    def test_health_rejects_placeholders_and_whitespace(self):
        for value in ['your_api_key', 'changeme', '   ']:
            with self.subTest(value=value), patch.dict('os.environ', {
                    'LLM_API_KEY': value, 'LLM_MODEL': 'your_model', 'TAVILY_API_KEY': 'your_tavily_api_key'}, clear=True):
                health = health_payload()
            self.assertFalse(health['llm_configured'])
            self.assertFalse(health['tavily_configured'])

    def test_bad_skills_do_not_disable_local_comparison(self):
        with patch.dict('os.environ', MODEL, clear=True), patch('preflight.load_skills', side_effect=ValueError('PRIVATE-PATH')):
            report = inspect_runtime()
        self.assertFalse(report['legal_skills']['integrity_ok'])
        self.assertEqual(workflows(report)['contract-risk']['blockers'], ['skills'])
        self.assertTrue(workflows(report)['/diff']['can_attempt'])
        self.assertNotIn('PRIVATE-PATH', json.dumps(report))

    def test_empty_statutory_corpus_cannot_appear_ready(self):
        with patch.dict('os.environ', {}, clear=True), patch('preflight.corpus_report', return_value={'entries': [], 'errors': []}):
            rows = workflows(inspect_runtime())
        self.assertEqual(rows['/legal/corpus']['blockers'], ['statutes'])
        self.assertTrue(rows['/diff']['can_attempt'])

    def test_statutory_readiness_reports_missing_coverage_without_faking_acceptance(self):
        with patch.dict('os.environ', {}, clear=True), \
                patch('preflight.corpus_report', return_value={'entries': [], 'errors': []}):
            report = inspect_runtime()
        coverage = report['statutory_corpus']
        self.assertGreater(coverage['expected_card_count'], 0)
        self.assertEqual(coverage['missing_card_count'], coverage['expected_card_count'])
        self.assertFalse(coverage['coverage_complete'])
        self.assertFalse(coverage['legal_acceptance'])
        self.assertIn('statutes', workflows(report)['/legal/corpus']['blockers'])

    def test_complete_synthetic_coverage_is_not_legal_acceptance(self):
        registry_path = Path(__file__).parent / 'fixtures' / 'legal_coverage.json'
        registry = json.loads(registry_path.read_text(encoding='utf-8'))
        entries = [{'card_id': card['id']} for card in registry['cards']]
        with patch.dict('os.environ', {}, clear=True), \
                patch('preflight.corpus_report', return_value={'entries': entries, 'errors': []}), \
                patch('preflight.evidence_report', return_value={'entries': [], 'errors': []}):
            report = inspect_runtime()
        coverage = report['statutory_corpus']
        self.assertTrue(coverage['coverage_complete'])
        self.assertEqual(coverage['missing_card_count'], 0)
        self.assertFalse(coverage['legal_acceptance'])
        self.assertFalse(report['legal_acceptance'])
        self.assertEqual(workflows(report)['/legal/corpus']['status'], 'ready_locally')
        self.assertEqual(workflows(report)['/legal/research']['blockers'], ['evidence'])

    def test_duplicate_registry_card_ids_cannot_claim_complete_coverage(self):
        original_loads = json.loads
        registry_path = Path(__file__).parent / 'fixtures' / 'legal_coverage.json'
        registry = original_loads(registry_path.read_text(encoding='utf-8'))
        complete_entries = [{'card_id': card['id']} for card in registry['cards']]

        def duplicate_cards(raw, *args, **kwargs):
            value = original_loads(raw, *args, **kwargs)
            if isinstance(value, dict) and 'cards' in value and value['cards']:
                value['cards'].append(dict(value['cards'][0]))
            return value

        with patch.dict('os.environ', {}, clear=True), \
                patch('preflight.json.loads', side_effect=duplicate_cards), \
                patch('preflight.corpus_report', return_value={'entries': complete_entries, 'errors': []}):
            report = inspect_runtime()
        self.assertFalse(report['statutory_corpus']['coverage_complete'])
        self.assertIn('statutes', workflows(report)['/legal/corpus']['blockers'])

    def test_blank_registry_id_does_not_count_as_coverage(self):
        original_loads = json.loads
        registry_path = Path(__file__).parent / 'fixtures' / 'legal_coverage.json'
        registry = original_loads(registry_path.read_text(encoding='utf-8'))
        complete_entries = [{'card_id': card['id']} for card in registry['cards']]
        complete_entries[0]['card_id'] = ''

        def blank_card(raw, *args, **kwargs):
            value = original_loads(raw, *args, **kwargs)
            if isinstance(value, dict) and value.get('cards'):
                value['cards'][0]['id'] = ''
            return value

        with patch.dict('os.environ', {}, clear=True), \
                patch('preflight.json.loads', side_effect=blank_card), \
                patch('preflight.corpus_report', return_value={'entries': complete_entries, 'errors': []}):
            report = inspect_runtime()
        self.assertFalse(report['statutory_corpus']['coverage_complete'])
        self.assertIn('statutes', workflows(report)['/legal/corpus']['blockers'])

    def test_whitespace_alias_in_complete_registry_fails_closed(self):
        registry_path = Path(__file__).parent / 'fixtures' / 'legal_coverage.json'
        original_loads = json.loads
        registry = original_loads(registry_path.read_text(encoding='utf-8'))
        entries = [{'card_id': card['id']} for card in registry['cards']]
        entries[0]['card_id'] = ' ' + entries[0]['card_id']

        def whitespace_alias(raw, *args, **kwargs):
            value = original_loads(raw, *args, **kwargs)
            if isinstance(value, dict) and value.get('cards'):
                value['cards'][0]['id'] = ' ' + value['cards'][0]['id']
            return value

        with patch.dict('os.environ', {}, clear=True), \
                patch('preflight.json.loads', side_effect=whitespace_alias), \
                patch('preflight.corpus_report', return_value={'entries': entries, 'errors': []}):
            report = inspect_runtime()
        self.assertFalse(report['statutory_corpus']['coverage_complete'])
        self.assertIn('statutes', workflows(report)['/legal/corpus']['blockers'])

    def test_empty_legal_evidence_blocks_evidence_dependent_workflows(self):
        # Missing evidence must not be reported as locally ready, even when
        # the statutory corpus is otherwise available.
        with patch.dict('os.environ', {}, clear=True), \
                patch('preflight.evidence_report', return_value={'entries': [], 'errors': []}), \
                patch('preflight.corpus_report', return_value={'entries': [{'id': 'synthetic'}], 'errors': []}):
            rows = workflows(inspect_runtime())
        self.assertEqual(rows['/privacy/check']['blockers'], ['evidence'])
        self.assertEqual(rows['/legal/research']['blockers'], ['statutes', 'evidence'])
        self.assertEqual(rows['/legal/coverage']['blockers'], ['statutes', 'evidence'])
        # A single synthetic extract is not complete statutory coverage.
        self.assertEqual(rows['/legal/corpus']['status'], 'blocked')
        self.assertIn('statutes', rows['/legal/corpus']['blockers'])
        self.assertTrue(rows['/diff']['can_attempt'])

    def test_values_and_credential_urls_are_never_serialized(self):
        with patch.dict('os.environ', {**MODEL, 'TAVILY_API_KEY': 'PRIVATE-SEARCH',
                                      'LLM_BASE_URL': 'https://PRIVATE-USER:PRIVATE-PASSWORD@example.org/v1'}, clear=True):
            report = inspect_runtime()
        self.assertNotIn('PRIVATE-', json.dumps(report))
        self.assertFalse(report['provider_configuration']['llm'])
        self.assertFalse(any(row['legal_acceptance'] for row in report['workflows']))

    def test_readiness_is_a_protected_get_in_schema(self):
        for require_auth in [True, False]:
            operation = openapi_spec(require_auth=require_auth)['paths']['/runtime/readiness']['get']
            self.assertEqual(bool(operation['security']), require_auth)

    def test_http_auth_no_store_and_no_persisted_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server('127.0.0.1', 0, directory, 'readiness-token')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for token, expected in [('', 401), ('wrong-token', 401), ('readiness-token', 200)]:
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    try:
                        with patch.dict('os.environ', MODEL, clear=True):
                            connection.request('GET', '/runtime/readiness', headers={'Authorization': 'Bearer ' + token})
                            response = connection.getresponse()
                            payload = json.load(response)
                        self.assertEqual(response.status, expected)
                        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                        if expected == 200:
                            self.assertEqual(workflows(payload)['claim-audit']['blockers'], ['search'])
                            self.assertNotIn('PRIVATE-', json.dumps(payload))
                    finally:
                        connection.close()
                self.assertEqual(list(Path(directory).iterdir()), [])
                self.assertFalse(server.jobs.active)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
