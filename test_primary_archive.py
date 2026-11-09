import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from primary_sources import ROOT, PrimarySourceError, load_sources, original_bytes, source_catalog
from workspace_api import dispatch
from workspace_store import WorkspaceStore, WorkspaceError


class PrimaryArchiveValidationTests(unittest.TestCase):
    def mutation(self, change):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT, root, dirs_exist_ok=True)
            path = root / 'manifest.json'
            data = json.loads(path.read_text())
            change(data)
            path.write_text(json.dumps(data))
            with self.assertRaises(PrimarySourceError) as caught:
                load_sources(root)
            self.assertNotIn(str(root), str(caught.exception))

    def test_three_physical_originals_have_identity_and_page_maps_without_legal_acceptance(self):
        sources = source_catalog()
        self.assertEqual(len(sources['sources']), 3)
        self.assertEqual(sum(row['page_count'] for row in sources['sources']), 59)
        self.assertEqual({row['document_number'] for row in sources['sources']}, {'49', '7', '54'})
        self.assertFalse(sources['legal_acceptance'])
        for row in sources['sources']:
            self.assertNotIn('text', row)
            self.assertFalse(row['history_complete'])
            self.assertFalse(row['current_validity_verified'])

    def test_schema_duplicates_paths_and_urls_fail_closed(self):
        for change in [lambda data: data.update(schema_version=True),
                       lambda data: data['sources'].append(copy.deepcopy(data['sources'][0])),
                       lambda data: data['sources'][0].update(original_file='../outside.pdf'),
                       lambda data: data['sources'][0].update(source_url='https://vsrf.ru.evil.example/file.pdf'),
                       lambda data: data['sources'][0].update(source_url='https://user:secret@www.vsrf.ru/file.pdf'),
                       lambda data: data['sources'][0].update(original_sha256='invalid')]:
            self.mutation(change)

    def test_identity_and_trust_promotions_are_rejected(self):
        for change in [lambda data: data['sources'][0].update(signed_on='2018-12-24'),
                       lambda data: data['sources'][0].update(document_number='999'),
                       lambda data: data['sources'][0].update(identity_terms=['Неверное название документа', '25 декабря 2018 г.']),
                       lambda data: data['sources'][0].update(lawyer_reviewed=True),
                       lambda data: data['sources'][0].update(current_validity_verified=True)]:
            self.mutation(change)

    def test_page_map_incomplete_boolean_and_gaps_are_rejected(self):
        for change in [lambda data: data['sources'][0]['pages'].pop(),
                       lambda data: data['sources'][0]['pages'][0].update(page=True),
                       lambda data: data['sources'][0]['pages'][0].update(start=10),
                       lambda data: data['sources'][0]['pages'][0].update(end=10000000)]:
            self.mutation(change)

    def test_header_amendment_mention_is_literal_not_complete_history(self):
        row = next(row for row in load_sources() if row['document_number'] == '7')
        mention = row['amendment_mentions'][0]
        self.assertEqual(mention['signed_on'], '2017-02-07')
        self.assertIn(mention['quote'], row['text'])
        self.assertFalse(row['history_complete'])
        self.mutation(lambda data: data['sources'][1]['amendment_mentions'][0].update(quote='Выдуманная цитата об изменении документа.'))

    def test_symlink_and_oversized_manifest_are_rejected(self):
        for mode in ('symlink', 'large'):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'manifest.json'
                if mode == 'symlink':
                    path.symlink_to(ROOT / 'manifest.json')
                else:
                    path.write_text(' ' * 262145)
                with self.assertRaises(PrimarySourceError):
                    load_sources(directory)

    def test_original_is_rechecked_after_catalog_load(self):
        with tempfile.TemporaryDirectory() as directory:
            shutil.copytree(ROOT, directory, dirs_exist_ok=True)
            row = load_sources(directory)[0]
            (Path(directory) / row['original_file']).write_bytes(b'%PDF changed')
            with self.assertRaises(PrimarySourceError):
                original_bytes(row, directory)


class PrimaryArchiveImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = WorkspaceStore(Path(self.directory.name) / 'workspace.db')
        self.owner = self.store.create_user('Admin', 'admin')['user_id']
        self.matter = self.store.create_matter(self.owner, 'Синтетическое дело')['matter_id']

    def do_import(self, identity='vsrf-plenum-49-2018', matter=None, user=None):
        return dispatch(self.store, user or self.owner, '/workspace/sources/import',
                        {'matter_id': matter or self.matter, 'source_id': identity})

    def test_concurrent_retries_reuse_one_original_and_one_index(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.do_import(), range(8)))
        self.assertEqual(len({row['document_id'] for row in results}), 1)
        self.assertEqual(sum(row['reused'] for row in results), 7)
        self.assertEqual(len(self.store.documents(self.owner, self.matter)['documents']), 1)
        self.assertTrue(self.store.verify_audit())

    def test_deduplication_is_scoped_to_matter_and_preserves_write_permissions(self):
        first = self.do_import()
        second_matter = self.store.create_matter(self.owner, 'Другое дело')['matter_id']
        second = self.do_import(matter=second_matter)
        self.assertNotEqual(first['document_id'], second['document_id'])
        user = self.store.create_user('Viewer', actor=self.owner)['user_id']
        self.store.grant(self.owner, self.matter, user, 'viewer')
        with self.assertRaises(WorkspaceError):
            self.do_import(user=user)

    def test_reimport_rejects_corrupted_original_instead_of_reusing_it(self):
        first = self.do_import()
        with self.store.connect(True) as db:
            db.execute('DROP TRIGGER originals_no_update')
            db.execute('UPDATE originals SET bytes=? WHERE document_id=?', (b'corrupt', first['document_id']))
        with self.assertRaises(WorkspaceError) as caught:
            self.do_import()
        self.assertEqual(caught.exception.status, 503)
