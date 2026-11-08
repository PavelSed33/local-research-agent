"""Adapter integrity, quote fidelity and transport integration; not legal acceptance."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent import AgentError, Source
from api import openapi_spec
from legal_skills import ROOT, handle_skill, load_skills, skill_options

DOCUMENT = 'Заказчик оплачивает услуги после получения подписанного акта.'
PLAYBOOK = 'Оплата производится только после получения подписанного акта.'


class LegalSkillsTests(unittest.TestCase):
    def payload(self, skill='contract-risk', **extra):
        return {'skill_id': skill, 'document': DOCUMENT, 'user_position': 'Заказчик', **extra}

    def model_result(self):
        return {'summary': 'Черновик', 'verified': True, 'lawyer_reviewed': True,
                'findings': [{'document_quote': DOCUMENT, 'assessment': 'Проверить условие.',
                              'priority': 'review', 'recommendation': 'Уточнить приёмку.',
                              'playbook_quote': '', 'source_quotes': [], 'verified': True}]}

    def run_model(self, payload, result):
        with patch('legal_skills.ensure_document_transfer_allowed'), patch('legal_skills.OpenAICompatibleModel') as model:
            model.return_value.complete.return_value = json.dumps(result)
            return handle_skill(payload)

    def test_three_licensed_pinned_adapters_and_demo_without_network(self):
        self.assertEqual(len(skill_options()['skills']), 3)
        with patch('legal_skills.OpenAICompatibleModel', side_effect=AssertionError('network')), patch('legal_skills.TavilySearch', side_effect=AssertionError('network')):
            for skill in load_skills():
                result = handle_skill(self.payload(skill, demo=True, playbook=PLAYBOOK, research_query='ГК РФ 310'))
                self.assertEqual(result['status'], 'draft')
                self.assertTrue(result['demo'])
                self.assertFalse(result['verified'])
                self.assertFalse(result['external_search_attempted'])

    def test_required_context_is_requested_before_any_external_work(self):
        with patch('legal_skills.ensure_document_transfer_allowed', side_effect=AssertionError('transfer')):
            for skill, field in [('contract-playbook', 'playbook'), ('claim-audit', 'research_query')]:
                result = handle_skill(self.payload(skill))
                self.assertEqual(result['status'], 'needs_facts')
                self.assertIn(field, result['missing_fields'])

    def test_model_cannot_promote_trust_flags_or_inject_extra_fields(self):
        result = self.run_model(self.payload(), self.model_result())
        self.assertFalse(result['verified'])
        self.assertFalse(result['lawyer_reviewed'])
        self.assertFalse(result['findings'][0]['verified'])
        self.assertNotIn('instructions', result)

    def test_fabricated_document_quote_rejected(self):
        result = self.model_result()
        result['findings'][0]['document_quote'] = 'Условие, которого нет в представленном документе.'
        with self.assertRaises(AgentError):
            self.run_model(self.payload(), result)

    def test_playbook_deviation_requires_exact_company_rule(self):
        result = self.model_result()
        with self.assertRaises(AgentError):
            self.run_model(self.payload('contract-playbook', playbook=PLAYBOOK), result)
        result['findings'][0]['playbook_quote'] = PLAYBOOK
        self.assertEqual(self.run_model(self.payload('contract-playbook', playbook=PLAYBOOK), result)['status'], 'draft')

    def test_audit_searches_only_explicit_topic_and_validates_source_quote(self):
        quote = 'Статья 310. Односторонний отказ от исполнения обязательства.'
        source = Source('Synthetic source', 'https://example.org/source', quote, '')
        candidate = self.model_result()
        candidate['findings'][0]['source_quotes'] = [{'source_id': 1, 'quote': quote, 'verified': True}]
        with patch('legal_skills.TavilySearch') as search:
            search.return_value.search.return_value = [source]
            result = self.run_model(self.payload('claim-audit', research_query='ГК РФ статья 310'), candidate)
            search.return_value.search.assert_called_once_with('ГК РФ статья 310', 5)
        self.assertFalse(result['findings'][0]['source_quotes'][0]['verified'])
        self.assertFalse(result['findings'][0]['source_quotes'][0]['period_supported'])
        for bad in [{'source_id': True, 'quote': quote}, {'source_id': 2, 'quote': quote},
                    {'source_id': 1, 'quote': 'Выдуманный фрагмент источника'}]:
            changed = copy.deepcopy(candidate)
            changed['findings'][0]['source_quotes'] = [bad]
            with patch('legal_skills.TavilySearch') as search, self.assertRaises(AgentError):
                search.return_value.search.return_value = [source]
                self.run_model(self.payload('claim-audit', research_query='ГК РФ статья 310'), changed)

    def test_no_sources_is_unverifiable_and_does_not_call_model(self):
        with patch('legal_skills.ensure_document_transfer_allowed'), patch('legal_skills.TavilySearch') as search, patch('legal_skills.OpenAICompatibleModel') as model:
            search.return_value.search.side_effect = AgentError('offline')
            result = handle_skill(self.payload('claim-audit', research_query='ГК РФ'))
            model.assert_not_called()
        self.assertEqual(result['status'], 'unverifiable')
        self.assertFalse(result['verified'])

    def test_cloud_policy_applies_before_search_and_model(self):
        with patch('legal_skills.ensure_document_transfer_allowed', side_effect=ValueError('disabled')), patch('legal_skills.TavilySearch') as search:
            with self.assertRaises(ValueError):
                handle_skill(self.payload('claim-audit', research_query='ГК РФ'))
            search.assert_not_called()

    def test_stale_playbook_and_query_are_not_forwarded_to_contract_risk(self):
        with patch('legal_skills.ensure_document_transfer_allowed'), patch('legal_skills.OpenAICompatibleModel') as model, patch('legal_skills.TavilySearch') as search:
            model.return_value.complete.return_value = json.dumps(self.model_result())
            handle_skill(self.payload(playbook='PRIVATE-UNUSED-RULES', research_query='PRIVATE-UNUSED-QUERY'))
            sent = model.return_value.complete.call_args.args[1]
            self.assertNotIn('PRIVATE-UNUSED', sent)
            search.assert_not_called()

    def test_stale_company_context_is_not_forwarded_to_claim_audit(self):
        with patch('legal_skills.ensure_document_transfer_allowed'), patch('legal_skills.OpenAICompatibleModel') as model, patch('legal_skills.TavilySearch') as search:
            model.return_value.complete.return_value = json.dumps(self.model_result())
            search.return_value.search.return_value = [Source('Synthetic', 'https://example.org', DOCUMENT, '')]
            handle_skill(self.payload('claim-audit', research_query='ГК РФ',
                                      user_position='PRIVATE-UNUSED-POSITION', playbook='PRIVATE-UNUSED-RULES'))
            self.assertNotIn('PRIVATE-UNUSED', model.return_value.complete.call_args.args[1])
            search.return_value.search.assert_called_once_with('ГК РФ', 5)

    def test_unknown_skills_paths_foreign_law_and_types_rejected(self):
        for extra in [{'skill_id': '../secret'}, {'skill_id': []}, {'jurisdiction': 'US'},
                      {'demo': 'false'}, {'playbook': []}, {'url': 'https://example.org'},
                      {'user_position': 'x' * 501}, {'as_of': '2026-02-30'}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                handle_skill(self.payload(demo=True) | extra)

    def test_modified_adapter_and_original_rejected(self):
        for key in ('adapter_file', 'original_file'):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copytree(ROOT / 'skills', root / 'skills')
                shutil.copytree(ROOT / 'third_party', root / 'third_party')
                row = json.loads((root / 'skills/manifest.json').read_text())['skills'][0]
                (root / row[key]).write_text('altered')
                with self.assertRaises(ValueError):
                    load_skills(root)

    def test_schema_advertises_auth_cancellation_and_closed_input(self):
        operation = openapi_spec(require_auth=True)['paths']['/legal/skills/run']['post']
        self.assertTrue(operation['security'])
        self.assertIn('504', operation['responses'])
        self.assertFalse(operation['requestBody']['content']['application/json']['schema']['additionalProperties'])
