"""Regressions for the confirmed audit defects; no real legal conclusions."""
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request

from agent import Source, find_unconfirmed_articles, validate_report, build_validated_report, AgentError
from api import create_server, openapi_spec
from extract import extract_entities, handle_extract, validate_extraction
from review import review_contract, handle_review, MAX_DOCUMENT_CHARS
from test_review import DOCUMENT as REVIEW_DOCUMENT, VALID_RESULT as REVIEW_RESULT
from test_extract import DOCUMENT as EXTRACT_DOCUMENT, VALID_RESULT as EXTRACT_RESULT


def copy(value):
    return json.loads(json.dumps(value))


def report(claim):
    return '\n\n'.join('## ' + name + '\n\n' + text for name, text in [
        ('Краткий ответ', claim), ('Правовое основание', claim), ('Актуальность и изменения', 'Условный материал [1].'),
        ('Как применить к ситуации', 'Технический пример.'), ('Ограничения', 'Это не правовой вывод.')])


class CitationBindingTests(unittest.TestCase):
    def setUp(self):
        self.sources = [Source('Другой материал', 'https://example.org/1', 'Нет проверяемой статьи.', ''),
                        Source('ГК РФ', 'https://example.org/2', 'Статья 432. Условный технический текст.', '')]

    def test_original_wrong_source_reproduction_is_rejected(self):
        value = report('Статья 432 ГК РФ: условный вывод [1].')
        self.assertTrue(validate_report(value, self.sources))
        self.assertTrue(find_unconfirmed_articles(value, self.sources))

    def test_actual_cited_source_supports_article(self):
        self.assertEqual(validate_report(report('Статья 432 ГК РФ: условный вывод [2].'), self.sources), [])

    def test_citation_from_other_sentence_paragraph_or_bullet_cannot_be_borrowed(self):
        for text in ['Статья 432 ГК РФ. Другой текст [2].',
                     'Статья 432 ГК РФ [1]. Другая фраза [2].',
                     'Статья 432 ГК РФ\n\nДругой абзац [2].',
                     '- Статья 432 ГК РФ\n- Другой пункт [2].']:
            with self.subTest(text=text):
                self.assertTrue(find_unconfirmed_articles(text, self.sources))

    def test_trailing_citation_after_period_is_attached_to_its_sentence(self):
        self.assertEqual(find_unconfirmed_articles('Статья 432 ГК РФ. [2]', self.sources), [])
        self.assertEqual(find_unconfirmed_articles('«Статья 432 ГК РФ.» [2]', self.sources), [])

    def test_decimal_article_and_abbreviations_are_not_sentence_boundaries(self):
        sources = [Source('ГК РФ', 'https://example.org', 'Ст. 432.1 ГК РФ: условный текст.', '')]
        self.assertEqual(find_unconfirmed_articles('Применяется ст. 432.1 ГК РФ [1].', sources), [])
        self.assertTrue(find_unconfirmed_articles('Применяется ст. 432 ГК РФ [1].', sources))

    def test_multiple_laws_in_one_segment_require_disambiguation(self):
        sources = [Source('ГК РФ', 'https://example.org', 'Статья 15. Условный текст.', '')]
        self.assertTrue(find_unconfirmed_articles('Статья 15 ГК РФ и статья 15 ТК РФ [1].', sources))

    def test_each_article_must_be_supported_by_its_cited_group(self):
        sources = [Source('ГК РФ', 'https://example.org/1', 'Статья 432. Условный текст.', ''),
                   Source('ГК РФ', 'https://example.org/2', 'Статья 433. Другой условный текст.', '')]
        self.assertEqual(find_unconfirmed_articles('Статья 432 и статья 433 ГК РФ [1][2].', sources), [])
        self.assertTrue(find_unconfirmed_articles('Статья 432 и статья 433 ГК РФ [1].', sources))

    def test_missing_or_out_of_range_citation_cannot_fall_back_to_other_sources(self):
        for suffix in ['', '[0]', '[3]']:
            with self.subTest(suffix=suffix):
                self.assertTrue(find_unconfirmed_articles('Статья 432 ГК РФ ' + suffix, self.sources))

    def test_repair_must_fix_source_binding_or_fail(self):
        wrong, right = report('Статья 432 ГК РФ [1].'), report('Статья 432 ГК РФ [2].')
        model = unittest.mock.Mock()
        model.complete.side_effect = [wrong, right]
        result, repaired = build_validated_report(model, 'Технический пример', self.sources)
        self.assertTrue(repaired)
        self.assertEqual(result, right)
        model.complete.side_effect = [wrong, wrong]
        with self.assertRaises(AgentError):
            build_validated_report(model, 'Технический пример', self.sources)


