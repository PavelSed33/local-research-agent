"""Technical integrity and provenance checks, not approval of statutory editions."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.import_statutes import NormativeHTML, import_corpus
from scripts.fetch_statutes import _public_copy, fetch_article
from statute_corpus import ROOT, corpus_report, handle_corpus, statutory_review_sources, search_statutory_sources
from unittest.mock import patch


class StatutoryCorpusTests(unittest.TestCase):
    def test_quarantined_legal_corpus_is_not_served_as_verified_evidence(self):
        manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('groups'):
            self.skipTest('This test applies only to quarantined corpora')
        report = corpus_report(as_of='2026-11-08', include_text=True)
        self.assertEqual(report['entries'], [])
        self.assertEqual(report['text_received_count'], 0)
        self.assertFalse(report['commercial_use_allowed'])
        self.assertFalse(report['verified'])
        self.assertTrue(any(gap['status'] == 'quarantined_and_removed'
                            for gap in report['refresh_gaps']))
        self.assertEqual(search_statutory_sources('ГК РФ статья 15', '2026-11-08'), [])

    def test_received_texts_cover_the_declared_scope_without_claiming_legal_acceptance(self):
        result = corpus_report(as_of='2026-11-08')
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['text_received_count'], 454)
        registry = json.loads((ROOT.parent / 'legal_coverage.json').read_text())
        self.assertEqual({e['card_id'] for e in result['entries']}, {c['id'] for c in registry['cards']})
        self.assertEqual(len({e['act_id'] for e in result['entries']}), 15)
        for entry in result['entries']:
            self.assertTrue(entry['integrity_checked'])
            self.assertFalse(entry['original_publication'])
            self.assertFalse(entry['raw_response_archived'])
            self.assertFalse(entry['period_supported'])
            self.assertFalse(entry['verified'])
            self.assertNotIn('text', entry)

    def test_text_is_requested_explicitly_and_keeps_article_identity(self):
        result = handle_corpus({'card_ids': ['LAW-02:165.1'], 'as_of': '2026-11-08', 'include_text': True})
        entry = result['entries'][0]
        self.assertIn('Статья 165.1.', entry['text'])
        self.assertEqual(entry['act_id'], 'gk1')
        self.assertTrue(entry['url'].startswith('https://www.consultant.ru/document/cons_doc_LAW_5142/'))

    def mutate(self, callback, *, rehash=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT, root, dirs_exist_ok=True)
            manifest = json.loads((root / 'manifest.json').read_text())
            group = next(g for g in manifest['groups'] if g['act_id'] == 'gk1')
            data = json.loads((root / group['file']).read_text())
            callback(root, group, data)
            path = root / 'gk1.json'
            path.write_text(json.dumps(data, ensure_ascii=False))
            if rehash:
                group['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            (root / 'manifest.json').write_text(json.dumps(manifest))
            return corpus_report(as_of='2026-11-08', corpus_dir=root)

    def test_corrupt_file_is_excluded_without_serving_its_text(self):
        result = self.mutate(lambda root, group, data: data['sections'][0].update(text='Подмена'), rehash=False)
        self.assertTrue(result['errors'])
        self.assertFalse(any(e['act_id'] == 'gk1' for e in result['entries']))

    def test_rehashed_file_still_checks_text_hash_and_exact_act_url(self):
        for changes in [{'text': 'Подменённый текст с достаточным количеством символов.'},
                        {'url': 'https://www.consultant.ru/document/cons_doc_LAW_9027/other/'},
                        {'url': 'https://www.consultant.ru.evil.example/document/'},
                        {'url': 'https://www.consultant.ru:1234/document/'},
                        {'kind': 'official_publication'}, {'act_id': 'gk2'}, {'provision': '100'}]:
            with self.subTest(changes=changes):
                result = self.mutate(lambda root, group, data: data['sections'][0].update(changes))
                self.assertTrue(result['errors'])
                self.assertFalse(any(e['act_id'] == 'gk1' for e in result['entries']))

    def test_path_escape_and_symlink_are_rejected(self):
        for name in ['../legal_coverage.json', '/etc/passwd']:
            with self.subTest(name=name):
                result = self.mutate(lambda root, group, data: group.update(file=name))
                self.assertTrue(result['errors'])
        def link(root, group, data):
            path = root / 'external.json'
            path.symlink_to(Path(__file__).resolve())
            group['file'] = path.name
        self.assertTrue(self.mutate(link)['errors'])

    def test_manifest_flags_cannot_promote_a_copy_to_an_accepted_law(self):
        result = self.mutate(lambda root, group, data: data['sections'][0].update(
            verified=True, original_publication=True, period_supported=True, effective_from='1995-01-01'))
        entry = next(e for e in result['entries'] if e['act_id'] == 'gk1')
        self.assertFalse(entry['verified'])
        self.assertFalse(entry['period_supported'])
        self.assertFalse(entry['original_publication'])
        self.assertNotIn('effective_from', entry)

    def test_later_copy_is_not_injected_into_historical_model_context(self):
        sources, report = statutory_review_sources('consumer', 'services', '2026-11-07')
        self.assertEqual(sources, [])
        self.assertTrue(report['entries'])
        self.assertTrue(all(e['temporal_status'].startswith('later_capture') for e in report['entries']))

    def test_special_and_consumer_articles_are_not_crowded_out_by_general_law(self):
        sources, _ = statutory_review_sources('consumer', 'services', '2026-11-08')
        self.assertEqual(len(sources), 3)
        self.assertTrue(any('cons_doc_LAW_305/' in s.url for s in sources))
        self.assertTrue(any('cons_doc_LAW_9027/' in s.url for s in sources))
        sources, _ = statutory_review_sources('employment', 'general', '2026-11-08')
        self.assertTrue(all('cons_doc_LAW_34683/' in s.url for s in sources))

    def test_invalid_requests_do_not_become_local_file_or_network_operations(self):
        for payload in [{'card_ids': 'LAW-02:15'}, {'card_ids': [True]}, {'card_ids': ['LAW-99:1']},
                        {'include_text': True}, {'include_text': 'yes'}, {'document': 'data'},
                        {'card_ids': ['LAW-02:15'], 'as_of': '2026-02-30'}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                handle_corpus(payload)

    def test_extractor_excludes_editorial_inserts_and_preserves_table_cell_boundaries(self):
        parser = NormativeHTML()
        parser.feed('<div class="document-page__content"><h1>Технический пример</h1>'
                    '<div class="document__insert">Редакционный комментарий</div>'
                    '<table><tr><td>Мера</td><td>Уровень 1</td><td>+</td></tr></table>'
                    '<p>Технический текст для проверки.</p></div><footer>Навигация</footer>')
        text = parser.text()
        self.assertNotIn('Редакционный', text)
        self.assertNotIn('Навигация', text)
        self.assertIn('Мера\tУровень 1\t+', text)

    def test_local_search_keeps_exact_article_and_act_and_historical_cutoff(self):
        result = search_statutory_sources('152-ФЗ статья 9: согласие', '2026-11-08')
        self.assertEqual(len(result), 1)
        self.assertIn('cons_doc_LAW_61801/', result[0].url)
        self.assertIn('Статья 9.', result[0].text)
        self.assertEqual(search_statutory_sources('152-ФЗ статья 9', '2026-11-07'), [])
        self.assertEqual(search_statutory_sources('статья 9 без указания акта', '2026-11-08'), [])
        self.assertEqual(search_statutory_sources('1152-ФЗ статья 9', '2026-11-08'), [])
        result = search_statutory_sources('КоАП статья 13.11', '2026-11-08')
        self.assertEqual(len(result), 1)
        self.assertIn('Статья 13.11.', result[0].text)

    def test_failed_partial_import_preserves_old_texts_and_original_capture_dates(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as staging:
            destination = Path(directory)
            shutil.copytree(ROOT, destination, dirs_exist_ok=True)
            result = import_corpus(Path(staging), destination)
            self.assertEqual(result['received'], 454)
            self.assertEqual(result['gaps'], 454)
            report = corpus_report(corpus_dir=destination)
            self.assertEqual(report['errors'], [])
            self.assertTrue(all(entry['captured_on'] == '2026-11-08' for entry in report['entries']))
            self.assertTrue(all(gap['status'] == 'retained_older_extract' for gap in report['refresh_gaps']))

    def test_refresh_rejects_unpinned_urls_and_does_not_redate_stale_bytes(self):
        for url in ['http://www.consultant.ru/document/cons_doc_LAW_5142/',
                    'https://www.consultant.ru.evil.example/document/cons_doc_LAW_5142/',
                    'https://127.0.0.1/document/cons_doc_LAW_5142/',
                    'https://www.consultant.ru/document/cons_doc_LAW_5142/?redirect=x']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                _public_copy(url)
        entry = corpus_report(['LAW-02:15'])['entries'][0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'gk1').mkdir()
            old = root / 'gk1/15.raw'
            old.write_bytes(b'stale')
            with patch('scripts.fetch_statutes.urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = OSError('offline')
                self.assertEqual(fetch_article(entry, root), 'unavailable')
            self.assertFalse(old.exists())
            self.assertEqual(json.loads((root / 'gk1/15.json').read_text())['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
