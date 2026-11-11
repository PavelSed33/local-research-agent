"""Technical safeguards only; these cases are not lawyer-approved benchmarks."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

from api import create_server
from agent import Source
from legal_catalog import review_checklist
from legal_coverage import coverage_report
from legal_review import handle_legal_review, validate_legal_bases
from privacy_review import handle_privacy_check, privacy_options
from test_legal_workflows import DOCUMENT, review_result


class P0CoverageTests(unittest.TestCase):
    def test_scope_and_unaccepted_cards_match_spec(self):
        result = coverage_report(as_of='2026-11-07')
        self.assertEqual(len(result['modules']), 9)
        self.assertEqual([m['research_card_count'] for m in result['modules'][:2]], [21, 111])
        for module in result['modules']:
            self.assertEqual(module['accepted_provision_count'], 0)
            self.assertFalse(module['verified'])
            for card in module['cards']:
                self.assertIsNone(card['effective_from'])
                self.assertIsNone(card['quote'])
                self.assertFalse(card['quote_checked'])
                self.assertFalse(card['period_supported'])
                self.assertTrue(card['gaps'])

    def test_unretrieved_and_partially_observed_court_texts_remain_distinct(self):
        result = coverage_report(['LAW-02'], '2026-11-07')
        sources = {s['id']: s for s in result['sources']}
        self.assertFalse(sources['vsrf-7']['text_observed'])
        self.assertTrue(sources['vsrf-49']['text_observed'])
        self.assertIn('point 47 only', sources['vsrf-49']['observed_scope'])
        self.assertTrue(sources['vsrf-54']['text_observed'])
        self.assertIsNone(sources['vsrf-54']['sha256'])

    def test_invalid_module_and_date_rejected(self):
        for modules, date in [('LAW-01', None), (['LAW-99'], None), ([True], None), ([], '2026-02-30')]:
            with self.subTest(modules=modules, date=date), self.assertRaises(ValueError):
                coverage_report(modules, date)

    def test_registry_sources_and_cards_are_referentially_consistent(self):
        data = json.loads((Path(__file__).parent / 'fixtures/legal_coverage.json').read_text())
        sources = {s['id'] for s in data['sources']}
        self.assertEqual(len({c['id'] for c in data['cards']}), len(data['cards']))
        for card in data['cards']:
            self.assertTrue(set(card['source_ids']) <= sources)
            self.assertFalse(card['legal_review']['approved'])

    def test_review_reports_profile_specific_gaps_without_claiming_coverage(self):
        result = handle_legal_review({'document': DOCUMENT, 'profile': 'employment', 'demo': True})
        self.assertEqual([m['id'] for m in result['legal_coverage']['modules']], ['LAW-05'])
        self.assertFalse(result['legal_coverage']['verified'])

    def test_2016_court_questions_not_inserted_before_decision(self):
        before = review_checklist('business', 'general', '2016-11-21')
        after = review_checklist('business', 'general', '2016-11-22')
        self.assertNotIn('civil-refusal-payment', {r['id'] for r in before['rules']})
        self.assertIn('civil-refusal-payment', {r['id'] for r in after['rules']})
        self.assertFalse(after['verified'])


class PrivacyFactTests(unittest.TestCase):
    def test_every_scenario_stays_local_and_unverified_even_with_all_yes(self):
        with patch.object(socket, 'create_connection', side_effect=AssertionError('Unexpected network')):
            for key, options in privacy_options()['scenarios'].items():
                with self.subTest(scenario=key):
                    missing = handle_privacy_check({'scenario': key})
                    self.assertEqual(missing['status'], 'needs_facts')
                    result = handle_privacy_check({'scenario': key, 'facts': dict.fromkeys(options['questions'], True)})
                    self.assertEqual(result['status'], 'needs_legal_review')
                    self.assertFalse(result['verified'])
                    self.assertFalse(result['lawyer_reviewed'])
                    self.assertEqual({c['provision'] for c in result['research_cards']}, set(options['provisions']))

    def test_no_is_unresolved_not_automatic_violation(self):
        result = handle_privacy_check({'scenario': 'consent', 'facts': {'consent_evidence': False}})
        self.assertEqual(result['status'], 'needs_facts')
        self.assertEqual(result['unresolved_items'][0]['key'], 'consent_evidence')
        self.assertFalse(result['verified'])

    def test_transfer_flag_cannot_be_submitted_as_proof(self):
        for payload in [{'scenario': 'consent', 'ALLOW_CLOUD_DOCUMENTS': True},
                        {'scenario': 'consent', 'facts': {'cloud_allowed': True}},
                        {'scenario': 'consent', 'document': DOCUMENT}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                handle_privacy_check(payload)

    def test_invalid_inputs_do_not_become_user_facts(self):
        for payload in [{'scenario': 'unknown'}, {'scenario': []},
                        {'scenario': 'consent', 'facts': []},
                        {'scenario': 'consent', 'facts': {'consent_evidence': 'yes'}},
                        {'scenario': 'consent', 'as_of': '2026-02-30'}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                handle_privacy_check(payload)


class ProvisionQuoteTests(unittest.TestCase):
    def test_heading_elsewhere_cannot_identify_unrelated_quote(self):
        result = review_result()
        result['risks'][0]['legal_basis'][0]['quote'] = 'Другая условная норма об иной ситуации.'
        source = Source('Технический пример', 'https://example.org', 'Статья 1. Первая норма. Статья 2. Другая условная норма об иной ситуации.', '')
        self.assertTrue(validate_legal_bases(result, DOCUMENT, [source]))

    def test_article_one_does_not_match_article_ten_or_subarticle(self):
        for label in ('Статья 10', 'Статья 1.1'):
            result = review_result()
            quote = label + '. Условный текст нормы для тестирования.'
            result['risks'][0]['legal_basis'][0]['quote'] = quote
            source = Source('Технический пример', 'https://example.org', quote, '')
            self.assertTrue(validate_legal_bases(result, DOCUMENT, [source]))


class P0HttpTests(unittest.TestCase):
    def test_routes_auth_validation_and_no_persisted_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server('127.0.0.1', 0, directory, 'p0-token')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f'http://127.0.0.1:{server.server_address[1]}'
                with urllib.request.urlopen(base + '/privacy/options', timeout=5) as response:
                    self.assertEqual(len(json.load(response)['scenarios']), 11)
                for route, payload in [('/legal/coverage', {'module_ids': ['LAW-01']}),
                                       ('/privacy/check', {'scenario': 'consent'})]:
                    request = urllib.request.Request(base + route, data=json.dumps(payload).encode(),
                                                     headers={'Content-Type': 'application/json'})
                    with self.assertRaises(urllib.error.HTTPError) as denied:
                        urllib.request.urlopen(request, timeout=5)
                    self.assertEqual(denied.exception.code, 401)
                    request.add_header('Authorization', 'Bearer p0-token')
                    with urllib.request.urlopen(request, timeout=5) as response:
                        self.assertFalse(json.load(response)['verified'])
                request = urllib.request.Request(base + '/privacy/check', data=b'{"scenario":"bad"}',
                    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer p0-token'})
                with self.assertRaises(urllib.error.HTTPError) as invalid:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(invalid.exception.code, 400)
                self.assertFalse(list(Path(directory).rglob('*')))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

