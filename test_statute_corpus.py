"""Regression checks for the HTTP catalog and quarantined statutory data."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.import_statutes import import_corpus
from statute_corpus import ROOT, corpus_report, search_statutory_sources
from test_api import ApiTestCase


class StatuteCatalogTests(ApiTestCase):
    api_token = 'catalog-test-token'

    def test_catalog_excludes_quarantined_excerpts_and_legacy_placeholders(self):
        report = corpus_report(include_text=True)
        self.assertEqual(report['entries'], [])
        self.assertFalse(report['errors'])
        self.assertEqual(search_statutory_sources('статья 432 ГК РФ о договоре', None), [])
        status, catalog = self.request('GET', '/statutes/list', token=self.api_token)
        self.assertEqual(status, 200)
        self.assertEqual(catalog['statutes'], [])
        self.assertEqual(catalog['status'], 'no_usable_texts')
        status, results = self.request('POST', '/statutes/search', {'query': 'текст документа'}, self.api_token)
        self.assertEqual(status, 200)
        self.assertEqual(results['results'], [])
        status, result = self.request('POST', '/statutes/get', {
            'statute_id': 'gk_part1', 'article_number': '432'}, self.api_token)
        self.assertEqual(status, 404)
        self.assertFalse(result['verified'])

    def test_catalog_routes_validate_authorization_and_payload(self):
        status, _ = self.request('GET', '/statutes/list')
        self.assertEqual(status, 401)
        status, _ = self.request('POST', '/statutes/search', {'query': ' '}, self.api_token)
        self.assertEqual(status, 400)
        status, _ = self.request('POST', '/statutes/search', {'query': 'договор', 'limit': True}, self.api_token)
        self.assertEqual(status, 400)
        status, _ = self.request('POST', '/statutes/get', {'statute_id': 'gk1'}, self.api_token)
        self.assertEqual(status, 400)

    def test_catalog_returns_source_and_review_status_for_an_admitted_extract(self):
        extract = {
            'card_id': 'synthetic:1', 'act_id': 'synthetic', 'provision': '1',
            'heading': 'Договор', 'text': 'Синтетический текст про договор.',
            'url': 'https://example.org/synthetic', 'temporal_status': 'incomplete_history',
            'verified': False,
        }
        report = {'status': 'partial_statutory_texts', 'entries': [extract],
                  'errors': [], 'as_of': '2026-01-01'}
        with patch('statute_corpus.corpus_report', return_value=report):
            status, result = self.request('POST', '/statutes/search', {
                'query': 'ДОГОВОР', 'statute_ids': ['synthetic'], 'limit': 1}, self.api_token)
            self.assertEqual(status, 200)
            self.assertEqual(result['results'][0]['url'], extract['url'])
            self.assertFalse(result['results'][0]['verified'])
            status, result = self.request('POST', '/statutes/get', {
                'statute_id': 'synthetic', 'article_number': '1'}, self.api_token)
            self.assertEqual(status, 200)
            self.assertEqual(result['text'], extract['text'])

    def test_catalog_reports_integrity_failure(self):
        report = {'status': 'unavailable', 'entries': [], 'errors': ['missing'], 'as_of': '2026-01-01'}
        with patch('statute_corpus.corpus_report', return_value=report):
            status, result = self.request('GET', '/statutes/list', token=self.api_token)
        self.assertEqual(status, 503)
        self.assertEqual(result['status'], 'unavailable')


class QuarantineIntegrityTests(unittest.TestCase):
    def test_unlisted_missing_card_and_inconsistent_manifest_are_errors(self):
        manifest = json.loads((ROOT / 'manifest.json').read_text())
        gap = manifest['gaps'][0]
        omitted = gap['card_ids'].pop()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'manifest.json'
            path.write_text(json.dumps(manifest))
            self.assertEqual(corpus_report(corpus_dir=root)['status'], 'unavailable')
            gap['removed_article_extracts'] -= 1
            path.write_text(json.dumps(manifest))
            result = corpus_report(corpus_dir=root)
            self.assertTrue(result['errors'])
            self.assertNotIn(omitted, result['quarantined_card_ids'])
            self.assertIn(omitted, result['missing_card_ids'])

    def test_quarantine_cannot_mask_missing_group_or_be_overwritten_by_import(self):
        raw = (ROOT / 'manifest.json').read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'manifest.json'
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'карантин'):
                import_corpus(root / 'staging', root)
            self.assertEqual(path.read_bytes(), raw)
            manifest = json.loads(raw)
            manifest['groups'] = [{'file': 'missing.json', 'sha256': 'a' * 64}]
            path.write_text(json.dumps(manifest))
            self.assertTrue(corpus_report(corpus_dir=root)['errors'])
