import json
import unittest
from unittest.mock import patch

from agent import AgentError
from matter_research import answer


class MatterResearchTests(unittest.TestCase):
    def test_archival_sources_keep_date_limitations_in_result_and_model_prompt(self):
        payload = self.payload(False)
        payload['sources'][0]['source_context'] = {'origin': 'pinned_primary_archive',
            'captured_on': '2026-11-09', 'edition_label': 'История после 2017 не подтверждена.', 'current_validity_verified': False}
        result = {'summary': 'Черновик', 'findings': []}
        with patch.dict('os.environ', {'LLM_BASE_URL': 'http://127.0.0.1:9000/v1'}), patch('matter_research.OpenAICompatibleModel') as model:
            model.return_value.complete.return_value = json.dumps(result)
            response = answer(payload)
            system, user = model.return_value.complete.call_args.args
        self.assertTrue(response['source_warnings'])
        self.assertIn('не называй действующими', system)
        self.assertIn('История после 2017 не подтверждена.', user)
        self.assertFalse(response['legal_acceptance'])
    def payload(self, demo=True):
        return {'matter_id': 'synthetic', 'question': 'Когда оплата?', 'demo': demo, 'allow_cloud': False,
                'sources': [{'source_id': 1, 'document_id': 'doc1', 'title': 'Договор',
                             'document_sha256': 'a' * 64, 'text': 'Оплата после передачи отчёта.', 'start': 50, 'end': 79}]}

    def test_demo_is_local_and_citations_keep_document_offsets(self):
        with patch('matter_research.OpenAICompatibleModel', side_effect=AssertionError('network')):
            result = answer(self.payload())
        self.assertEqual(result['findings'][0]['citations'][0]['start'], 50)
        self.assertFalse(result['legal_acceptance'])

    def test_working_mode_blocks_cloud_without_per_request_consent(self):
        with patch.dict('os.environ', {'LLM_BASE_URL': 'https://example.org/v1', 'ALLOW_CLOUD_DOCUMENTS': 'true'}), patch('matter_research.OpenAICompatibleModel') as model:
            with self.assertRaises(ValueError):
                answer(self.payload(False))
            model.assert_not_called()

    def test_fabricated_citation_rejected_and_trust_fields_not_promoted(self):
        result = {'summary': 'Черновик', 'verified': True, 'findings': [{'assessment': 'Проверить', 'citations': [{'source_id': 1, 'quote': 'Оплата после передачи отчёта.'}], 'verified': True}]}
        with patch.dict('os.environ', {'LLM_BASE_URL': 'http://127.0.0.1:9000/v1'}), patch('matter_research.OpenAICompatibleModel') as model:
            model.return_value.complete.return_value = json.dumps(result)
            accepted = answer(self.payload(False))
            self.assertFalse(accepted['verified'])
            self.assertFalse(accepted['findings'][0]['verified'])
            result['findings'][0]['citations'][0]['quote'] = 'Вымышленная цитата документа.'
            model.return_value.complete.return_value = json.dumps(result)
            with self.assertRaises(AgentError):
                answer(self.payload(False))
