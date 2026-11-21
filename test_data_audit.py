import copy
import json
import socket
import unittest
from unittest.mock import patch

from data_audit import ROOT, audit_data, audit_repository
from statute_corpus import corpus_report


class DataAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads((ROOT / 'fixtures/legal_coverage.json').read_text())
        cls.workflows = json.loads((ROOT / 'fixtures/legal_workflows.json').read_text())
        cls.benchmarks = json.loads((ROOT / 'fixtures/legal_benchmarks.json').read_text())
        cls.corpus = corpus_report(include_text=True)
        # The real legal corpus is intentionally empty after quarantine. Build
        # synthetic extracts for mutation tests without restoring source texts.
        cls.synthetic_corpus = copy.deepcopy(cls.corpus)
        cls.synthetic_corpus['entries'] = [
            {'card_id': card['id'], 'captured_on': '2026-11-01',
             'period_supported': False, 'text': f'Synthetic audit fixture {index}'}
            for index, card in enumerate(cls.registry['cards'][:3])
        ]

    def report(self, registry=None, corpus=None):
        return audit_data(registry or self.registry, self.workflows, self.benchmarks,
                          corpus or self.corpus, checked_on='2026-11-08')

    def test_actual_repository_has_no_structural_errors_but_explicit_evidence_gaps(self):
        with patch.object(socket, 'create_connection', side_effect=AssertionError('network')):
            report = audit_repository(checked_on='2026-11-08')
        self.assertEqual(report['counts'], {'error': 0, 'warning': 0, 'gap': 938})
        self.assertFalse(report['legal_acceptance'])
        self.assertEqual(report['coverage']['cards'], 454)

    def test_duplicate_modules_sources_and_bad_intervals_are_detected(self):
        for field in ('modules', 'sources'):
            registry = copy.deepcopy(self.registry)
            registry[field].append(copy.deepcopy(registry[field][0]))
            self.assertTrue(any(row['code'] == 'duplicate_' + field for row in self.report(registry)['findings']))
        registry = copy.deepcopy(self.registry)
        registry['cards'][0].update(effective_from='2026-11-08', effective_to='2025-01-01')
        self.assertTrue(any(row['code'] == 'reversed_effective_interval' for row in self.report(registry)['findings']))

    def test_missing_extract_future_capture_and_duplicate_texts_are_distinct(self):
        corpus = copy.deepcopy(self.synthetic_corpus)
        corpus['entries'].pop()
        corpus['entries'][0]['captured_on'] = '2027-01-01'
        corpus['entries'][1]['text'] = corpus['entries'][0]['text']
        codes = {row['code'] for row in self.report(corpus=corpus)['findings']}
        self.assertTrue({'missing_corpus_extract', 'capture_after_audit_date', 'duplicate_normalized_extract'} <= codes)

    def test_audit_reports_never_include_source_text(self):
        report = self.report(corpus=self.synthetic_corpus)
        self.assertNotIn(self.synthetic_corpus['entries'][0]['text'], json.dumps(report, ensure_ascii=False))
