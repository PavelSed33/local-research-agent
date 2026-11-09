"""Controlled document discovery regressions; no semantic/legal accuracy claim."""
import unittest

from services_retrieval import related_terms, retrieve
import test_workspace_store as store_tests
from workspace_store import WorkspaceError


CONTROLS = [
    ('стоимость', 'Цена услуг составляет сто тысяч рублей.'),
    ('предоплата', 'Аванс составляет половину согласованной суммы.'),
    ('расторгнуть', 'Заказчик направляет отказ от исполнения обязательств.'),
    ('штраф', 'Неустойка начисляется за каждый день просрочки.'),
    ('уведомление', 'Извещение направляется на согласованный адрес.'),
    ('приёмка', 'Акт направляется заказчику для подписания.'),
    ('платёж', 'Оплата производится после передачи отчёта.'),
    ('дедлайн', 'Срок оказания услуг составляет десять дней.'),
    ('прекратить', 'Условие об отказе требует отдельной проверки.'),
    ('тариф', 'Стоимость составляет сто тысяч рублей.'),
    ('пени', 'Штраф рассчитывается согласно условиям сторон.'),
    ('извещение', 'Уведомление направляется на согласованный адрес.'),
]


class ServicesRetrievalTests(unittest.TestCase):
    setUp = store_tests.WorkspaceStoreTests.setUp

    def add(self, text, matter=None, **kwargs):
        return self.store.add_document(self.owner, matter or self.matter,
            {'title': 'СИНТЕТИЧЕСКИЙ поисковый контроль', 'document': text}, **kwargs)['document_id']

    def test_related_word_controls_improve_target_discovery_without_external_calls(self):
        baseline_hits = expanded_hits = 0
        for index, (query, content) in enumerate(CONTROLS):
            matter = self.store.create_matter(self.owner, f'СИНТЕТИЧЕСКИЙ контроль {index}')['matter_id']
            target = self.add(content, matter)
            baseline = self.store.search(self.owner, matter, query)['matches']
            expanded = retrieve(self.store, self.owner, matter, query, '2025-06-30')
            baseline_hits += any(row['document_id'] == target for row in baseline)
            expanded_hits += any(row['document_id'] == target for row in expanded['matches'])
            self.assertFalse(expanded['retrieval']['semantic_search'])
            self.assertFalse(expanded['retrieval']['complete_search'])
            self.assertTrue(expanded['retrieval']['related_search_terms'])
        self.assertEqual(baseline_hits, 0)
        self.assertEqual(expanded_hits, len(CONTROLS))

    def test_long_document_cannot_crowd_out_a_matching_letter(self):
        long = self.add(('Оплата ' * 160 + 'условие. ') * 60)
        letter = self.add('Прочие сведения. ' * 150 + 'Оплата удержана из-за замечаний к услугам.')
        baseline = self.store.search(self.owner, self.matter, 'Оплата')['matches']
        self.assertEqual({row['document_id'] for row in baseline}, {long})
        expanded = retrieve(self.store, self.owner, self.matter, 'Оплата', '2025-06-30')
        self.assertEqual({row['document_id'] for row in expanded['matches']}, {long, letter})

    def test_original_word_matches_keep_priority_over_related_term_only_matches(self):
        exact = self.add('Стоимость услуг составляет сто тысяч рублей.')
        self.add('Цена услуг составляет сто тысяч рублей.')
        result = retrieve(self.store, self.owner, self.matter, 'Стоимость', '2025-06-30')
        self.assertEqual(result['matches'][0]['document_id'], exact)

    def test_future_decisions_are_removed_before_final_context_selection(self):
        future = self.add('Аванс возвращён согласно учебному судебному решению.',
                          provenance={'decision_date': '2025-07-01'})
        current = self.add('Предоплата обсуждается в переписке сторон.')
        result = retrieve(self.store, self.owner, self.matter, 'Предоплата', '2025-06-30')
        self.assertEqual({row['document_id'] for row in result['matches']}, {current})
        self.assertNotIn(future, str(result['matches']))
        self.assertEqual(result['future_candidates_excluded'], 1)

    def test_scoping_and_access_are_enforced_for_each_related_search(self):
        other = self.store.create_matter(self.owner, 'Другой проект')['matter_id']
        foreign = self.add('Аванс содержит SECRET OTHER MATTER.', other)
        local = self.add('Предоплата относится к выбранному делу.')
        result = retrieve(self.store, self.owner, self.matter, 'Предоплата', '2025-06-30')
        self.assertEqual({row['document_id'] for row in result['matches']}, {local})
        self.assertNotIn(foreign, str(result))
        self.assertNotIn('SECRET', str(result))
        with self.assertRaises(WorkspaceError):
            retrieve(self.store, self.other, self.matter, 'Предоплата', '2025-06-30')

    def test_offsets_hashes_and_source_text_stay_bound_after_fusion(self):
        self.add('Длинное вступление. ' * 80 + 'Аванс составляет половину суммы.')
        result = retrieve(self.store, self.owner, self.matter, 'Предоплата', '2025-06-30')
        for row in result['matches']:
            doc = self.store.document(self.owner, row['document_id'])
            self.assertEqual(doc['text'][row['start']:row['end']], row['text'])
            self.assertEqual(doc['sha256'], row['document_sha256'])

    def test_unknown_terms_do_not_invent_related_queries(self):
        self.assertEqual(related_terms('архив переписки'), [])
        result = retrieve(self.store, self.owner, self.matter, 'архив переписки', '2025-06-30')
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['retrieval']['related_search_terms'], [])

    def test_related_terms_and_final_context_are_bounded(self):
        terms = related_terms('цена отказ аванс штраф извещение акт платеж дедлайн')
        self.assertLessEqual(len(terms), 12)
        for index in range(16):
            self.add(f'Аванс по синтетическому документу номер {index} требует проверки.')
        result = retrieve(self.store, self.owner, self.matter, 'Предоплата', '2025-06-30')
        self.assertLessEqual(len(result['matches']), 8)
        self.assertEqual(len({row['document_id'] for row in result['matches']}), 8)
        self.assertIn('не означают юридическую равнозначность', result['retrieval']['limitation'])
        for index in range(81):
            self.add(f'Аванс по дополнительному поисковому контролю {index} требует проверки.')
        capped = retrieve(self.store, self.owner, self.matter, 'Предоплата', '2025-06-30')
        self.assertTrue(capped['retrieval']['candidate_limit_reached'])
        self.assertFalse(capped['retrieval']['complete_search'])
        self.assertLessEqual(len(capped['matches']), 8)
