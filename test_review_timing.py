import json
import unittest
from unittest.mock import patch

from legal_catalog import review_checklist, review_timing
from legal_review import handle_legal_review


class ReviewTimingTests(unittest.TestCase):
    def test_unknown_dates_are_questions_not_inferred_facts(self):
        result = review_timing('2026-11-07')
        self.assertIsNone(result['contract_signed_on'])
        self.assertEqual(result['status'], 'needs_temporal_facts')
        self.assertEqual(len(result['questions']), 2)

    def test_event_after_signing_is_distinct_from_signing(self):
        result = review_timing('2026-11-07', '2020-01-01', 'termination')
        self.assertEqual(result['review_event'], 'termination')
        self.assertEqual(result['status'], 'context_supplied')
        self.assertFalse(result['verified'])

    def test_inconsistent_or_invalid_dates_are_rejected(self):
        for as_of, signed, event in [('2023-01-01', '2024-01-01', 'performance'),
                                     ('2023-01-02', '2023-01-01', 'conclusion'),
                                     ('2023-01-01', '2023-02-29', 'dispute')]:
            with self.subTest(signed=signed), self.assertRaises(ValueError):
                review_timing(as_of, signed, event)

    def test_older_contract_does_not_automatically_exclude_135_transition(self):
        result = review_checklist('consumer', 'services', '2026-11-07', contract_signed_on='2020-01-01')
        rules = {rule['id']: rule for rule in result['rules']}
        note = rules['consumer-exit-fee']['temporal_note']
        self.assertTrue(note['previously_concluded_contracts_mentioned'])
        self.assertFalse(note['verified'])
        self.assertEqual(rules['consumer-exit-fee']['transition']['provision'], 'статья 2, часть 2')

    def test_135_version_still_not_used_before_commencement(self):
        result = review_checklist('consumer', 'services', '2022-08-31', contract_signed_on='2020-01-01')
        self.assertNotIn('consumer-exit-fee', {rule['id'] for rule in result['rules']})

    def test_other_amendment_is_not_given_135_transition_automatically(self):
        result = review_checklist('consumer', 'services', '2026-11-07', contract_signed_on='2020-01-01')
        rule = next(rule for rule in result['rules'] if rule['id'] == 'consumer-preset-consent-2025')
        self.assertFalse(rule['temporal_note']['previously_concluded_contracts_mentioned'])
        self.assertFalse(rule['verified'])

    def test_supplied_context_reaches_model_and_response(self):
        document = 'Условный договор оказания услуг для проверки.'
        output = {'summary': 'Черновик', 'risks': [], 'missing_clauses': []}
        with patch('legal_review.ensure_document_transfer_allowed'), patch('legal_review.TavilySearch') as search, patch('legal_review.OpenAICompatibleModel') as model:
            search.return_value.search.return_value = []
            model.return_value.complete.return_value = json.dumps(output)
            result = handle_legal_review({'document': document, 'facts': {'both_businesses': True},
                                         'as_of': '2026-11-07', 'contract_signed_on': '2020-01-01',
                                         'review_event': 'performance'})
        self.assertEqual(result['temporal_context']['contract_signed_on'], '2020-01-01')
        self.assertIn('2020-01-01', model.return_value.complete.call_args.args[1])
        self.assertIn('performance', model.return_value.complete.call_args.args[1])
