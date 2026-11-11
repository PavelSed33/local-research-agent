"""A broken optional registry must not take down unrelated local workflows."""
import http.client
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from api import create_server
from legal_skills import SkillRegistryError


class SkillServiceHealthTests(unittest.TestCase):
    def test_malformed_inputs_across_public_workflows_return_400(self):
        cases = [('/ask', {'question': [], 'demo': True}),
                 ('/review', {'document': [], 'demo': True}),
                 ('/review/legal', {'document': [], 'demo': True}),
                 ('/extract', {'document': [], 'demo': True}),
                 ('/extract/local', {'document': []}),
                 ('/diff', {'document_a': [], 'document_b': 'test'}),
                 ('/case-law/check', {'question': [], 'demo': True}),
                 ('/privacy/check', {'scenario': []}),
                 ('/legal/research', {'scenario': []}),
                 ('/legal/corpus', {'card_ids': {}}),
                 ('/legal/coverage', {'module_ids': {}}),
                 ('/legal/skills/run', {'skill_id': [], 'document': 'synthetic document', 'demo': True})]
        with tempfile.TemporaryDirectory() as directory:
            server = create_server('127.0.0.1', 0, directory, 'test-token', persist_results=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for path, payload in cases:
                    with self.subTest(path=path):
                        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
                        try:
                            connection.request('POST', path, json.dumps(payload),
                                               {'Authorization': 'Bearer test-token', 'Content-Type': 'application/json'})
                            response = connection.getresponse()
                            response.read()
                            self.assertEqual(response.status, 400)
                        finally:
                            connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_broken_registry_is_503_while_core_endpoints_remain_available(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server('127.0.0.1', 0, directory, 'test-token', persist_results=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch('api.skill_options', side_effect=SkillRegistryError('PRIVATE-PATH')), patch('api.load_skills', side_effect=SkillRegistryError('PRIVATE-PATH')):
                    for path, expected in [('/legal/skills/options', 503), ('/health', 200), ('/openapi.json', 200)]:
                        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                        try:
                            connection.request('GET', path, headers={'Authorization': 'Bearer test-token'})
                            response = connection.getresponse()
                            body = response.read().decode()
                            self.assertEqual(response.status, expected)
                            self.assertNotIn('PRIVATE-PATH', body)
                            self.assertIsInstance(json.loads(body), dict)
                        finally:
                            connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
