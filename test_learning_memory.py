"""Reviewed-example memory, held-out tasks and revocation; not quality claims."""
import json
import unittest
from unittest.mock import patch

from analysis_reports import AnalysisReports
from agent import AgentError
from learning_memory import LearningMemory
from matter_research import answer
from service_pilot import validate_findings
import test_quality_scoring as quality_tests
import test_service_pilot as pilot_tests
from workspace_api import dispatch
from workspace_store import WorkspaceError


class LearningMemoryTests(unittest.TestCase):
    setUp = quality_tests.QualityScoringTests.setUp
    approve = quality_tests.QualityScoringTests.approve
    benchmark = quality_tests.QualityScoringTests.benchmark
    score = quality_tests.QualityScoringTests.score
    edition = pilot_tests.ServicePilotTests.edition
    payload = pilot_tests.ServicePilotTests.payload
    prepared = pilot_tests.ServicePilotTests.prepared
    with_edition = pilot_tests.ServicePilotTests.with_edition
    risk = pilot_tests.ServicePilotTests.risk

    def select(self, **extra):
        return dispatch(self.store, self.owner, '/workspace/learning/preview', {
            'matter_id': self.matter, 'question': 'СИНТЕТИЧЕСКИЙ вопрос для нового разбора',
            'as_of': '2025-01-01', **extra})

    def experience(self, suffix=''):
        identity = self.benchmark(suffix); self.score(identity)
        return identity

    def test_only_current_reviewed_corrections_with_observed_errors_are_selected(self):
        identity = self.benchmark()
        self.assertEqual(self.select()['used_count'], 0)
        self.score(identity)
        result = self.select()
        self.assertEqual(result['used_count'], 1)
        example = result['examples'][0]
        self.assertEqual(example['benchmark_id'], identity)
        self.assertIn('эталон', example['corrected_answer'])
        self.assertNotIn('СИНТЕТИЧЕСКИЙ ответ.', json.dumps(result, ensure_ascii=False))
        self.assertFalse(result['weights_updated']); self.assertFalse(result['legal_acceptance'])

    def test_perfect_scores_do_not_generate_error_lessons(self):
        self.score(self.benchmark(), correctness=4, missed=[], unsupported=0)
        self.assertEqual(self.select()['used_count'], 0)

    def test_same_date_same_matter_and_shared_words_are_required(self):
        self.experience()
        self.assertEqual(self.select(as_of='2025-01-02')['used_count'], 0)
        self.assertEqual(self.select(question='несвязанные слова')['used_count'], 0)
        other = self.store.create_matter(self.owner, 'Другой проект')['matter_id']
        self.assertEqual(self.select(matter_id=other)['used_count'], 0)
        with self.assertRaises(WorkspaceError):
            LearningMemory(self.store).select(self.other, self.matter, 'СИНТЕТИЧЕСКИЙ вопрос', '2025-01-01')

    def test_explicit_holdout_and_exact_question_exclusion(self):
        identity = self.experience()
        self.assertEqual(self.select(learning_exclude_artifact_ids=[identity])['used_count'], 0)
        self.assertEqual(self.select(question='  синтетический ВОПРОС  ')['used_count'], 0)

    def test_revocation_and_reapproval_do_not_restore_old_experience(self):
        identity = self.experience(); refs = self.select()['references']
        self.evidence.review(self.reviewer, identity, 'revoked', 'СИНТЕТИЧЕСКИЙ отзыв.', self.attestations)
        self.assertEqual(self.select()['used_count'], 0)
        self.approve(identity)
        self.assertEqual(self.select()['used_count'], 0)
        with self.store.connect() as db, self.assertRaises(WorkspaceError):
            LearningMemory(self.store).assert_current(db, self.owner, self.matter, '2025-01-01', refs)

    def test_oversized_corrected_examples_are_skipped_without_truncation(self):
        identity = self.evidence.add(self.owner, self.matter, 'benchmark', {
            'question': 'СИНТЕТИЧЕСКИЙ большой пример', 'as_of': '2025-01-01',
            'expected_answer': 'Учебный текст. ' * 1000, 'required_risks': [], 'forbidden_claims': [],
            'citations': [{'document_id': self.document_id, 'quote': 'Условная норма.'}]})['artifact_id']
        self.approve(identity); self.score(identity, missed=[])
        self.assertEqual(self.select()['used_count'], 0)

    def test_context_is_bounded_to_three_examples(self):
        for i in range(4): self.experience(str(i))
        self.assertEqual(self.select()['used_count'], 3)

    def test_opt_in_only_and_strict_inputs(self):
        self.experience()
        self.assertEqual(self.prepared()['learning_context']['used_count'], 0)
        for extra in ({'use_learning': 'true'}, {'use_learning': True, 'learning_exclude_artifact_ids': 'bad'},
                      {'use_learning': True, 'learning_exclude_artifact_ids': [True]},
                      {'learning_exclude_artifact_ids': []}):
            with self.subTest(extra=extra), self.assertRaises(ValueError): self.prepared(**extra)
        with self.assertRaises(ValueError): self.select(learning_exclude_artifact_ids=['a', 'a'])

    def test_model_gets_corrected_examples_but_risks_still_require_selected_norms(self):
        self.experience()
        prepared = self.with_edition(question='СИНТЕТИЧЕСКИЙ новый вопрос', as_of='2025-01-01', use_learning=True, demo=False)
        self.assertEqual(prepared['learning_context']['used_count'], 1)
        with patch('matter_research.query_model', return_value=self.risk(prepared)) as model:
            result = answer(prepared)
        context = model.call_args.args[2]
        self.assertEqual(context['learning_context']['used_count'], 1)
        self.assertIn('не правовые источники', model.call_args.args[1])
        self.assertIn('Веса модели не изменялись', result['markdown'])
        self.assertTrue(result['learning_applied'])
        self.assertFalse(result['legal_acceptance'])

    def test_missing_norms_still_block_model_even_with_experience(self):
        self.experience()
        prepared = self.prepared(question='СИНТЕТИЧЕСКИЙ новый вопрос', as_of='2025-01-01', use_learning=True, demo=False)
        with patch('matter_research.query_model') as model:
            result = answer(prepared)
        model.assert_not_called(); self.assertEqual(result['status'], 'needs_sources')
        self.assertFalse(result['learning_applied'])

    def test_corrected_answer_is_not_a_new_citable_norm(self):
        self.experience()
        prepared = self.with_edition(question='СИНТЕТИЧЕСКИЙ новый вопрос', as_of='2025-01-01', use_learning=True)
        candidate = self.risk(prepared)
        candidate['findings'][0]['citations'][1]['quote'] = prepared['learning_context']['examples'][0]['corrected_answer']
        with self.assertRaises(AgentError):
            validate_findings(candidate, prepared['sources'], self.contract)

    def test_changed_feedback_invalidates_selected_experience(self):
        identity = self.experience(); refs = self.select()['references']
        with self.store.connect(True) as db:
            row = db.execute('SELECT id,scores FROM evaluations WHERE artifact_id=?', (identity,)).fetchone()
            score = json.loads(row['scores']); score['note'] = 'СИНТЕТИЧЕСКОЕ изменение оценки.'
            db.execute('UPDATE evaluations SET scores=? WHERE id=?', (json.dumps(score), row['id']))
        with self.store.connect() as db, self.assertRaises(WorkspaceError) as caught:
            LearningMemory(self.store).assert_current(db, self.owner, self.matter, '2025-01-01', refs)
        self.assertEqual(caught.exception.code, 'learning_review_changed')

    def test_saved_reports_recheck_learning_reviews_before_download(self):
        identity = self.experience()
        prepared = self.with_edition(question='СИНТЕТИЧЕСКИЙ новый вопрос', as_of='2025-01-01', use_learning=True)
        with self.store.connect(True) as db:
            saved = AnalysisReports(self.store).save(db, self.owner, prepared, answer(prepared))
        args = {'matter_id': self.matter, 'report_id': saved['report_id'], 'markdown_sha256': saved['markdown_sha256']}
        self.assertTrue(dispatch(self.store, self.owner, '/workspace/analysis/read', args)['context_current'])
        self.evidence.review(self.reviewer, identity, 'revoked', 'СИНТЕТИЧЕСКИЙ отзыв.', self.attestations)
        with self.assertRaises(WorkspaceError) as caught:
            dispatch(self.store, self.owner, '/workspace/analysis/read', args)
        self.assertEqual(caught.exception.code, 'learning_review_changed')

    def test_changed_evaluation_is_detected_before_publishing(self):
        identity = self.experience(); refs = self.select()['references']
        with self.store.connect(True) as db:
            db.execute('UPDATE evaluations SET response_text=? WHERE artifact_id=?', ('CORRUPTED', identity))
        with self.assertRaises(WorkspaceError): self.select()


if __name__ == '__main__':
    unittest.main()
