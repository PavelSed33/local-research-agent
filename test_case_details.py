"""Literal court evidence guards; synthetic text is not a legally approved benchmark."""
import json
import unittest
from unittest.mock import patch

from agent import Source
from case_law import handle_case_law, validate_decisions

HEADER = 'Условный суд 01.03.2023 дело А00-123/2023.'
CONTEXT = HEADER + ' ОПРЕДЕЛЕНИЕ. Кассационная инстанция. Жалобу удовлетворить частично. Спор о неоплаченных работах.'


def record():
    return {'source_id': 1, 'court': 'Условный суд', 'case_number': 'А00-123/2023',
            'decision_date': '2023-03-01', 'quote': HEADER}


def source(text=CONTEXT):
    return Source('Технический пример', 'https://example.org/case', text, '')


class CourtDetailTests(unittest.TestCase):
    def validate(self, value, sources=None, as_of=None):
        return validate_decisions([value], sources or [source()], as_of=as_of)

    def test_missing_fields_and_appeal_history_mean_unknown(self):
        accepted, rejected = self.validate(record())
        self.assertFalse(rejected)
        act = accepted[0]
        for key in ('act_type', 'instance', 'outcome', 'case_facts'):
            self.assertEqual(act[key]['status'], 'not_established')
            self.assertIsNone(act[key]['quote'])
        self.assertEqual(act['material_type'], 'unknown')
        self.assertEqual(act['subsequent_history']['status'], 'not_established')
        self.assertFalse(act['subsequent_history']['complete'])
        self.assertEqual(act['comparability']['status'], 'not_assessed')
        self.assertIsNone(act['temporal_evidence']['publication_date'])
        self.assertEqual(act['temporal_evidence']['historical_availability'], 'not_established')

    def test_literal_details_preserve_full_excerpts_without_model_summaries(self):
        value = record()
        for key, quote in [('act_type', 'ОПРЕДЕЛЕНИЕ'), ('instance', 'Кассационная инстанция'),
                           ('outcome', 'Жалобу удовлетворить частично.'), ('case_facts', 'Спор о неоплаченных работах.')]:
            value[key] = {'quote': quote, 'context_quote': CONTEXT, 'value': 'Иск удовлетворён полностью', 'verified': True}
        accepted, rejected = self.validate(value)
        self.assertFalse(rejected)
        act = accepted[0]
        self.assertEqual(act['material_type'], 'individual_act_candidate')
        self.assertEqual(act['outcome']['quote'], 'Жалобу удовлетворить частично.')
        self.assertNotIn('value', act['outcome'])
        self.assertFalse(act['outcome']['verified'])
        quote = act['outcome']
        self.assertEqual(CONTEXT[quote['quote_offset']:quote['quote_offset'] + len(quote['quote'])], quote['quote'])

    def test_quote_in_other_case_does_not_fill_current_outcome(self):
        other = 'Другой суд 02.03.2023 дело А00-999/2023. Иск удовлетворить.'
        value = record()
        value['outcome'] = {'quote': 'Иск удовлетворить.', 'context_quote': other}
        accepted, rejected = self.validate(value, [source(CONTEXT + '\n' + other)])
        self.assertTrue(rejected)
        self.assertEqual(accepted[0]['outcome']['status'], 'not_established')

    def test_fabricated_or_unbound_optional_claim_does_not_destroy_metadata(self):
        for claim in [{'quote': 'Выдуманный исход', 'context_quote': CONTEXT},
                      {'quote': 'Жалобу удовлетворить частично.', 'context_quote': 'Жалобу удовлетворить частично.'},
                      {'quote': 'ОПРЕДЕЛЕНИЕ', 'context_quote': CONTEXT, 'source_id': 2},
                      {'quote': 'ОПРЕДЕЛЕНИЕ', 'context_quote': CONTEXT, 'source_id': True}, 'кассация']:
            with self.subTest(claim=claim):
                value = record()
                value['instance'] = claim
                accepted, rejected = self.validate(value)
                self.assertEqual(len(accepted), 1)
                self.assertTrue(rejected)
                self.assertEqual(accepted[0]['instance']['status'], 'not_established')

    def event_data(self):
        context = HEADER + ' Следующий условный суд 01.04.2023: определение Условного суда от 01.03.2023 по делу А00-123/2023 оставить без изменения.'
        event = {'source_id': 2, 'court': 'Следующий условный суд', 'event_date': '2023-04-01',
                 'quote': 'оставить без изменения.', 'context_quote': context, 'verified': True}
        return event, [source(), source(context)]

    def test_later_event_excluded_before_date_and_retained_on_date(self):
        event, sources = self.event_data()
        value = {**record(), 'subsequent_history': [event]}
        before, _ = self.validate(value, sources, '2023-03-31')
        on, rejected = self.validate(value, sources, '2023-04-01')
        after, _ = self.validate(value, sources, '2023-04-02')
        self.assertEqual(before[0]['subsequent_history']['events'], [])
        self.assertEqual(before[0]['subsequent_history']['later_events_excluded'], 1)
        self.assertFalse(rejected)
        for result in (on, after):
            history = result[0]['subsequent_history']
            self.assertEqual(len(history['events']), 1)
            self.assertFalse(history['events'][0]['verified'])
            self.assertFalse(history['complete'])

    def test_event_without_link_to_original_act_is_rejected(self):
        event, sources = self.event_data()
        event['context_quote'] = 'Следующий условный суд 01.04.2023 оставить без изменения.'
        sources[1] = source(event['context_quote'])
        accepted, rejected = self.validate({**record(), 'subsequent_history': [event]}, sources)
        self.assertTrue(rejected)
        self.assertEqual(accepted[0]['subsequent_history']['events'], [])

    def test_invalid_earlier_noncanonical_dates_and_boolean_source_ids_rejected(self):
        for changes in [{'event_date': '2023-02-01'}, {'event_date': '2023-4-1'},
                        {'event_date': 'not a date'}, {'source_id': True}, {'court': []}]:
            event, sources = self.event_data()
            event.update(changes)
            with self.subTest(changes=changes):
                accepted, rejected = self.validate({**record(), 'subsequent_history': [event]}, sources)
                self.assertTrue(rejected)
                self.assertEqual(accepted[0]['subsequent_history']['events'], [])

    def test_model_cannot_claim_no_appeals_finality_or_comparable_facts(self):
        value = {**record(), 'verified': True, 'lawyer_reviewed': True, 'in_force': True,
                 'subsequent_history': [], 'comparability': {'status': 'identical', 'verified': True}}
        accepted, _ = self.validate(value)
        act = accepted[0]
        self.assertFalse(act['verified'])
        self.assertFalse(act['lawyer_reviewed'])
        self.assertNotIn('in_force', act)
        self.assertEqual(act['subsequent_history']['status'], 'not_established')
        self.assertEqual(act['comparability']['status'], 'not_assessed')

    def test_non_case_materials_separate_from_selected_decisions(self):
        for label in ('Постановление Пленума', 'Обзор судебной практики'):
            text = HEADER + ' ' + label
            value = {**record(), 'act_type': {'quote': label, 'context_quote': text}}
            with self.subTest(label=label), patch('case_law.TavilySearch') as search, patch('case_law.OpenAICompatibleModel') as model:
                search.return_value.search.return_value = [source(text)]
                model.return_value.complete.return_value = json.dumps({'decisions': [value]})
                result = handle_case_law({'question': 'Технический пример', 'as_of': '2023-03-01'})
                self.assertEqual(result['decisions'], [])
                self.assertEqual(len(result['non_case_materials']), 1)
                self.assertEqual(result['practice_status'], 'not_established')

    def test_no_sources_does_not_call_model_and_demo_has_same_non_case_container(self):
        with patch('case_law.TavilySearch') as search, patch('case_law.OpenAICompatibleModel') as model:
            search.return_value.search.return_value = []
            result = handle_case_law({'question': 'Тест'})
            model.assert_not_called()
            self.assertEqual(result['non_case_materials'], [])
        self.assertEqual(handle_case_law({'question': 'Тест', 'demo': True})['non_case_materials'], [])

    def test_overflow_is_reported_not_presented_as_complete(self):
        accepted, rejected = validate_decisions([record()] * 21, [source()])
        self.assertEqual(len(accepted), 20)
        self.assertTrue(rejected)

    def test_case_number_and_date_prefixes_do_not_match_different_identifiers(self):
        for changed in [HEADER.replace('А00-123/2023', 'А00-123/20230'),
                        HEADER.replace('А00-123/2023', 'БА00-123/2023'),
                        HEADER.replace('01.03.2023', '01.03.20230')]:
            value = {**record(), 'quote': changed}
            accepted, rejected = self.validate(value, [source(changed)])
            self.assertFalse(accepted)
            self.assertTrue(rejected)

    def test_search_snapshot_hash_and_text_make_quote_check_reproducible(self):
        import hashlib
        with patch('case_law.TavilySearch') as search, patch('case_law.OpenAICompatibleModel') as model:
            search.return_value.search.return_value = [source()]
            model.return_value.complete.return_value = json.dumps({'decisions': [record()]})
            result = handle_case_law({'question': 'Технический пример', 'as_of': '2023-03-01'})
        snapshot = result['sources'][0]
        self.assertEqual(snapshot['text'], CONTEXT)
        self.assertEqual(snapshot['text_sha256'], hashlib.sha256(CONTEXT.encode()).hexdigest())
        self.assertFalse(snapshot['original_bytes_archived'])
        self.assertIn(result['decisions'][0]['quote'], snapshot['text'])
