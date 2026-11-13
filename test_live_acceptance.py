"""Acceptance orchestration tests; mocked providers are not live provider acceptance."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from scripts.acceptance import _finish, _worker, run_acceptance, initialize_environment


class LocalAcceptanceTests(unittest.TestCase):
    def test_environment_initialization_never_overwrites_existing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env.example').write_text('LLM_API_KEY=placeholder\n')
            self.assertTrue(initialize_environment(root))
            if os.name == 'posix':
                self.assertEqual((root / '.env').stat().st_mode & 0o777, 0o600)
            (root / '.env').write_text('LLM_API_KEY=PRIVATE-EXISTING\n')
            self.assertFalse(initialize_environment(root))
            self.assertEqual((root / '.env').read_text(), 'LLM_API_KEY=PRIVATE-EXISTING\n')

    def test_demo_in_isolated_process_runs_all_http_workflows(self):
        with patch.dict(os.environ, {}, clear=True):
            report = run_acceptance(timeout_seconds=30)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['checks'][0]['passed_http_checks'], 6)  # six routes checked by scripts.smoke
        self.assertFalse(report['external_requests_attempted'])
        self.assertFalse(report['live_acceptance_passed'])
        self.assertFalse(report['legal_acceptance'])

    def test_missing_keys_are_blocked_not_counted_as_live_success(self):
        with patch.dict(os.environ, {}, clear=True):
            report = run_acceptance(live=True, timeout_seconds=30)
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(set(report['configuration']['missing']), {'LLM_API_KEY', 'LLM_MODEL', 'TAVILY_API_KEY'})
        self.assertFalse(report['external_requests_attempted'])
        self.assertFalse(report['live_acceptance_passed'])

    def test_empty_or_partial_report_cannot_pass(self):
        for checks in ([], [{'id': 'live_model_connection', 'status': 'passed'}]):
            self.assertEqual(_finish({'mode': 'live', 'checks': checks})['status'], 'failed')

    def test_timeout_or_missing_child_report_fails_closed(self):
        with patch('scripts.acceptance._run_isolated', side_effect=subprocess.TimeoutExpired('synthetic', 10)):
            report = run_acceptance(live=True, timeout_seconds=10)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['checks'][-1]['reason'], 'deadline_exceeded')
        with patch('scripts.acceptance._run_isolated') as run:
            run.return_value.returncode = 0
            self.assertEqual(run_acceptance()['status'], 'failed')

    def test_invalid_deadlines_rejected(self):
        for deadline in (0, 1801, True, '600'):
            with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                run_acceptance(timeout_seconds=deadline)

    def test_provider_errors_do_not_expose_secret_values_or_responses(self):
        values = {'LLM_API_KEY': 'PRIVATE-MODEL-KEY', 'LLM_MODEL': 'PRIVATE-MODEL-NAME',
                  'TAVILY_API_KEY': 'PRIVATE-SEARCH-KEY'}
        error = urllib.error.HTTPError('https://example.org/PRIVATE-URL', 401, 'PRIVATE-ERROR', {}, io.BytesIO(b'PRIVATE-BODY'))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, values, clear=True), \
                patch('scripts.acceptance.smoke', return_value=['ok'] * 18), \
                patch('scripts.acceptance.OpenAICompatibleModel') as model, patch('scripts.acceptance.TavilySearch') as search:
            model.return_value.complete.side_effect = RuntimeError('PRIVATE-DETAILS')
            search.return_value.search.side_effect = error
            path = Path(directory) / 'report.json'
            _worker(path, True)
            report = json.loads(path.read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertNotIn('PRIVATE-', json.dumps(report))
        self.assertTrue(any(c.get('http_status') == 401 for c in report['checks']))

    def test_document_checks_remain_blocked_without_transfer_configuration(self):
        def response(request, **kwargs):
            self.assertNotIn('/review', request.full_url)
            self.assertNotIn('/extract', request.full_url)
            payload = json.loads(request.data)
            self.assertIs(payload['demo'], False)
            data = {'verified': False, 'decisions': [], 'answer': 'Технический ответ', 'sources': []}
            return io.BytesIO(json.dumps(data).encode())

        values = {'LLM_API_KEY': 'TEST', 'LLM_MODEL': 'TEST', 'TAVILY_API_KEY': 'TEST'}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, values, clear=True), \
                patch('scripts.acceptance.smoke', return_value=['ok'] * 18), \
                patch('scripts.acceptance.OpenAICompatibleModel') as model, patch('scripts.acceptance.TavilySearch') as search, \
                patch('scripts.acceptance.urllib.request.urlopen', side_effect=response):
            model.return_value.complete.return_value = '{"ok":true}'
            search.return_value.search.return_value = [object()]
            path = Path(directory) / 'report.json'
            _worker(path, True)
            report = json.loads(path.read_text())
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(len([c for c in report['checks'] if c.get('reason') == 'document_transfer_not_permitted']), 3)
        self.assertFalse(report['live_acceptance_passed'])

    def test_complete_mocked_checks_require_all_endpoints_and_never_demo_in_live_requests(self):
        routes = []
        def response(request, **kwargs):
            route = request.full_url.split(':', 2)[-1].split('/', 1)[-1]
            routes.append('/' + route)
            payload = json.loads(request.data)
            self.assertIs(payload['demo'], False)
            if 'document' in payload:
                from scripts.acceptance import DOCUMENT
                self.assertEqual(payload['document'], DOCUMENT)
            return io.BytesIO(json.dumps({'verified': False, 'status': 'draft', 'risks': [],
                'amounts': [{'quote': '1 000 рублей'}], 'decisions': [], 'answer': 'Технический ответ', 'sources': []}).encode())
        values = {'LLM_API_KEY': 'TEST', 'LLM_MODEL': 'TEST', 'TAVILY_API_KEY': 'TEST', 'ALLOW_CLOUD_DOCUMENTS': 'true'}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, values, clear=True), \
                patch('scripts.acceptance.smoke', return_value=['ok'] * 18), \
                patch('scripts.acceptance.OpenAICompatibleModel') as model, patch('scripts.acceptance.TavilySearch') as search, \
                patch('scripts.acceptance.urllib.request.urlopen', side_effect=response):
            model.return_value.complete.return_value = '{"ok":true}'
            search.return_value.search.return_value = [object()]
            path = Path(directory) / 'report.json'
            _worker(path, True)
            report = json.loads(path.read_text())
        self.assertEqual(set(routes), {'/ask', '/review', '/extract', '/review/legal', '/case-law/check'})
        self.assertTrue(report['live_acceptance_passed'])
        self.assertFalse(report['legal_acceptance'])

    def test_placeholder_settings_do_not_trigger_external_calls(self):
        from preflight import inspect_runtime
        with patch.dict(os.environ, {'LLM_API_KEY': 'замените_на_ключ_модели',
            'LLM_MODEL': 'замените_на_название_модели', 'TAVILY_API_KEY': 'замените_на_ключ_tavily'}, clear=True):
            self.assertEqual(len(inspect_runtime()['missing_configuration']), 3)

    def test_failed_local_baseline_blocks_provider_calls(self):
        values = {'LLM_API_KEY': 'TEST', 'LLM_MODEL': 'TEST', 'TAVILY_API_KEY': 'TEST'}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, values, clear=True), \
                patch('scripts.acceptance.smoke', side_effect=ValueError('synthetic local failure')), \
                patch('scripts.acceptance.OpenAICompatibleModel') as model, patch('scripts.acceptance.TavilySearch') as search:
            path = Path(directory) / 'report.json'
            _worker(path, True)
            report = json.loads(path.read_text())
            model.assert_not_called()
            search.assert_not_called()
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['external_requests_attempted'])

