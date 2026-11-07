import json
import unittest
from unittest.mock import patch

from agent import AgentError, Source
from case_law import handle_case_law, validate_decisions
from legal_review import applicability, handle_legal_review, validate_legal_bases

DOCUMENT = 'Поставка товара производится в течение десяти дней после оплаты.'
SOURCE = Source('Условный источник', 'https://example.org/law', 'Статья 1. Условная норма для технической проверки цитат.', 'test')


def review_result():
    return {'summary': 'Черновик', 'risks': [{
        'category': 'сроки и пролонгация', 'severity': 'low', 'quote': DOCUMENT,
        'comment': 'Проверить срок', 'recommendation': 'Уточнить порядок',
        'legal_basis': [{'source_id': 1, 'provision': 'Статья 1', 'quote': SOURCE.text}],
    }], 'missing_clauses': []}


class LegalReviewTests(unittest.TestCase):
    def test_consumer_facts_need_clarification_and_do_not_trigger_network(self):
        with patch('legal_review.TavilySearch') as search, patch('legal_review.OpenAICompatibleModel') as model:
            result = handle_legal_review({'document': DOCUMENT, 'profile': 'consumer'})
        self.assertEqual(result['status'], 'needs_facts')
        self.assertEqual(len(result['applicability']['questions']), 3)
        search.assert_not_called()
        model.assert_not_called()

    def test_false_fact_excludes_selected_scope(self):
        result = applicability('consumer', {'customer_is_individual': True, 'personal_use': False, 'seller_is_business': True})
        self.assertEqual(result['status'], 'scope_mismatch')

    def test_non_boolean_fact_is_rejected(self):
        with self.assertRaises(ValueError):
            applicability('employment', {'employment_relationship': 'yes'})

    def test_fake_citation_and_fake_provision_are_rejected(self):
        result = review_result()
        self.assertEqual(validate_legal_bases(result, DOCUMENT, [SOURCE]), [])
        result['risks'][0]['legal_basis'][0]['quote'] = 'Выдуманная цитата законодательства'
        self.assertTrue(validate_legal_bases(result, DOCUMENT, [SOURCE]))
        result = review_result()
        result['risks'][0]['legal_basis'][0]['provision'] = 'Статья 999'
        self.assertTrue(validate_legal_bases(result, DOCUMENT, [SOURCE]))

    def test_contract_text_is_not_sent_to_search(self):
        with patch('legal_review.ensure_document_transfer_allowed'), patch('legal_review.TavilySearch') as search, patch(
            'legal_review.OpenAICompatibleModel'
        ) as model:
            search.return_value.search.return_value = [SOURCE]
            model.return_value.complete.return_value = json.dumps(review_result())
            result = handle_legal_review({'document': DOCUMENT, 'facts': {'both_businesses': True}})
        query = search.return_value.search.call_args.args[0]
        self.assertNotIn(DOCUMENT, query)
        self.assertFalse(result['verified'])
        self.assertEqual(result['risks'][0]['basis_status'], 'quoted_source_unverified')

    def test_remote_document_transfer_guard_runs_before_search(self):
        with patch('legal_review.ensure_document_transfer_allowed', side_effect=ValueError('blocked')), patch('legal_review.TavilySearch') as search:
            with self.assertRaises(ValueError):
                handle_legal_review({'document': DOCUMENT, 'facts': {'both_businesses': True}})
            search.assert_not_called()

    def test_demo_profiles_do_not_use_external_services(self):
        for profile, facts in [('business', {'both_businesses': True}),
                               ('consumer', {'customer_is_individual': True, 'personal_use': True, 'seller_is_business': True}),
                               ('employment', {'employment_relationship': True})]:
            result = handle_legal_review({'document': DOCUMENT, 'profile': profile, 'facts': facts, 'demo': True})
            self.assertEqual(result['status'], 'draft')
            self.assertFalse(result['verified'])


class CourtMetadataTests(unittest.TestCase):
    def source_and_record(self):
        text = 'Условный суд 01.03.2023 дело А00-123/2023. Технический пример.'
        return Source('Пример', 'https://example.org/case', text, 'q'), {
            'source_id': 1, 'court': 'Условный суд', 'case_number': 'А00-123/2023',
            'decision_date': '2023-03-01', 'quote': text}

    def test_literal_joint_metadata_is_accepted_but_unverified(self):
        source, record = self.source_and_record()
        accepted, rejected = validate_decisions([record], [source])
        self.assertEqual(len(accepted), 1)
        self.assertFalse(accepted[0]['verified'])
        self.assertEqual(rejected, [])

    def test_fabricated_date_or_number_is_rejected(self):
        for key, value in [('decision_date', '2023-03-02'), ('case_number', 'А00-999/2023')]:
            source, record = self.source_and_record()
            record[key] = value
            accepted, rejected = validate_decisions([record], [source])
            self.assertEqual(accepted, [])
            self.assertTrue(rejected)

    def test_later_decisions_are_excluded_from_historical_set(self):
        source, record = self.source_and_record()
        with patch('case_law.TavilySearch') as search, patch('case_law.OpenAICompatibleModel') as model:
            search.return_value.search.return_value = [source]
            model.return_value.complete.return_value = json.dumps({'decisions': [record]})
            result = handle_case_law({'question': 'Тест', 'as_of': '2023-02-28'})
        self.assertEqual(result['decisions'], [])
        self.assertEqual(result['later_decisions_excluded'], 1)