class ModelFlagPolicyTests(unittest.TestCase):
    def poisoned_review(self):
        result = copy(REVIEW_RESULT)
        result.update(verified=True, lawyer_reviewed=True, signed_opinion={'verified': True})
        result['risks'][0].update(verified=True, lawyer_reviewed=True, signed_opinion={'approved': True},
                                  legal_basis=[{'verified': True, 'quote': 'выдуманная норма'}])
        return result

    def poisoned_extraction(self):
        result = copy(EXTRACT_RESULT)
        result.update(verified=True, lawyer_reviewed=True)
        for section in ('parties', 'amounts', 'dates', 'penalties'):
            for entry in result[section]:
                entry.update(verified=True, lawyer_reviewed=True, legal_basis={'verified': True})
        for section in ('term', 'jurisdiction'):
            result[section].update(verified=True, lawyer_reviewed=True, signed_opinion={'verified': True})
        return result

    def test_legacy_review_whitelists_risks_and_discards_legal_approval(self):
        model = unittest.mock.Mock()
        model.complete.return_value = json.dumps(self.poisoned_review())
        result, repaired = review_contract(model, REVIEW_DOCUMENT)
        self.assertFalse(repaired)
        self.assertFalse(result['verified'])
        self.assertNotIn('signed_opinion', result)
        risk = result['risks'][0]
        self.assertFalse(risk['verified'])
        self.assertFalse(risk['lawyer_reviewed'])
        self.assertNotIn('signed_opinion', risk)
        self.assertEqual(risk['legal_basis'], [])
        self.assertEqual(risk['basis_status'], 'no_legal_evidence')

    def test_repaired_legacy_review_uses_the_same_policy(self):
        invalid = self.poisoned_review()
        invalid['risks'][0]['quote'] = 'Выдуманная фраза для проверки исправления.'
        model = unittest.mock.Mock()
        model.complete.side_effect = [json.dumps(invalid), json.dumps(self.poisoned_review())]
        result, repaired = review_contract(model, REVIEW_DOCUMENT)
        self.assertTrue(repaired)
        self.assertFalse(result['risks'][0]['verified'])
        self.assertEqual(result['risks'][0]['legal_basis'], [])

    def test_entities_and_nested_objects_cannot_self_certify(self):
        model = unittest.mock.Mock()
        model.complete.return_value = json.dumps(self.poisoned_extraction())
        result, repaired = extract_entities(model, EXTRACT_DOCUMENT)
        self.assertFalse(repaired)
        self.assertFalse(result['verified'])
        for section in ('parties', 'amounts', 'dates', 'penalties'):
            for entry in result[section]:
                self.assertFalse(entry['verified'])
                self.assertFalse(entry['lawyer_reviewed'])
                self.assertNotIn('legal_basis', entry)
        for section in ('term', 'jurisdiction'):
            self.assertFalse(result[section]['verified'])
            self.assertNotIn('signed_opinion', result[section])

    def test_repaired_extraction_uses_the_same_flag_policy(self):
        invalid = self.poisoned_extraction()
        invalid['parties'][0]['quote'] = 'Выдуманная фраза в извлечении.'
        model = unittest.mock.Mock()
        model.complete.side_effect = [json.dumps(invalid), json.dumps(self.poisoned_extraction())]
        result, repaired = extract_entities(model, EXTRACT_DOCUMENT)
        self.assertTrue(repaired)
        self.assertFalse(result['parties'][0]['verified'])
        self.assertNotIn('legal_basis', result['parties'][0])

    def test_non_string_quotes_and_nested_values_do_not_pass_extraction(self):
        for section, key, value in [('term', 'duration', {'verified': True}),
                                    ('jurisdiction', 'court', {'verified': True}), ('term', 'quote', 12345678901234567890)]:
            result = copy(EXTRACT_RESULT)
            result[section][key] = value
            self.assertTrue(validate_extraction(result, EXTRACT_DOCUMENT))

    def test_oversized_documents_fail_before_transfer_or_model(self):
        for function, prefix in [(handle_review, 'review'), (handle_extract, 'extract')]:
            with self.subTest(route=prefix), patch(prefix + '.OpenAICompatibleModel') as model, patch(prefix + '.ensure_document_transfer_allowed') as transfer:
                with self.assertRaises(ValueError):
                    function({'document': 'x' * (MAX_DOCUMENT_CHARS + 1)})
                model.assert_not_called()
                transfer.assert_not_called()

    def test_http_legacy_routes_and_openapi_reflect_actual_auth_mode(self):
        for token in ('regression-token', ''):
            with self.subTest(token=bool(token)), tempfile.TemporaryDirectory() as directory:
                server = create_server('127.0.0.1', 0, directory, token)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f'http://127.0.0.1:{server.server_address[1]}'
                    with urllib.request.urlopen(base + '/openapi.json', timeout=5) as response:
                        spec = json.load(response)
                    self.assertEqual(spec['components']['securitySchemes']['ApiToken']['scheme'], 'bearer')
                    for route in ('/ask', '/review', '/extract', '/review/legal'):
                        self.assertEqual(spec['paths'][route]['post']['security'], [{'ApiToken': []}] if token else [])
                    for route, module, payload, model_output in [('/review', 'review', REVIEW_DOCUMENT, self.poisoned_review()),
                                                                ('/extract', 'extract', EXTRACT_DOCUMENT, self.poisoned_extraction())]:
                        headers = {'Content-Type': 'application/json'}
                        if token:
                            headers['Authorization'] = 'Bearer ' + token
                        request = urllib.request.Request(base + route, data=json.dumps({'document': payload, 'demo': True}).encode(), headers=headers)
                        with patch(module + '.Demo' + ('ReviewModel' if module == 'review' else 'ExtractModel')) as demo_model:
                            demo_model.return_value.complete.return_value = json.dumps(model_output)
                            with urllib.request.urlopen(request, timeout=5) as response:
                                result = json.load(response)
                        self.assertFalse(result['verified'])
                        nested = result['risks'][0] if module == 'review' else result['parties'][0]
                        self.assertFalse(nested['verified'])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)
