"""Human score ledger, common-case comparisons and revocation semantics."""
import json
import unittest
from unittest.mock import patch

from reviewed_evidence import ReviewedEvidence
import test_reviewed_evidence as evidence_tests
from workspace_api import dispatch
from workspace_store import WorkspaceError, canonical


class QualityScoringTests(unittest.TestCase):
    setUp = evidence_tests.ReviewedEvidenceTests.setUp
    approve = evidence_tests.ReviewedEvidenceTests.approve

    def benchmark(self, suffix=''):
        identity = self.evidence.add(self.owner, self.matter, 'benchmark', {
            'question': 'СИНТЕТИЧЕСКИЙ вопрос ' + suffix, 'as_of': '2025-01-01',
            'expected_answer': 'СИНТЕТИЧЕСКИЙ эталон для проверки механизма.',
            'required_risks': ['Учебный риск А', 'Учебный риск Б'], 'forbidden_claims': ['Гарантия победы'],
            'citations': [{'document_id': self.document_id, 'quote': 'Условная норма.'}]})['artifact_id']
        self.approve(identity)
        return identity

    def score(self, identity, label='v1', answer='СИНТЕТИЧЕСКИЙ ответ.', correctness=3, missed=[1], unsupported=1):
        return self.evidence.score(self.reviewer, identity, answer, correctness, missed, unsupported,
                                   'СИНТЕТИЧЕСКАЯ оценка транспорта, не юридическая приёмка.', label)

    def test_revoked_then_reapproved_benchmark_does_not_resurrect_old_scores(self):
        identity = self.benchmark(); self.score(identity)
        self.assertEqual(self.evidence.metrics(self.owner, self.matter)['human_evaluations'], 1)
        self.evidence.review(self.reviewer, identity, 'revoked', 'Учебный отзыв.', self.attestations)
        self.assertEqual(self.evidence.metrics(self.owner, self.matter)['human_evaluations'], 0)
        self.approve(identity)
        result = self.evidence.metrics(self.owner, self.matter)
        self.assertEqual(result['human_evaluations'], 0)
        self.assertEqual(result['excluded_due_to_review_change'], 1)

    def test_exact_review_record_identity_survives_identical_timestamps(self):
        with patch('reviewed_evidence.now', return_value='2026-11-09T00:00:00+00:00'):
            identity = self.benchmark(); self.score(identity)
            self.evidence.review(self.reviewer, identity, 'revoked', 'Учебный отзыв.', self.attestations)
            self.approve(identity)
        result = self.evidence.metrics(self.owner, self.matter)
        self.assertEqual(result['human_evaluations'], 0)
        self.assertEqual(result['excluded_due_to_review_change'], 1)

    def test_groups_and_comparison_use_only_common_approved_benchmarks(self):
        shared, extra = self.benchmark('общий'), self.benchmark('только первая версия')
        self.score(shared, 'v1', 'Ответ версии один.', correctness=2)
        self.score(shared, 'v2', 'Ответ версии два.', correctness=4, missed=[], unsupported=0)
        self.score(extra, 'v1', 'Ответ другой задачи.', correctness=0)
        result = self.evidence.metrics(self.owner, self.matter)
        groups = {row['run_label']: row for row in result['by_run_label']}
        self.assertEqual(groups['v1']['human_evaluations'], 2)
        self.assertEqual(groups['v1']['mean_correctness_0_to_4'], 1)
        matched = {row['run_label']: row for row in result['shared_benchmark_comparison']}
        self.assertEqual(result['shared_benchmark_count'], 1)
        self.assertEqual(matched['v1']['mean_correctness_0_to_4'], 2)
        self.assertEqual(matched['v2']['mean_correctness_0_to_4'], 4)
        self.assertFalse(result['legal_acceptance'])
        self.assertFalse(result['reviewer_qualification_verified'])

    def test_disjoint_cohorts_do_not_produce_shared_comparison(self):
        self.score(self.benchmark('A'), 'v1')
        self.score(self.benchmark('B'), 'v2', 'Другой ответ.')
        result = self.evidence.metrics(self.owner, self.matter)
        self.assertEqual(result['shared_benchmark_count'], 0)
        self.assertEqual(result['shared_benchmark_comparison'], [])

    def test_legacy_scores_without_review_snapshot_are_reported_but_excluded(self):
        identity = self.benchmark(); saved = self.score(identity)
        with self.store.connect(True) as db:
            row = json.loads(db.execute('SELECT scores FROM evaluations WHERE id=?', (saved['evaluation_id'],)).fetchone()[0])
            del row['benchmark_review_sha256']
            db.execute('UPDATE evaluations SET scores=? WHERE id=?', (canonical(row), saved['evaluation_id']))
        result = self.evidence.metrics(self.owner, self.matter)
        self.assertEqual(result['excluded_without_review_binding'], 1)
        self.assertEqual(result['human_evaluations'], 0)
        self.assertIsNone(result['mean_correctness_0_to_4'])

    def test_labels_validate_and_never_bypass_duplicate_answer_detection(self):
        identity = self.benchmark()
        for label in ('', [], 'x' * 81):
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.score(identity, label)
        self.score(identity)
        with self.assertRaises(WorkspaceError):
            self.score(identity, 'v2')

    def test_readers_can_view_aggregates_but_cannot_record_scores(self):
        identity = self.benchmark()
        self.store.grant(self.owner, self.matter, self.other, 'viewer')
        with self.assertRaises(WorkspaceError):
            self.evidence.score(self.other, identity, 'Ответ', 3, [], 0, 'Оценка')
        self.score(identity)
        result = dispatch(self.store, self.other, '/workspace/evaluations/metrics', {'matter_id': self.matter})
        self.assertEqual(result['human_evaluations'], 1)
        self.assertNotIn('СИНТЕТИЧЕСКАЯ оценка транспорта', json.dumps(result, ensure_ascii=False))
        with self.assertRaises(WorkspaceError):
            dispatch(self.store, self.other, '/workspace/evaluations/metrics', {'matter_id': 'other-matter'})

    def test_dispatch_passes_run_label_without_promoting_trust(self):
        identity = self.benchmark()
        result = dispatch(self.store, self.reviewer, '/workspace/evaluations/record', {
            'artifact_id': identity, 'answer': 'СИНТЕТИЧЕСКИЙ ответ для HTTP.', 'correctness': 4,
            'missed_risks': [], 'unsupported_claim_count': 0, 'note': 'Проверка интерфейса.', 'run_label': 'pilot-v2'})
        self.assertEqual(result['scores']['run_label'], 'pilot-v2')
        self.assertEqual(len(result['scores']['benchmark_review_sha256']), 64)
        self.assertEqual(len(result['response_sha256']), 64)
        self.assertFalse(result['legal_acceptance'])

    def test_metrics_do_not_change_existing_schema_or_write_fake_approvals(self):
        identity = self.benchmark(); self.score(identity)
        with self.store.connect() as db:
            reviews_before = db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]
            version = db.execute('PRAGMA user_version').fetchone()[0]
        self.evidence.metrics(self.owner, self.matter)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], reviews_before)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], version)
