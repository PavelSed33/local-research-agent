"""Workflow separation, hostile input and HTTP integration; not legal benchmarks."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from api import create_server, openapi_spec
from legal_catalog import review_checklist
from legal_research import handle_legal_research, research_options


class ResearchWorkflowTests(unittest.TestCase):
    def test_every_workflow_is_local_and_all_yes_still_requires_legal_review(self):
        options = research_options()['scenarios']
        self.assertEqual(len(options), 35)
        with patch.object(socket, 'create_connection', side_effect=AssertionError('Unexpected network')):
            for name, option in options.items():
                with self.subTest(scenario=name):
                    missing = handle_legal_research({'scenario': name, 'as_of': '2026-11-08'})
                    self.assertEqual(missing['status'], 'needs_facts')
                    full = handle_legal_research({'scenario': name, 'as_of': '2026-11-08',
                                                 'facts': dict.fromkeys(option['questions'], True)})
                    self.assertEqual(full['status'], 'needs_legal_review')
                    self.assertFalse(full['verified'])
                    self.assertFalse(full['lawyer_reviewed'])
                    requested = {c['id'] for m in full['legal_coverage']['modules'] for c in m['cards']}
                    self.assertEqual(requested, set(full['research_card_ids']))
                    self.assertTrue(all(m['accepted_provision_count'] == 0 for m in full['legal_coverage']['modules']))

    def test_no_does_not_automatically_establish_violation(self):
        result = handle_legal_research({'scenario': 'civil-limitation',
                                       'facts': {'claim_classified': False}, 'as_of': '2026-11-08'})
        self.assertEqual(result['unresolved_items'][0]['key'], 'claim_classified')
        self.assertEqual(result['status'], 'needs_facts')
        self.assertNotIn('deadline', result)
        self.assertNotIn('fine', result)

    def test_scenarios_and_documents_cannot_be_injected_via_facts(self):
        for payload in [{'scenario': []}, {'scenario': 'unknown'},
                        {'scenario': 'civil-limitation', 'facts': {'claim_classified': 'yes'}},
                        {'scenario': 'civil-limitation', 'facts': {'document': 'sensitive'}},
                        {'scenario': 'civil-limitation', 'document': 'sensitive'},
                        {'scenario': 'civil-limitation', 'demo': 'false'},
                        {'scenario': 'civil-limitation', 'as_of': '2026-02-30'}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                handle_legal_research(payload)

    def test_special_profiles_remain_separate_in_contract_checklists(self):
        business = review_checklist('business', 'services', '2026-11-08')['research_workflows']
        consumer = review_checklist('consumer', 'services', '2026-11-08')['research_workflows']
        employment = review_checklist('employment', 'general', '2026-11-08')['research_workflows']
        self.assertFalse(any(w['id'].startswith(('consumer-', 'employment-', 'work-', 'lease-')) for w in business))
        self.assertTrue(any(w['id'] == 'consumer-work-services' for w in consumer))
        self.assertTrue(all(w['id'].startswith('employment-') for w in employment))
        self.assertEqual(review_checklist('business', 'services', '2026-11-07')['research_workflows'], [])

    def test_http_routes_auth_schema_and_no_persisted_user_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server('127.0.0.1', 0, directory, 'research-token')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_address[1]}'
            try:
                with urllib.request.urlopen(base + '/legal/research/options') as response:
                    self.assertEqual(len(json.load(response)['scenarios']), 35)
                for route, payload in [('/legal/research', {'scenario': 'civil-notices'}),
                                       ('/legal/corpus', {'card_ids': ['LAW-02:165.1'], 'include_text': True})]:
                    req = urllib.request.Request(base + route, data=json.dumps(payload).encode(),
                                                 headers={'Content-Type': 'application/json'})
                    with self.assertRaises(urllib.error.HTTPError) as denied:
                        urllib.request.urlopen(req)
                    self.assertEqual(denied.exception.code, 401)
                    req.add_header('Authorization', 'Bearer research-token')
                    with urllib.request.urlopen(req) as response:
                        self.assertFalse(json.load(response)['verified'])
                    self.assertIn(route, openapi_spec()['paths'])
                self.assertEqual(list(Path(directory).iterdir()), [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
