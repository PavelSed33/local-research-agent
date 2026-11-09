import unittest

from reviewed_evidence import ReviewedEvidence
import test_workspace_store as store_tests
from workspace_store import WorkspaceError


class ReviewedEvidenceTests(unittest.TestCase):
    def setUp(self):
        store_tests.WorkspaceStoreTests.setUp(self)
        self.reviewer = self.store.create_user('Эксперт', 'reviewer', actor=self.owner)['user_id']
        self.store.grant(self.owner, self.matter, self.reviewer, 'viewer')
        self.evidence = ReviewedEvidence(self.store)
        self.document_id = self.store.add_document(self.owner, self.matter, {
            'title': 'Синтетический источник', 'document': 'Условная норма. Вступает в силу 01.01.2025. Изменяется с 01.01.2026.',
            'source_url': 'https://publication.pravo.gov.ru/document/synthetic-not-real'})['document_id']
        self.attestations = dict.fromkeys(['source_authenticity', 'completeness', 'temporal_applicability', 'legal_reasoning'], True)

    def edition(self, **extra):
        payload = {'act_id': 'synthetic', 'provision': '1', 'document_id': self.document_id,
                   'provision_quote': 'Условная норма.',
                   'valid_from': '2025-01-01', 'valid_to': '2026-01-01', 'full_text_declared': True,
                   'period_evidence': [{'document_id': self.document_id, 'quote': 'Вступает в силу 01.01.2025. Изменяется с 01.01.2026.'}]}
        payload.update(extra)
        return self.evidence.add(self.owner, self.matter, 'edition', payload)['artifact_id']

    def approve(self, identity):
        return self.evidence.review(self.reviewer, identity, 'approved', 'СИНТЕТИЧЕСКИЙ тест учёта проверки, не юридическое заключение.', self.attestations)

    def test_drafts_are_not_selected_and_review_is_independent(self):
        identity = self.edition()
        self.assertEqual(self.evidence.select_edition(self.owner, self.matter, 'synthetic', '1', '2025-06-01')['status'], 'no_reviewed_edition')
        with self.assertRaises(WorkspaceError):
            self.evidence.review(self.owner, identity, 'approved', 'Self approval', self.attestations)
        self.approve(identity)
        selected = self.evidence.select_edition(self.owner, self.matter, 'synthetic', '1', '2025-06-01')
        self.assertEqual(selected['status'], 'review_recorded')
        self.assertFalse(selected['legal_acceptance'])
        self.assertIsNone(self.evidence.select_edition(self.owner, self.matter, 'synthetic', '1', '2026-01-01')['edition'])

    def test_overlap_revocation_and_boundary_selection(self):
        first, second = self.edition(), self.edition()
        self.approve(first); self.approve(second)
        self.assertEqual(self.evidence.select_edition(self.owner, self.matter, 'synthetic', '1', '2025-06-01')['status'], 'ambiguous')
        self.evidence.review(self.reviewer, second, 'revoked', 'Синтетический отзыв.', self.attestations)
        self.assertEqual(self.evidence.select_edition(self.owner, self.matter, 'synthetic', '1', '2025-06-01')['matching_count'], 1)

    def test_dates_and_fabricated_period_quotes_are_rejected(self):
        for extra in [{'valid_from': '2025-02-30'}, {'valid_to': '2024-01-01'},
                      {'valid_from': '2024-01-01'}, {'period_evidence': [{'document_id': self.document_id, 'quote': 'Выдуманное правило о вступлении в силу.'}]}]:
            with self.assertRaises(ValueError):
                self.edition(**extra)

    def test_a_reviewer_cannot_approve_their_own_artifact(self):
        self.store.grant(self.owner, self.matter, self.reviewer, 'editor')
        original = self.evidence.get(self.owner, self.edition())['payload']
        payload = {key: original[key] for key in ('act_id', 'provision', 'document_id', 'valid_from', 'valid_to', 'full_text_declared')}
        payload['period_evidence'] = [{'document_id': self.document_id, 'quote': original['period_evidence'][0]['quote']}]
        identity = self.evidence.add(self.reviewer, self.matter, 'edition', payload)['artifact_id']
        with self.assertRaises(WorkspaceError):
            self.approve(identity)

    def test_secondary_source_cannot_be_approved_as_primary_edition(self):
        document = self.store.add_document(self.owner, self.matter, {'title': 'Справочная копия',
            'document': 'Условная норма. Вступает в силу 01.01.2025. Изменяется с 01.01.2026.',
            'source_url': 'https://www.consultant.ru/document/synthetic'})['document_id']
        with self.assertRaises(ValueError):
            self.approve(self.edition(document_id=document))

    def test_sources_from_other_matters_are_not_accepted(self):
        other = self.store.create_matter(self.owner, 'Другой проект')['matter_id']
        document = self.store.add_document(self.owner, other, {'title': 'Источник', 'document': 'Вступает в силу 01.01.2025. Изменяется с 01.01.2026.'})['document_id']
        with self.assertRaises(ValueError):
            self.edition(document_id=document)

    def test_nested_document_and_artifact_ids_reject_wrong_types(self):
        for wrong_id in ([], {}, True, None):
            with self.subTest(value=wrong_id), self.assertRaises(ValueError):
                self.edition(period_evidence=[{'document_id': wrong_id, 'quote': 'Условная норма.'}])
            with self.store.connect() as db, self.assertRaises(ValueError):
                self.evidence._artifact(db, self.owner, wrong_id)

    def test_human_benchmark_scores_track_missed_risks_and_unconfirmed_claims(self):
        payload = {'question': 'Условный вопрос?', 'as_of': '2025-01-01', 'expected_answer': 'Условный ответ для теста.',
                   'required_risks': ['Риск А', 'Риск Б'], 'forbidden_claims': ['Гарантия победы'],
                   'citations': [{'document_id': self.document_id, 'quote': 'Условная норма.'}]}
        identity = self.evidence.add(self.owner, self.matter, 'benchmark', payload)['artifact_id']
        with self.assertRaises(WorkspaceError):
            self.evidence.score(self.reviewer, identity, 'Ответ', 3, [1], 1, 'Проверка')
        self.approve(identity)
        self.evidence.score(self.reviewer, identity, 'Синтетический ответ.', 3, [1], 2, 'Синтетическая оценка.')
        with self.assertRaises(WorkspaceError):
            self.evidence.score(self.reviewer, identity, 'Синтетический ответ.', 3, [1], 2, 'Повторная оценка не должна менять статистику.')
        metrics = self.evidence.metrics(self.owner, self.matter)
        self.assertEqual(metrics['mean_correctness_0_to_4'], 3)
        self.assertEqual(metrics['missed_required_risk_fraction'], .5)
        self.assertEqual(metrics['unsupported_claim_count'], 2)
        self.evidence.review(self.reviewer, identity, 'revoked', 'Синтетический отзыв', self.attestations)
        self.assertEqual(self.evidence.metrics(self.owner, self.matter)['human_evaluations'], 0)

    def add_decision(self, decision_date, body, links=[]):
        document = self.store.add_document(self.owner, self.matter, {'title': 'СИНТЕТИЧЕСКИЙ акт', 'document': body})['document_id']
        payload = {'case_number': 'А00-1/2025', 'court': 'Учебный суд', 'decision_date': decision_date, 'instance': 'Первая инстанция',
                   'identity': {'document_id': document, 'quote': body.split('\n')[0]},
                   'outcome': {'document_id': document, 'quote': 'Условное требование удовлетворено.'},
                   'full_text_declared': True, 'positions': [{'side': 'supporting', 'citation': {'document_id': document, 'quote': 'Условное требование удовлетворено.'}, 'applicability_note': 'Нужно сравнить факты.'}],
                   'links': [{**link, 'citation': {'document_id': document, 'quote': body.split('\n')[-1]}} for link in links]}
        return self.evidence.add(self.owner, self.matter, 'decision', payload)['artifact_id']

    def test_case_history_excludes_future_events_and_binds_links_to_quotes(self):
        first = self.add_decision('2025-01-01', 'Учебный суд. Первая инстанция. Дело А00-1/2025 от 01.01.2025\nУсловное требование удовлетворено.')
        second = self.add_decision('2025-02-01', 'Учебный суд. Первая инстанция. Дело А00-1/2025 от 01.02.2025\nУсловное требование удовлетворено.\nАкт по делу А00-1/2025 от 01.01.2025 оставлен без изменения.', [{'target_id': first, 'relation': 'upheld'}])
        timeline = self.evidence.timeline(self.owner, self.matter, 'А00-1/2025', '2025-01-15')
        self.assertEqual(len(timeline['decisions']), 1)
        self.assertEqual(timeline['later_decisions_excluded'], 1)
        self.assertFalse(timeline['complete_history'])
        self.assertEqual(self.evidence.get(self.owner, second)['payload']['links'][0]['target_id'], first)
