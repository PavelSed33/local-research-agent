import json
import unittest
from unittest.mock import patch

from preflight import inspect_runtime


class PreflightTests(unittest.TestCase):
    def test_missing_settings_are_reported_without_network(self):
        with patch.dict('os.environ', {}, clear=True), patch('urllib.request.OpenerDirector.open') as network:
            result = inspect_runtime()
        self.assertFalse(result['research_configuration_present'])
        self.assertEqual(len(result['missing_configuration']), 3)
        self.assertFalse(result['external_services_tested'])
        network.assert_not_called()

    def test_secret_values_never_appear_in_report(self):
        with patch.dict('os.environ', {'LLM_API_KEY': 'PRIVATE-KEY', 'LLM_MODEL': 'PRIVATE-MODEL',
                                       'TAVILY_API_KEY': 'PRIVATE-SEARCH', 'ALLOW_CLOUD_DOCUMENTS': 'true'}, clear=True):
            result = inspect_runtime()
        self.assertTrue(result['research_configuration_present'])
        self.assertFalse(result['legal_acceptance'])
        self.assertNotIn('PRIVATE-', json.dumps(result))

    def test_external_plain_http_is_not_ready(self):
        with patch.dict('os.environ', {'LLM_BASE_URL': 'http://example.org/v1'}, clear=True):
            result = inspect_runtime()
        self.assertIn('LLM_BASE_URL', result['invalid_endpoint_configuration'])
