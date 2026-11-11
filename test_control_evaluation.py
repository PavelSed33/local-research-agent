"""Frozen controls and paired regression gates use synthetic human-score records."""
import unittest

from control_evaluation import ControlEvaluation
from learning_memory import LearningMemory
import test_quality_scoring as quality_tests
import test_service_pilot as pilot_tests
from reviewed_evidence import ReviewedEvidence
from workspace_api import dispatch
from workspace_backup import create_backup, restore_backup
from workspace_store import WorkspaceError, WorkspaceStore
from benchmark_tasks import BenchmarkTasks
from control_evaluation import control_plan


class ControlEvaluationTests(unittest.TestCase):
    setUp = quality_tests.QualityScoringTests.setUp
    approve = quality_tests.QualityScoringTests.approve
    benchmark = quality_tests.QualityScoringTests.benchmark
    score = quality_tests.QualityScoringTests.score

    def freeze(self, identities):
        self.control = ControlEvaluation(self.store)
        return self.control.create(self.owner, self.matter, 'СИНТЕТИЧЕСКИЙ контроль', identities)['set_id']

    def compare(self, identity, **extra):
        return dispatch(self.store, self.owner, '/workspace/quality/compare', {
            'matter_id': self.matter, 'set_id': identity, 'baseline_label': 'v1', 'candidate_label': 'v2', **extra})

    def pair(self, identity, left=2, right=4, **kwargs):
        self.score(identity, 'v1', 'СИНТЕТИЧЕСКИЙ ответ исходной версии.', correctness=left, missed=[1], unsupported=1)
        self.score(identity, 'v2', 'СИНТЕТИЧЕСКИЙ ответ новой версии.', correctness=right,
                   missed=kwargs.get('missed', []), unsupported=kwargs.get('unsupported', 0))

    def memory(self):
        return LearningMemory(self.store).select(self.owner, self.matter, 'СИНТЕТИЧЕСКИЙ новый вопрос', '2025-01-01')

    def test_frozen_membership_automatically_excludes_ids_and_duplicate_questions(self):
        identity = self.benchmark(); self.score(identity)
        self.assertEqual(self.memory()['used_count'], 1)
        self.freeze([identity])
        self.assertEqual(self.memory()['used_count'], 0)
        duplicate = self.benchmark(); self.score(duplicate)
        self.assertEqual(self.memory()['used_count'], 0)

    def test_draft_plan_reserves_ten_tasks_before_scores_exist(self):
        plan = control_plan()
        self.assertEqual(len(set(plan['control_case_ids']) & set(plan['training_case_ids'])), 0)
        self.assertFalse(plan['lawyer_reviewed'])
        tasks = BenchmarkTasks(self.store)
        tasks.import_tasks(self.owner, self.matter, ['SERV-P01', 'SERV-P02'])
        listing = tasks.list_tasks(self.owner, self.matter)['tasks']
        for task in listing:
            case = tasks.read(self.owner, task['task_id'])['source_case']
            draft = tasks.save_draft(self.owner, task['task_id'], {
                'expected_answer': 'СИНТЕТИЧЕСКИЙ ответ для проверки учёта.', 'required_risks': ['Учебный риск'],
                'forbidden_claims': [], 'citations': [{'document_id': self.document_id, 'quote': 'Условная норма.'}]})
            self.approve(draft['artifact_id'])
            self.score(draft['artifact_id'], missed=[0])
            result = LearningMemory(self.store).select(self.owner, self.matter, case['facts'][:1900] + ' новый', case['as_of'])
            ids = [row['benchmark_id'] for row in result['examples']]
            if task['case_id'] == 'SERV-P02':
                self.assertNotIn(draft['artifact_id'], ids)
            else:
                self.assertIn(draft['artifact_id'], ids)
    def test_assigning_a_control_invalidates_previously_selected_learning(self):
        identity = self.benchmark(); self.score(identity); refs = self.memory()['references']
        self.freeze([identity])
        with self.store.connect() as db, self.assertRaises(WorkspaceError) as caught:
            LearningMemory(self.store).assert_current(db, self.owner, self.matter, '2025-01-01', refs)
        self.assertEqual(caught.exception.code, 'learning_review_changed')

    def test_drafts_foreign_benchmarks_and_viewer_assignment_are_rejected(self):
        identity = self.benchmark()
        self.evidence.review(self.reviewer, identity, 'revoked', 'Учебный отзыв.', self.attestations)
        with self.assertRaises(WorkspaceError): self.freeze([identity])
        self.approve(identity)
        other = self.store.create_matter(self.owner, 'Другой проект')['matter_id']
        with self.assertRaises(WorkspaceError): ControlEvaluation(self.store).create(self.owner, other, 'Контроль', [identity])
        with self.assertRaises(WorkspaceError): ControlEvaluation(self.store).create(self.reviewer, self.matter, 'Контроль', [identity])

    def test_strict_sets_and_labels(self):
        identity = self.benchmark()
        for ids in ([], [identity, identity], [True], 'invalid', ['x'] * 101):
            with self.subTest(ids=str(ids)[:30]), self.assertRaises(ValueError): self.freeze(ids)
        control = self.freeze([identity])
        for extra in ({'candidate_label': 'v1'}, {'baseline_label': ''}, {'candidate_label': []}, {'unexpected': True}):
            with self.subTest(extra=extra), self.assertRaises(ValueError): self.compare(control, **extra)

    def test_complete_improvement_is_only_eligible_for_human_review(self):
        identity = self.benchmark(); control = self.freeze([identity]); self.pair(identity)
        result = self.compare(control)
        self.assertEqual(result['gate'], 'no_regression'); self.assertTrue(result['improvement_observed'])
        self.assertEqual(result['averages']['baseline']['correctness'], 2)
        self.assertEqual(result['averages']['candidate']['correctness'], 4)
        self.assertTrue(result['eligible_for_expert_review'])
        self.assertFalse(result['adoption_approved']); self.assertFalse(result['quality_improvement_proven'])
        self.assertFalse(result['run_provenance_verified']); self.assertFalse(result['legal_acceptance'])

    def test_averages_cannot_hide_one_task_regression(self):
        a, b = self.benchmark('A'), self.benchmark('B'); control = self.freeze([a, b])
        self.pair(a, left=0, right=4); self.pair(b, left=4, right=3)
        result = self.compare(control)
        self.assertGreater(result['averages']['candidate']['correctness'], result['averages']['baseline']['correctness'])
        self.assertEqual(result['gate'], 'regression'); self.assertFalse(result['eligible_for_expert_review'])

    def test_new_missed_risks_or_unsupported_claims_block_gate(self):
        for index, kwargs in enumerate(({'missed': [0, 1]}, {'unsupported': 2}, {'missed': [0]})):
            identity = self.benchmark(str(index)); control = self.freeze([identity]); self.pair(identity, **kwargs)
            self.assertEqual(self.compare(control)['gate'], 'regression')

    def test_missing_tasks_and_ambiguous_answers_do_not_pass(self):
        a, b = self.benchmark('A'), self.benchmark('B'); control = self.freeze([a, b]); self.pair(a)
        result = self.compare(control); self.assertEqual(result['gate'], 'incomplete')
        self.assertEqual(result['paired_task_count'], 1); self.assertEqual(result['task_count'], 2)
        self.pair(b); self.score(a, 'v2', 'Другой ответ той же метки.')
        self.assertEqual(self.compare(control)['gate'], 'incomplete')

    def test_scores_from_before_freeze_are_not_comparison_evidence(self):
        identity = self.benchmark(); self.pair(identity); control = self.freeze([identity])
        self.assertEqual(self.compare(control)['paired_task_count'], 0)

    def test_reviewer_cohorts_must_match(self):
        identity = self.benchmark(); control = self.freeze([identity]); self.pair(identity)
        other = self.store.create_user('Другой эксперт', 'reviewer', actor=self.owner)['user_id']
        self.store.grant(self.owner, self.matter, other, 'viewer')
        self.evidence.score(other, identity, 'Ответ другого эксперта.', 4, [], 0, 'СИНТЕТИЧЕСКАЯ оценка.', 'v2')
        self.assertEqual(self.compare(control)['gate'], 'incomplete')

    def test_each_task_has_equal_weight_with_different_expert_counts(self):
        a, b = self.benchmark('A'), self.benchmark('B'); control = self.freeze([a, b])
        self.pair(a, left=0, right=4); self.pair(b, left=4, right=4)
        other = self.store.create_user('Второй эксперт', 'reviewer', actor=self.owner)['user_id']
        self.store.grant(self.owner, self.matter, other, 'viewer')
        self.evidence.score(other, b, 'Дополнительная исходная оценка.', 4, [1], 1, 'Учебный контроль.', 'v1')
        self.evidence.score(other, b, 'Дополнительная новая оценка.', 4, [], 0, 'Учебный контроль.', 'v2')
        self.assertEqual(self.compare(control)['averages']['baseline']['correctness'], 2)

    def test_revocation_and_reapproval_invalidate_fixed_set(self):
        identity = self.benchmark(); control = self.freeze([identity]); self.pair(identity)
        self.evidence.review(self.reviewer, identity, 'revoked', 'Учебный отзыв.', self.attestations)
        with self.assertRaises(WorkspaceError): self.compare(control)
        self.approve(identity)
        with self.assertRaises(WorkspaceError) as caught: self.compare(control)
        self.assertEqual(caught.exception.code, 'control_review_changed')
        self.assertEqual(self.memory()['used_count'], 0)

    def test_identical_answers_can_be_explicitly_attributed_without_faking_new_scores(self):
        identity = self.benchmark(); control = self.freeze([identity])
        score = self.score(identity, 'v1', 'Одинаковый ответ обеих версий.', correctness=4, missed=[], unsupported=0)
        self.control.register_same_answer(self.reviewer, self.matter, control, score['evaluation_id'], 'v2', True)
        result = self.compare(control)
        self.assertEqual(result['gate'], 'no_regression'); self.assertFalse(result['improvement_observed'])
        pair = result['tasks'][0]['pairs'][0]
        self.assertEqual(pair['baseline_evaluation_id'], pair['candidate_evaluation_id'])
        self.assertFalse(result['run_provenance_verified'])
        with self.assertRaises(WorkspaceError):
            self.control.register_same_answer(self.reviewer, self.matter, control, score['evaluation_id'], 'v2', True)
        with self.assertRaises(ValueError):
            self.control.register_same_answer(self.reviewer, self.matter, control, score['evaluation_id'], 'v3', False)

    def test_other_users_cannot_attribute_an_experts_score(self):
        identity = self.benchmark(); control = self.freeze([identity]); score = self.score(identity)
        with self.assertRaises(WorkspaceError):
            self.control.register_same_answer(self.owner, self.matter, control, score['evaluation_id'], 'v2', True)
        with self.assertRaises(WorkspaceError): self.compare(control, matter_id='unknown-matter')
        with self.assertRaises(WorkspaceError):
            dispatch(self.store, self.other, '/workspace/quality/sets/list', {'matter_id': self.matter})

    def test_corrupted_evaluations_cannot_produce_a_passing_gate(self):
        identity = self.benchmark(); control = self.freeze([identity]); self.pair(identity)
        with self.store.connect(True) as db:
            db.execute('UPDATE evaluations SET response_text=? WHERE artifact_id=?', ('CORRUPTED', identity))
        with self.assertRaises(WorkspaceError): self.compare(control)

    def test_backup_preserves_controls_and_their_automatic_exclusion(self):
        identity = self.benchmark(); control = self.freeze([identity]); self.pair(identity)
        archive = self.path.parent / 'controls.zip'; backup = create_backup(self.path, archive)
        restored_path = self.path.parent / 'restored.db'
        restore_backup(archive, restored_path, backup['snapshot_sha256']); restored = WorkspaceStore(restored_path)
        self.assertEqual(ControlEvaluation(restored).compare(self.owner, self.matter, control, 'v1', 'v2')['gate'], 'no_regression')
        self.assertEqual(LearningMemory(restored).select(self.owner, self.matter, 'СИНТЕТИЧЕСКИЙ новый вопрос', '2025-01-01')['used_count'], 0)
        self.assertIsNone(restored.authenticate(self.admin['token']))


