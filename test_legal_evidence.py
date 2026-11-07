"""Corruption, provenance and runtime checks; not legal acceptance cases."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent import Source
from legal_coverage import coverage_report
from legal_evidence import DEFAULT_CORPUS, cached_contract_sources, evidence_report
from legal_review import handle_legal_review
from privacy_review import handle_privacy_check


class EvidenceCorpusTests(unittest.TestCase):
    def test_real_captures_have_exact_spans_but_no_original_or_period_claim(self):
        report = evidence_report(as_of='2026-11-07')
        self.assertEqual(report['errors'], [])
        self.assertEqual(len(report['entries']), 2)
        for entry in report['entries']:
            self.assertTrue(entry['integrity_checked'])
            self.assertFalse(entry['raw_http_response'])
            self.assertFalse(entry['original_publication'])
            self.assertFalse(entry['period_supported'])
            self.assertFalse(entry['verified'])
            for quote in entry['quotes']:
                self.assertEqual(entry['text'][quote['start']:quote['end']], quote['quote'])
                self.assertTrue(quote['quote_checked'])
                self.assertFalse(quote['verified'])

    def test_quotes_do_not_complete_statute_cards(self):
        result = coverage_report(['LAW-01', 'LAW-02'], '2026-11-07')
        self.assertEqual(result['evidence_summary']['checked_quote_count'], 3)
        for module in result['modules']:
            self.assertEqual(module['accepted_provision_count'], 0)
            for card in module['cards']:
                self.assertFalse(card['quote_checked'])
                self.assertFalse(card['period_supported'])
                self.assertIsNone(card['effective_from'])
        card = next(c for c in result['modules'][1]['cards'] if c['provision'] == '310')
        self.assertEqual(card['supporting_evidence'][0]['kind'], 'supreme_court_plenum')

    def test_later_capture_is_not_used_as_historical_model_evidence(self):
        self.assertEqual(cached_contract_sources('2026-11-06'), [])
        self.assertTrue(cached_contract_sources('2026-11-07'))
        report = evidence_report(as_of='2018-01-01')
        self.assertTrue(all(e['temporal_status'] == 'later_capture_unusable_for_historical_review' for e in report['entries']))

    def mutate(self, change):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(DEFAULT_CORPUS, root, dirs_exist_ok=True)
            manifest = json.loads((root / 'manifest.json').read_text())
            change(root, manifest)
            (root / 'manifest.json').write_text(json.dumps(manifest))
            return evidence_report(as_of='2026-11-07', corpus_dir=root)

    def test_modified_capture_is_excluded_even_if_manifest_claims_verified(self):
        def change(root, manifest):
            entry = manifest['entries'][0]
            entry['verified'] = True
            (root / entry['file']).write_text('Текст изменён')
        result = self.mutate(change)
        self.assertTrue(result['errors'])
        self.assertEqual(len(result['entries']), 1)
        self.assertFalse(result['verified'])

    def test_forged_span_and_boolean_offset_are_excluded(self):
        for value in [0, True, -1, '0']:
            def change(root, manifest):
                manifest['entries'][0]['quotes'][0]['start'] = value
            with self.subTest(value=value):
                result = self.mutate(change)
                self.assertTrue(result['errors'])
                self.assertEqual(len(result['entries']), 1)

    def test_missing_file_and_path_traversal_fail_closed(self):
        for value in ['../README.md', '/etc/passwd', 'missing.txt']:
            with self.subTest(value=value):
                result = self.mutate(lambda root, m: m['entries'][0].update(file=value))
                self.assertTrue(result['errors'])
                self.assertEqual(len(result['entries']), 1)

    def test_symlink_outside_corpus_rejected(self):
        def change(root, manifest):
            file = root / manifest['entries'][0]['file']
            file.unlink()
            file.symlink_to(Path(__file__).resolve())
        self.assertTrue(self.mutate(change)['errors'])

    def test_url_impersonation_unknown_kind_and_raw_claim_rejected(self):
        for changes in [{'url': 'https://mintrud.gov.ru.example.org/law'},
                        {'url': 'https://mintrud.gov.ru@evil.example/law'},
                        {'url': 'http://mintrud.gov.ru/law'},
                        {'url': 'https://mintrud.gov.ru:1234/law'},
                        {'url': 'https://mintrud.gov.ru:bad/law'},
                        {'kind': 'original_publication'}, {'capture_kind': 'raw_http'}]:
            with self.subTest(changes=changes):
                self.assertTrue(self.mutate(lambda root, m: m['entries'][0].update(changes))['errors'])

    def test_flags_in_manifest_cannot_promote_extracted_copy_to_primary(self):
        result = self.mutate(lambda root, m: m['entries'][0].update(
            original_publication=True, raw_http_response=True, verified=True, edition_on='2026-11-07'))
        entry = result['entries'][0]
        self.assertFalse(entry['original_publication'])
        self.assertFalse(entry['raw_http_response'])
        self.assertFalse(entry['verified'])
        self.assertIsNone(entry['edition_on'])

    def test_invalid_or_missing_manifest_returns_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(evidence_report(corpus_dir=root)['status'], 'unavailable')
            for raw in ['[]', '{}', 'not JSON']:
                (root / 'manifest.json').write_text(raw)
                self.assertEqual(evidence_report(corpus_dir=root)['status'], 'unavailable')

    def test_privacy_returns_linked_partial_evidence_not_a_legal_approval(self):
        result = handle_privacy_check({'scenario': 'consent', 'as_of': '2026-11-07'})
        self.assertEqual(len(result['local_evidence']['entries']), 1)
        self.assertFalse(result['verified'])
        self.assertFalse(result['local_evidence']['entries'][0]['period_supported'])

    def test_search_failure_keeps_valid_local_source_and_provenance(self):
        from agent import AgentError
        with patch('legal_review.ensure_document_transfer_allowed'), patch('legal_review.TavilySearch') as search, patch('legal_review.OpenAICompatibleModel') as model:
            search.return_value.search.side_effect = AgentError('offline')
            model.return_value.complete.return_value = json.dumps({'summary': 'Черновик', 'risks': [], 'missing_clauses': []})
            result = handle_legal_review({'document': 'Договор для технического теста локальных источников.',
                                         'facts': {'both_businesses': True}, 'as_of': '2026-11-07'})
        self.assertEqual(len(result['sources']), 1)
        source = result['sources'][0]
        self.assertEqual(source['retrieval_mode'], 'local_web_extract')
        self.assertFalse(source['capture']['raw_http_response'])
        self.assertEqual(len(source['capture']['sha256']), 64)
        self.assertIn('прерывисты', model.return_value.complete.call_args.args[0])
        self.assertFalse(result['verified'])

    def test_newly_retrieved_same_url_keeps_ids_and_is_not_mislabelled_as_cached(self):
        fresh = Source('Новая выборка', 'https://www.vsrf.ru/documents/all/8524', 'Новый текст для проверки.', '')
        with patch('legal_review.ensure_document_transfer_allowed'), patch('legal_review.TavilySearch') as search, patch('legal_review.OpenAICompatibleModel') as model:
            search.return_value.search.return_value = [fresh]
            model.return_value.complete.return_value = json.dumps({'summary': 'Черновик', 'risks': [], 'missing_clauses': []})
            result = handle_legal_review({'document': 'Договор для технического теста локальных источников.',
                                         'facts': {'both_businesses': True}, 'as_of': '2026-11-07'})
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(result['sources'][0]['id'], 1)
        self.assertEqual(result['sources'][0]['retrieval_mode'], 'search_result')
        self.assertIsNone(result['sources'][0]['capture'])

    def test_model_cannot_assign_itself_legal_acceptance_or_supported_period(self):
        from test_legal_workflows import DOCUMENT, SOURCE, review_result
        output = review_result()
        output['risks'][0].update(verified=True, lawyer_reviewed=True, signed_opinion='invented')
        output['risks'][0]['legal_basis'][0].update(verified=True, lawyer_reviewed=True,
                                                  period_supported=True, effective_from='2000-01-01')
        with patch('legal_review.ensure_document_transfer_allowed'), patch('legal_review.TavilySearch') as search, patch('legal_review.OpenAICompatibleModel') as model:
            search.return_value.search.return_value = [SOURCE]
            model.return_value.complete.return_value = json.dumps(output)
            result = handle_legal_review({'document': DOCUMENT, 'facts': {'both_businesses': True}})
        risk = result['risks'][0]
        self.assertFalse(risk['verified'])
        self.assertFalse(risk['lawyer_reviewed'])
        self.assertNotIn('signed_opinion', risk)
        basis = risk['legal_basis'][0]
        self.assertFalse(basis['verified'])
        self.assertFalse(basis['lawyer_reviewed'])
        self.assertFalse(basis['period_supported'])
        self.assertNotIn('effective_from', basis)
