"""Literal provision boundaries, not semantic or legal acceptance."""
import unittest

from agent import AgentError
from matter_research import answer
from service_pilot import validate_findings
import test_service_pilot as pilot_tests
from workspace_api import prepare_answer
from workspace_store import WorkspaceError


class ProvisionScopeTests(unittest.TestCase):
    setUp = pilot_tests.ServicePilotTests.setUp
    edition = pilot_tests.ServicePilotTests.edition
    approve = pilot_tests.ServicePilotTests.approve
    payload = pilot_tests.ServicePilotTests.payload
    prepared = pilot_tests.ServicePilotTests.prepared
    with_edition = pilot_tests.ServicePilotTests.with_edition
    risk = pilot_tests.ServicePilotTests.risk

    def test_provision_offsets_are_immutable_and_exact(self):
        identity = self.edition()
        row = self.evidence.get(self.owner, identity)
        span = row['payload']['provision_evidence']
        document = self.store.document(self.owner, self.document_id)
        self.assertEqual(document['text'][span['start']:span['end']], 'Условная норма.')
        self.assertEqual(span['document_sha256'], document['sha256'])
        self.assertEqual(row['review_status'], 'draft')
        self.assertFalse(row['legal_acceptance'])

    def test_missing_repeated_short_or_oversized_quote_is_rejected(self):
        for quote in ('Отсутствующая цитата.', 'коротко', 'а' * 10001, None):
            with self.subTest(quote=str(quote)[:30]), self.assertRaises(ValueError):
                self.edition(provision_quote=quote)
        document = self.store.add_document(self.owner, self.matter, {
            'title': 'СИНТЕТИЧЕСКИЙ повтор',
            'document': 'Условная норма. Условная норма.'})['document_id']
        with self.assertRaisesRegex(ValueError, 'ровно один раз'):
            self.edition(document_id=document)
        document = self.store.add_document(self.owner, self.matter, {
            'title': 'СИНТЕТИЧЕСКОЕ перекрытие', 'document': 'а' * 11})['document_id']
        with self.assertRaisesRegex(ValueError, 'ровно один раз'):
            self.edition(document_id=document, provision_quote='а' * 10)

    def test_legacy_edition_remains_readable_but_cannot_support_pilot(self):
        row = self.evidence.get(self.owner, self.edition())['payload']
        legacy = {key: row[key] for key in ('act_id', 'provision', 'document_id',
            'valid_from', 'valid_to', 'full_text_declared')}
        legacy['period_evidence'] = [{'document_id': self.document_id,
            'quote': 'Вступает в силу 01.01.2025. Изменяется с 01.01.2026.'}]
        identity = self.evidence.add(self.owner, self.matter, 'edition', legacy)['artifact_id']
        self.approve(identity)
        self.assertNotIn('provision_evidence', self.evidence.get(self.owner, identity)['payload'])
        with self.assertRaises(WorkspaceError) as caught:
            self.prepared(editions=[{'act_id': 'synthetic', 'provision': '1'}])
        self.assertEqual(caught.exception.code, 'edition_provision_required')
        general = prepare_answer(self.store, self.owner, {
            'matter_id': self.matter, 'question': 'Условная', 'as_of': '2025-06-30',
            'editions': [{'act_id': 'synthetic', 'provision': '1'}], 'demo': True})
        self.assertEqual(len(general['selected_editions']), 1)

    def test_exact_provision_is_included_without_matching_search_words(self):
        prepared = self.with_edition(question='несовпадающий запрос')
        source = next(row for row in prepared['sources'] if row['edition_references'])
        self.assertEqual(source['text'], 'Условная норма.')
        ref = source['edition_references'][0]
        self.assertEqual((ref['provision_start'], ref['provision_end']), (source['start'], source['end']))
        self.assertEqual(answer(prepared)['status'], 'demo_evidence')

    def test_adjacent_article_cannot_support_a_risk_in_same_document(self):
        prepared = self.with_edition(question='Изменяется')
        candidate = self.risk(prepared)
        adjacent = next(row for row in prepared['sources'] if 'Изменяется' in row['text'])
        candidate['findings'][0]['citations'][1] = {
            'source_id': adjacent['source_id'], 'quote': 'Изменяется с 01.01.2026.'}
        with self.assertRaises(AgentError):
            validate_findings(candidate, prepared['sources'], self.contract)

    def test_citation_crossing_provision_boundary_cannot_support_a_risk(self):
        prepared = self.with_edition(question='Условная')
        broad = next(row for row in prepared['sources'] if 'Вступает' in row['text'])
        self.assertTrue(broad['edition_references'])
        candidate = self.risk(prepared)
        candidate['findings'][0]['citations'][1] = {
            'source_id': broad['source_id'], 'quote': 'Условная норма. Вступает в силу'}
        with self.assertRaises(AgentError):
            validate_findings(candidate, prepared['sources'], self.contract)
        candidate['findings'][0]['citations'][1]['quote'] = 'Условная норма.'
        result = validate_findings(candidate, prepared['sources'], self.contract)
        citation = result['findings'][0]['citations'][1]
        self.assertEqual(citation['evidence_role'], 'reviewed_edition')
        self.assertFalse(result['findings'][0]['legal_acceptance'])

    def test_two_norms_in_one_document_keep_distinct_ranges(self):
        document = self.store.add_document(self.owner, self.matter, {
            'title': 'СИНТЕТИЧЕСКИЕ статьи',
            'source_url': 'https://publication.pravo.gov.ru/document/synthetic-not-real',
            'document': 'Статья А. Учебная норма А. Статья Б. Учебная норма Б.'})['document_id']
        for provision, quote in (('A', 'Статья А. Учебная норма А.'), ('B', 'Статья Б. Учебная норма Б.')):
            self.approve(self.edition(document_id=document, provision=provision, provision_quote=quote))
        prepared = self.prepared(question='Учебная', editions=[
            {'act_id': 'synthetic', 'provision': value} for value in ('A', 'B')])
        exact = [source for source in prepared['sources'] if source['text'].startswith('Статья') and len(source['edition_references']) == 1]
        self.assertEqual(len(exact), 2)
        candidate = self.risk(prepared)
        source = next(row for row in prepared['sources'] if 'норма Б.' in row['text'])
        candidate['findings'][0]['citations'][1] = {'source_id': source['source_id'], 'quote': 'Учебная норма Б.'}
        result = validate_findings(candidate, prepared['sources'], self.contract)
        refs = result['findings'][0]['citations'][1]['edition_references']
        self.assertEqual([ref['provision'] for ref in refs], ['B'])


if __name__ == '__main__':
    unittest.main()