class ControlEvaluationHttpTests(unittest.TestCase):
    setUp = pilot_tests.ServicePilotHttpTests.setUp
    cleanup = pilot_tests.ServicePilotHttpTests.cleanup
    request = pilot_tests.ServicePilotHttpTests.request
    matter = pilot_tests.ServicePilotHttpTests.matter

    def test_http_control_creation_scores_comparison_and_identical_answer_registration(self):
        matter = self.matter(); owner = self.admin['user_id']
        reviewer = self.store.create_user('СИНТЕТИЧЕСКИЙ эксперт', 'reviewer', actor=owner)
        self.store.grant(owner, matter, reviewer['user_id'], 'viewer')
        doc = self.store.add_document(owner, matter, {'title': 'Учебный источник', 'document': 'СИНТЕТИЧЕСКАЯ цитата для контроля.'})['document_id']
        evidence = ReviewedEvidence(self.store)
        benchmark = evidence.add(owner, matter, 'benchmark', {'question': 'Учебный контрольный вопрос', 'as_of': '2025-01-01',
            'expected_answer': 'Учебный ответ, не юридическое заключение.', 'required_risks': ['Учебный риск'],
            'forbidden_claims': [], 'citations': [{'document_id': doc, 'quote': 'СИНТЕТИЧЕСКАЯ цитата для контроля.'}]})['artifact_id']
        attestations = dict.fromkeys(['source_authenticity', 'completeness', 'temporal_applicability', 'legal_reasoning'], True)
        evidence.review(reviewer['user_id'], benchmark, 'approved', 'СИНТЕТИЧЕСКОЕ одобрение механизма.', attestations)
        status, created = self.request('/workspace/quality/sets/create', {'matter_id': matter, 'title': 'Учебный контроль', 'benchmark_ids': [benchmark]})
        self.assertEqual(status, 200)
        scores = []
        for label, correctness in (('v1', 2), ('v2', 4)):
            status, score = self.request('/workspace/evaluations/record', {'artifact_id': benchmark, 'run_label': label,
                'answer': 'Учебный ответ ' + label, 'correctness': correctness, 'missed_risks': [],
                'unsupported_claim_count': 0, 'note': 'СИНТЕТИЧЕСКАЯ оценка.'}, token=reviewer['token'])
            self.assertEqual(status, 200); scores.append(score)
        compare = {'matter_id': matter, 'set_id': created['set_id'], 'baseline_label': 'v1', 'candidate_label': 'v2'}
        status, result = self.request('/workspace/quality/compare', compare)
        self.assertEqual(status, 200); self.assertTrue(result['improvement_observed']); self.assertFalse(result['adoption_approved'])
        self.assertEqual(self.request('/workspace/quality/compare', compare, token=self.other['token'])[0], 403)
        register = {'matter_id': matter, 'set_id': created['set_id'], 'evaluation_id': scores[0]['evaluation_id'],
                    'run_label': 'identical', 'same_answer_declared': True}
        self.assertEqual(self.request('/workspace/quality/runs/register', register, token=reviewer['token'])[0], 200)
        self.assertEqual(self.request('/workspace/quality/compare', {**compare, 'candidate_label': 'identical'})[1]['gate'], 'no_regression')
        self.assertEqual(self.request('/workspace/evidence/review', {'artifact_id': created['set_id'], 'decision': 'approved',
            'note': 'Попытка одобрить эксперимент.', 'attestations': attestations}, token=reviewer['token'])[0], 403)


if __name__ == '__main__': unittest.main()
