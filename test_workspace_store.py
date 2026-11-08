import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from workspace_store import WorkspaceStore, WorkspaceError


class WorkspaceStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'workspace.db'
        self.store = WorkspaceStore(self.path)
        self.admin = self.store.create_user('Администратор', 'admin')
        self.owner = self.admin['user_id']
        self.other = self.store.create_user('Другой участник', actor=self.owner)['user_id']
        self.matter = self.store.create_matter(self.owner, 'Пример дела')['matter_id']

    def document(self, value='Оплата отчёта после подписания акта. PRIVATE-DOCUMENT'):
        return self.store.add_document(self.owner, self.matter, {'title': 'Договор', 'document': value})['document_id']

    def test_tokens_are_not_stored_plaintext_and_survive_restart(self):
        self.assertEqual(WorkspaceStore(self.path).authenticate(self.admin['token'])['id'], self.owner)
        with self.store.connect() as db:
            self.assertNotIn(self.admin['token'], json.dumps([dict(row) for row in db.execute('SELECT * FROM tokens')]))
        self.assertIsNone(self.store.authenticate('wrong-token'))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_bootstrap_and_user_creation_require_administrator(self):
        with self.assertRaises(WorkspaceError):
            self.store.create_user('Second', 'admin')
        with self.assertRaises(WorkspaceError):
            self.store.create_user('Escalation', 'admin', actor=self.other)

    def test_unrelated_user_cannot_read_search_or_list_documents(self):
        document = self.document()
        self.assertEqual(self.store.list_matters(self.other)['matters'], [])
        for action in [lambda: self.store.document(self.other, document),
                       lambda: self.store.search(self.other, self.matter, 'оплата'),
                       lambda: self.store.documents(self.other, self.matter)]:
            with self.assertRaises(WorkspaceError) as caught:
                action()
            self.assertEqual(caught.exception.status, 403)

    def test_viewer_editor_and_revoked_membership(self):
        document = self.document()
        self.store.grant(self.owner, self.matter, self.other, 'viewer')
        self.assertEqual(self.store.document(self.other, document)['id'], document)
        with self.assertRaises(WorkspaceError):
            self.store.add_document(self.other, self.matter, {'title': 'Denied', 'document': 'Текст'})
        self.store.grant(self.owner, self.matter, self.other, 'editor')
        self.store.add_document(self.other, self.matter, {'title': 'Allowed', 'document': 'Текст документа'})
        self.store.grant(self.owner, self.matter, self.other, 'remove')
        with self.assertRaises(WorkspaceError):
            self.store.document(self.other, document)

    def test_revoke_user_invalidates_all_access(self):
        user = self.store.create_user('Пользователь', actor=self.owner)
        self.store.revoke_user(self.owner, user['user_id'])
        self.assertIsNone(self.store.authenticate(user['token']))
        with self.assertRaises(WorkspaceError):
            self.store.create_matter(user['user_id'], 'Forbidden')

    def test_token_rotation_keeps_user_and_revokes_old_token(self):
        result = self.store.rotate_token(self.owner)
        self.assertIsNone(self.store.authenticate(self.admin['token']))
        self.assertEqual(self.store.authenticate(result['token'])['id'], self.owner)
        self.assertTrue(self.store.verify_audit())

    def test_owner_transfer_is_required_before_owner_revocation(self):
        self.store.grant(self.owner, self.matter, self.other, 'viewer')
        self.store.transfer_matter(self.owner, self.matter, self.other)
        with self.assertRaises(ValueError):
            self.store.revoke_user(self.owner, self.other)
        with self.assertRaises(WorkspaceError):
            self.store.grant(self.owner, self.matter, self.other, 'editor')

    def test_search_citations_match_original_offsets_and_matter_filter(self):
        self.document('Вступление. ' * 100 + 'Оплата после подписания акта. PRIVATE-CONTEXT')
        other_matter = self.store.create_matter(self.other, 'Чужое дело')['matter_id']
        self.store.add_document(self.other, other_matter, {'title': 'Secret', 'document': 'Оплата чужая SECRET-OTHER'})
        result = self.store.search(self.owner, self.matter, 'оплат')
        self.assertTrue(result['matches'])
        for row in result['matches']:
            source = self.store.document(self.owner, row['document_id'])
            self.assertEqual(source['text'][row['start']:row['end']], row['text'])
        self.assertNotIn('SECRET-OTHER', json.dumps(result))
        self.store.search(self.owner, self.matter, '" OR NOT * SELECT')

    def test_documents_are_immutable_and_audit_is_append_only_without_private_text(self):
        self.document()
        events = self.store.audit(self.owner)['events']
        self.assertNotIn('PRIVATE-DOCUMENT', json.dumps(events))
        self.assertTrue(self.store.verify_audit())
        for query in ('UPDATE documents SET text="changed"', 'DELETE FROM audit', 'UPDATE audit SET action="changed"'):
            with self.assertRaises(sqlite3.IntegrityError), self.store.connect(True) as db:
                db.execute(query)

    def test_parallel_writes_keep_hash_chain_and_index_consistent(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda index: self.document(f'Договор номер {index}. Оплата после приёмки.'), range(12)))
        self.assertEqual(len(set(ids)), 12)
        self.assertTrue(self.store.verify_audit())

    def test_page_map_and_source_url_validation(self):
        for extra in [{'source_url': 'https://user:secret@pravo.gov.ru/'},
                      {'pages': [{'page': 2, 'start': 0, 'end': 10}]},
                      {'pages': [{'page': 1, 'start': 0, 'end': 2}]}]:
            with self.assertRaises(ValueError):
                self.store.add_document(self.owner, self.matter, {'title': 'Invalid', 'document': 'Длинный пример документа.', **extra})
