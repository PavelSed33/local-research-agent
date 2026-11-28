import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent import AgentError, classify_source_authority
from legal_catalog import review_checklist
from legal_review import handle_legal_review


class LegalCatalogTests(unittest.TestCase):
    def test_all_rules_have_source_and_explicit_limits(self):
        catalog = json.loads((Path(__file__).parent / 'fixtures/contract_rule_catalog.json').read_text())
        self.assertEqual(len(catalog['rules']), 17)
        self.assertFalse(catalog['lawyer_reviewed'])
        sources = {source['id'] for source in catalog['sources']}
        for rule in catalog['rules']:
            self.assertTrue(set(rule['source_ids']) <= sources)
            self.assertTrue(rule['caution'])
            self.assertFalse(rule['verified'])

    def test_supply_snapshot_is_only_selected_for_business_supply_after_capture(self):
        def selected(profile, kind, date):
            return {rule['id']: rule for rule in review_checklist(profile, kind, date)['rules']}

        rule = selected('business', 'supply', '2026-11-28')['supply-performance']
        self.assertFalse(rule['verified'])
        self.assertEqual(len(rule['sources']), 2)
        for source in rule['sources']:
            self.assertEqual(source['date_role'], 'snapshot_retrieval_date')
            self.assertRegex(source['response_sha256'], r'^[0-9a-f]{64}$')
        self.assertNotIn('supply-performance', selected('business', 'supply', '2026-11-27'))
        self.assertNotIn('supply-performance', selected('consumer', 'supply', '2026-11-28'))
        self.assertNotIn('supply-performance', selected('business', 'work', '2026-11-28'))

    def test_consumer_rules_do_not_leak_into_business_profile(self):
        selected = review_checklist('business', 'services', '2026-11-07')
        self.assertFalse(any(rule['id'].startswith('consumer-') for rule in selected['rules']))
        self.assertFalse(selected['verified'])

    def test_supply_review_offers_bound_excerpts_to_model_and_checks_a_returned_citation(self):
        document = 'Поставщик и покупатель согласовали порядок отказа по статье 523 ГК РФ.'
        payload = {'document': document, 'profile': 'business', 'contract_type': 'supply', 'as_of': '2026-11-28',
                   'facts': {'both_businesses': True, 'goods_nonpersonal_purpose': True,
                             'mixed_contract': False, 'special_regime': False}}
        title = 'Статья 523. Односторонний отказ от исполнения договора поставки'
        quote = title + '\n1. Односторонний отказ от исполнения договора поставки'
        result = {'summary': 'Черновик', 'missing_clauses': [], 'risks': [{
            'category': 'односторонние права', 'severity': 'low', 'quote': document,
            'comment': 'Проверить условие об отказе.', 'recommendation': 'Уточнить основания.',
            'legal_basis': [{'source_id': 2, 'provision': 'Статья 523', 'quote': quote}]}]}
        with patch('legal_review.ensure_document_transfer_allowed'), patch('legal_review.TavilySearch') as search, patch('legal_review.OpenAICompatibleModel') as model:
            search.return_value.search_links.return_value = []
            model.return_value.complete.return_value = json.dumps(result)
            review = handle_legal_review(payload)
        self.assertIn(quote, model.return_value.complete.call_args.args[1])
        self.assertEqual(review['sources'][1]['retrieval_mode'], 'supply_snapshot_excerpt')
        self.assertEqual(review['sources'][1]['capture']['article_number'], '523')
        self.assertEqual(review['sources'][1]['authority_class'], 'official_acts_bank')
        basis = review['risks'][0]['legal_basis'][0]
        self.assertTrue(basis['quote_checked'])
        self.assertFalse(basis['period_supported'])
        self.assertFalse(basis['verified'])
        refs = review['supply_references']
        self.assertEqual(len({(a['source_id'], a['number']) for a in refs['articles']}), 130)
        self.assertFalse(refs['period_supported'])
        early = handle_legal_review({**payload, 'as_of': '2026-11-27', 'demo': True})
        self.assertNotIn('supply_references', early)
        missing_facts = handle_legal_review({**payload, 'demo': True, 'facts': {}})
        self.assertNotIn('supply_references', missing_facts)

    def test_work_deadline_rule_does_not_apply_to_services(self):
        services = review_checklist('business', 'services', '2026-11-07')
        work = review_checklist('business', 'work', '2026-11-07')
        self.assertNotIn('work-dates', {r['id'] for r in services['rules']})
        self.assertIn('work-dates', {r['id'] for r in work['rules']})

    def test_2022_consumer_rules_are_not_used_before_commencement(self):
        before = review_checklist('consumer', 'services', '2022-08-31')
        on_date = review_checklist('consumer', 'services', '2022-09-01')
        self.assertIn('consumer-exit-fee', before['excluded_not_yet_effective_rules'])
        self.assertIn('consumer-exit-fee', {r['id'] for r in on_date['rules']})

    def test_later_sources_are_not_inserted_into_historical_review(self):
        selected = review_checklist('employment', 'general', '2022-06-01')
        for rule in selected['rules']:
            self.assertTrue(all(source['source_date'] <= '2022-06-01' for source in rule['sources']))

    def test_invalid_contract_type_is_rejected(self):
        with self.assertRaises(ValueError):
            review_checklist('employment', 'lease', '2026-11-07')

    def test_demo_result_includes_researched_questions_without_verification(self):
        result = handle_legal_review({'document': 'Условный договор оказания услуг для проверки.',
                                     'profile': 'consumer', 'contract_type': 'services', 'demo': True,
                                     'facts': {'customer_is_individual': True, 'personal_use': True, 'seller_is_business': True}})
        self.assertEqual(result['status'], 'needs_facts')
        self.assertFalse(result['checklist']['rules'])
        self.assertTrue(result['checklist']['deferred_rules'])
        self.assertFalse(result['checklist']['verified'])

    def test_catalog_does_not_count_as_fetched_quote_evidence(self):
        document = 'Условный договор оказания услуг для проверки.'
        result = {'summary': 'Черновик', 'missing_clauses': [], 'risks': [{
            'category': 'цена и порядок расчётов', 'severity': 'low', 'quote': document,
            'comment': 'Проверить', 'recommendation': 'Уточнить',
            'legal_basis': [{'source_id': 1, 'provision': 'Статья 32', 'quote': 'Выдуманная правовая цитата'}]}]}
        with patch('legal_review.ensure_document_transfer_allowed'), patch('legal_review.TavilySearch') as search, patch('legal_review.OpenAICompatibleModel') as model:
            search.return_value.search_links.return_value = []
            model.return_value.complete.return_value = json.dumps(result)
            with self.assertRaises(AgentError):
                handle_legal_review({'document': document, 'facts': {'both_businesses': True}})

    def test_source_roles_remain_distinct(self):
        self.assertEqual(classify_source_authority('https://arbitr.ru/materials/49398'), 'official_court')
        self.assertEqual(classify_source_authority('https://zpp.rospotrebnadzor.ru/npa/federal/389825'), 'government_legal_reproduction')
        self.assertEqual(classify_source_authority('https://zpp.rospotrebnadzor.ru/news/federal/247899'), 'official_legal_explanation')

    def test_2025_consent_rule_is_not_retroactively_inserted(self):
        before = review_checklist('consumer', 'services', '2025-08-31')
        after = review_checklist('consumer', 'services', '2025-09-05')
        self.assertIn('consumer-preset-consent-2025', before['excluded_not_yet_effective_rules'])
        self.assertIn('consumer-preset-consent-2025', {r['id'] for r in after['rules']})
        self.assertFalse(after['verified'])
