"""Workflow guards around declared contract facts, not a legal qualification benchmark."""
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request

from api import create_server, openapi_spec
from contract_scope import contract_qualification
from legal_review import handle_legal_review, review_options

DOCUMENT = 'Договор для технической проверки уточнения вида обязательств.'
BUSINESS = {'both_businesses': True}
CORE = {'supply': 'goods_nonpersonal_purpose', 'lease': 'temporary_use_for_fee',
        'services': 'paid_activity', 'work': 'result_handover'}


class ContractQualificationTests(unittest.TestCase):
    def test_explicit_types_need_facts_before_search_model_or_transfer_guard(self):
        with patch('legal_review.TavilySearch') as search, patch('legal_review.OpenAICompatibleModel') as model, patch('legal_review.ensure_document_transfer_allowed') as transfer:
            for kind in CORE:
                with self.subTest(kind=kind):
                    result = handle_legal_review({'document': DOCUMENT, 'contract_type': kind, 'facts': BUSINESS})
                    self.assertEqual(result['status'], 'needs_facts')
                    self.assertEqual(len(result['contract_qualification']['questions']), 3)
                    self.assertEqual(result['risks'], [])
                    self.assertEqual(result['sources'], [])
                    self.assertEqual(result['checklist']['rules'], [])
                    self.assertTrue(result['checklist']['deferred_rules'])
            search.assert_not_called()
            model.assert_not_called()
            transfer.assert_not_called()

    def test_positive_declarations_enable_only_unverified_candidate_draft(self):
        for kind, key in CORE.items():
            with self.subTest(kind=kind):
                result = handle_legal_review({'document': DOCUMENT, 'contract_type': kind, 'demo': True,
                    'facts': {**BUSINESS, key: True, 'mixed_contract': False, 'special_regime': False}})
                self.assertEqual(result['status'], 'draft')
                self.assertEqual(result['contract_qualification']['status'], 'candidate_type')
                self.assertTrue(result['checklist']['rules'])
                self.assertFalse(result['contract_qualification']['verified'])
                self.assertFalse(result['verified'])

    def test_missing_regime_facts_are_not_defaulted_to_no(self):
        result = contract_qualification('business', 'work', {'result_handover': True})
        self.assertEqual(result['status'], 'needs_facts')
        self.assertIsNone(result['declared_facts']['mixed_contract'])
        self.assertIsNone(result['declared_facts']['special_regime'])

    def test_mixed_or_special_contract_does_not_receive_ordinary_type_analysis(self):
        for flag in ('mixed_contract', 'special_regime'):
            facts = {**BUSINESS, 'result_handover': True, 'mixed_contract': False, 'special_regime': False, flag: True}
            with self.subTest(flag=flag), patch('legal_review.TavilySearch') as search, patch('legal_review.OpenAICompatibleModel') as model:
                result = handle_legal_review({'document': DOCUMENT, 'contract_type': 'work', 'facts': facts})
                self.assertEqual(result['status'], 'needs_qualification')
                self.assertEqual(result['contract_qualification']['contract_type'], 'work')
                self.assertEqual(result['risks'], [])
                search.assert_not_called()
                model.assert_not_called()

    def test_negative_core_fact_does_not_automatically_select_another_type(self):
        result = contract_qualification('business', 'services',
                                        {'paid_activity': False, 'mixed_contract': False, 'special_regime': False})
        self.assertEqual(result['status'], 'needs_qualification')
        self.assertEqual(result['contract_type'], 'services')

    def test_personal_consumer_purchase_conflicts_with_selected_supply(self):
        result = handle_legal_review({'document': DOCUMENT, 'profile': 'consumer', 'contract_type': 'supply',
            'facts': {'customer_is_individual': True, 'personal_use': True, 'seller_is_business': True,
                      'goods_nonpersonal_purpose': True, 'mixed_contract': False, 'special_regime': False}})
        self.assertEqual(result['status'], 'needs_qualification')
        self.assertTrue(result['contract_qualification']['conflicting_facts'])
        self.assertEqual(result['checklist']['rules'], [])

    def test_profile_conflict_remains_separate_from_type_fact_collection(self):
        result = handle_legal_review({'document': DOCUMENT, 'profile': 'consumer', 'contract_type': 'services',
            'facts': {'customer_is_individual': True, 'personal_use': False, 'seller_is_business': True}})
        self.assertEqual(result['status'], 'scope_mismatch')
        self.assertEqual(result['contract_qualification']['status'], 'needs_facts')

    def test_general_mode_is_limited_and_does_not_erase_declared_special_regime(self):
        generic = contract_qualification('business', 'general', {})
        self.assertEqual(generic['status'], 'general_scope_only')
        self.assertTrue(generic['limitation'])
        for flag in ('mixed_contract', 'special_regime'):
            self.assertEqual(contract_qualification('business', 'general', {flag: True})['status'], 'needs_qualification')

    def test_employment_does_not_inherit_civil_type_questions(self):
        result = handle_legal_review({'document': DOCUMENT, 'profile': 'employment', 'demo': True,
                                     'facts': {'employment_relationship': True}})
        self.assertEqual(result['status'], 'draft')
        self.assertEqual(result['contract_qualification']['status'], 'not_applicable')
        self.assertEqual(result['contract_qualification']['questions'], [])
        with self.assertRaises(ValueError):
            contract_qualification('employment', 'work', {})

    def test_unknown_and_malformed_inputs_rejected(self):
        for kind, facts in [('bad', {}), ([], {}), ('work', []), ('work', {'result_handover': 'yes'}),
                            ('general', {'mixed_contract': 1}), ('lease', {'temporary_use_for_fee': 0})]:
            with self.subTest(kind=kind, facts=facts), self.assertRaises(ValueError):
                contract_qualification('business', kind, facts)

    def test_options_describe_questions_without_coaching_answers(self):
        options = review_options()
        self.assertEqual(set(options['contract_types']), {'general', 'supply', 'lease', 'services', 'work'})
        self.assertEqual(options['contract_types']['general']['questions'], {})
        for kind, key in CORE.items():
            self.assertIn(key, options['contract_types'][kind]['questions'])
            self.assertNotIn('expected_answers', options['contract_types'][kind])
        self.assertFalse(options['verified'])

    def test_declared_facts_and_limits_reach_model_when_candidate(self):
        with patch('legal_review.ensure_document_transfer_allowed'), patch('legal_review.TavilySearch') as search, patch('legal_review.OpenAICompatibleModel') as model:
            search.return_value.search.return_value = []
            model.return_value.complete.return_value = json.dumps({'summary': 'Черновик', 'risks': [], 'missing_clauses': []})
            result = handle_legal_review({'document': DOCUMENT, 'contract_type': 'services', 'facts': {
                **BUSINESS, 'paid_activity': True, 'mixed_contract': False, 'special_regime': False}})
        self.assertIn('"paid_activity": true', model.return_value.complete.call_args.args[1])
        self.assertIn('неподтверждённые исходные данные', model.return_value.complete.call_args.args[0])
        self.assertEqual(result['contract_qualification']['status'], 'candidate_type')

    def test_options_and_clarification_over_real_http_do_not_persist_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server('127.0.0.1', 0, directory, 'scope-token')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f'http://127.0.0.1:{server.server_address[1]}'
                with urllib.request.urlopen(base + '/review/options', timeout=5) as response:
                    self.assertIn('work', json.load(response)['contract_types'])
                request = urllib.request.Request(base + '/review/legal',
                    data=json.dumps({'document': DOCUMENT, 'contract_type': 'work', 'facts': BUSINESS}).encode(),
                    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer scope-token'})
                with urllib.request.urlopen(request, timeout=5) as response:
                    result = json.load(response)
                self.assertEqual(result['status'], 'needs_facts')
                self.assertIn('/review/options', openapi_spec()['paths'])
                from pathlib import Path
                self.assertFalse(list(Path(directory).rglob('*')))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
