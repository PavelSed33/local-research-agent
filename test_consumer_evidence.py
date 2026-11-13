"""Source integrity/scope tests, not lawyer acceptance or current-law assertions."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent import AgentError, Source
from legal_evidence import DEFAULT_CORPUS, cached_contract_sources, evidence_report
from legal_coverage import coverage_report
from legal_review import handle_legal_review

URL = 'https://zpp.rospotrebnadzor.ru/npa/federal/389825'


class ConsumerTransitionEvidenceTests(unittest.TestCase):
    def test_saved_transition_quotes_have_exact_spans_and_no_edition_promotion(self):
        report = evidence_report(['LAW-04:16'], '2026-11-08')
        self.assertEqual(report['errors'], [])
        self.assertEqual(len(report['entries']), 1)
        entry = report['entries'][0]
        self.assertEqual(entry['kind'], 'government_amending_act_copy')
        self.assertEqual(entry['document_signed_on'], '2022-05-01')
        self.assertIsNone(entry['edition_on'])
        self.assertFalse(entry['original_publication'])
        self.assertFalse(entry['period_supported'])
        self.assertEqual(len(entry['quotes']), 2)
        for quote in entry['quotes']:
            self.assertEqual(entry['text'][quote['start']:quote['end']], quote['quote'])
            self.assertTrue(quote['quote_checked'])
        self.assertIn('перечень недопустимых условий', entry['quotes'][1]['quote'])

    def test_old_contract_transition_is_visible_without_accepting_article_16(self):
        result = coverage_report(['LAW-04'], '2026-11-08')
        module = result['modules'][0]
        self.assertEqual(module['accepted_provision_count'], 0)
        card = next(c for c in module['cards'] if c['id'] == 'LAW-04:16')
        self.assertEqual(card['id'], 'LAW-04:16')
        self.assertEqual(len(card['supporting_evidence']), 2)
        self.assertIsNone(card['effective_from'])
        self.assertFalse(card['quote_checked'])
        self.assertFalse(card['verified'])
        transition = card['transition_rules'][0]
        self.assertEqual(transition['candidate_effective_from'], '2022-09-01')
        self.assertFalse(transition['verified'])
        self.assertIn('перечень', transition['scope'].lower())

    def test_capture_does_not_backfill_historical_source_or_other_profiles(self):
        for day, profile in [('2026-11-07', 'consumer'), ('2022-09-01', 'consumer'),
                             ('2026-11-08', 'business'), ('2026-11-08', 'employment')]:
            self.assertNotIn(URL, {source.url for source in cached_contract_sources(day, profile)})
        self.assertIn(URL, {s.url for s in cached_contract_sources('2026-11-08', 'consumer')})

    def test_consumer_review_can_use_saved_transition_after_search_failure(self):
        with patch('legal_review.ensure_document_transfer_allowed'), patch('legal_review.TavilySearch') as search, patch('legal_review.OpenAICompatibleModel') as model:
            search.return_value.search.side_effect = AgentError('offline')
            model.return_value.complete.return_value = json.dumps({'summary': 'Черновик', 'risks': [], 'missing_clauses': []})
            result = handle_legal_review({'document': 'Условный потребительский договор для проверки источников.',
                'profile': 'consumer', 'as_of': '2026-11-08',
                'facts': {'customer_is_individual': True, 'personal_use': True, 'seller_is_business': True}})
        source = next(s for s in result['sources'] if s['url'] == URL)
        self.assertEqual(source['retrieval_mode'], 'local_web_extract')
        self.assertEqual(source['authority_class'], 'government_legal_reproduction')
        self.assertFalse(source['period_supported'])
        self.assertFalse(result['verified'])
        self.assertIn('переходных положений не заменяет', model.return_value.complete.call_args.args[0])

    def test_source_budget_keeps_transition_and_statute_with_fresh_search(self):
        fresh = [Source('Synthetic source', f'https://example.org/{i}', 'Synthetic text', '')
                 for i in range(5)]
        with patch('legal_review.ensure_document_transfer_allowed'), patch('legal_review.TavilySearch') as search, patch('legal_review.OpenAICompatibleModel') as model:
            search.return_value.search.return_value = fresh
            model.return_value.complete.return_value = json.dumps({'summary': 'Черновик', 'risks': [], 'missing_clauses': []})
            result = handle_legal_review({'document': 'Условный потребительский договор для проверки лимита.',
                'profile': 'consumer', 'as_of': '2026-11-08',
                'facts': {'customer_is_individual': True, 'personal_use': True, 'seller_is_business': True}})
        self.assertLessEqual(len(result['sources']), 5)
        self.assertEqual([s['url'] for s in result['sources'][:2]], [s.url for s in fresh[:2]])
        self.assertIn(URL, {s['url'] for s in result['sources']})
        self.assertTrue(any(s['retrieval_mode'] == 'local_statutory_extract' for s in result['sources']))
        self.assertFalse(result['verified'])

    def test_review_prompt_keeps_contract_out_of_system_in_real_mode(self):
        private = 'PRIVATE-CONTRACT-TEXT-DO-NOT-PROMOTE'
        with patch('legal_review.ensure_document_transfer_allowed'), \
             patch('legal_review.TavilySearch') as search, \
             patch('legal_review.OpenAICompatibleModel') as model:
            search.return_value.search.side_effect = AgentError('offline')
            model.return_value.complete.return_value = json.dumps({
                'summary': 'Черновик', 'risks': [], 'missing_clauses': []})
            result = handle_legal_review({
                'document': 'Условный потребительский договор. ' + private,
                'profile': 'consumer', 'as_of': '2026-11-08',
                'facts': {'customer_is_individual': True,
                          'personal_use': True, 'seller_is_business': True}})
        system, user = model.return_value.complete.call_args.args[:2]
        self.assertNotIn(private, system)
        self.assertIn(private, user)
        self.assertFalse(result['verified'])

    def test_news_and_lookalike_host_cannot_pose_as_saved_amending_act(self):
        for url in ['https://zpp.rospotrebnadzor.ru/news/389825',
                    'https://zpp.rospotrebnadzor.ru.evil.example/npa/federal/389825']:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copytree(DEFAULT_CORPUS, root, dirs_exist_ok=True)
                manifest = json.loads((root/'manifest.json').read_text())
                next(e for e in manifest['entries'] if e['id'] == 'rpn-135-art2')['url'] = url
                (root/'manifest.json').write_text(json.dumps(manifest))
                result = evidence_report(['LAW-04:16'], '2026-11-08', corpus_dir=root)
                self.assertTrue(result['errors'])
                self.assertEqual(result['entries'], [])

    def test_failed_primary_fetches_cannot_be_counted_as_corpus_originals(self):
        attempts = json.loads((DEFAULT_CORPUS/'retrieval_attempts_2026-11-08.json').read_text())
        self.assertEqual(attempts['original_texts_retrieved'], 0)
        self.assertEqual({a['status'] for a in attempts['attempts']}, {'retrieval_failed'})
        self.assertEqual(evidence_report()['original_publication_count'], 0)
