"""Synthetic workflow checks; these do not perform legal acceptance."""
import json
import sqlite3
import unittest
from unittest.mock import patch

from benchmark_tasks import BenchmarkTasks, BenchmarkTemplateError, load_templates
import test_workspace_store as store_tests
from workspace_store import WorkspaceError


class BenchmarkTaskTests(unittest.TestCase):
    def setUp(self):
        store_tests.WorkspaceStoreTests.setUp(self)
        self.tasks = BenchmarkTasks(self.store)

    def first(self):
        self.tasks.import_tasks(self.owner, self.matter)
        return self.tasks.list_tasks(self.owner, self.matter)['tasks'][0]['task_id']

    def test_import_is_scoped_idempotent_and_does_not_invent_answers(self):
        first = self.tasks.import_tasks(self.owner, self.matter)
        self.assertEqual(first['created'], 90)
        self.assertEqual(first['answers_generated'], 0)
        self.assertEqual(self.tasks.import_tasks(self.owner, self.matter)['reused'], 90)
        listing = self.tasks.list_tasks(self.owner, self.matter)
        self.assertEqual(listing['needs_expert_answer'], 90)
        self.assertEqual(listing['reviews_recorded'], 0)
        self.assertFalse(listing['legal_acceptance'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 0)
        other = self.store.create_matter(self.owner, 'Другое дело')['matter_id']
        self.assertEqual(self.tasks.import_tasks(self.owner, other)['created'], 90)

    def test_read_and_write_require_matter_access(self):
        task = self.first()
        for action in (lambda:self.tasks.read(self.other, task), lambda:self.tasks.import_tasks(self.other, self.matter)):
            with self.assertRaises(WorkspaceError): action()
        self.store.grant(self.owner, self.matter, self.other, 'viewer')
        self.assertEqual(self.tasks.read(self.other, task)['task_id'], task)
        with self.assertRaises(WorkspaceError): self.tasks.save_draft(self.other, task, {})

    def test_draft_quotes_revisions_and_optimistic_lock(self):
        task = self.first()
        doc = self.store.add_document(self.owner, self.matter, {'title':'Учебный документ', 'document':'Синтетическая норма для проверки цитат.'})['document_id']
        payload = {'expected_answer':'Учебный ответ для проверки механизма.', 'required_risks':['Учебный риск'], 'forbidden_claims':[], 'citations':[{'document_id':doc,'quote':'Синтетическая норма для проверки цитат.'}]}
        saved = self.tasks.save_draft(self.owner, task, payload)
        self.assertEqual(saved['review_status'], 'draft')
        self.assertFalse(saved['legal_acceptance'])
        with self.assertRaises(WorkspaceError): self.tasks.save_draft(self.owner, task, payload)
        second = self.tasks.save_draft(self.owner, task, payload, saved['artifact_id'])
        self.assertNotEqual(second['artifact_id'], saved['artifact_id'])
        self.assertEqual(self.tasks.read(self.owner, task)['current_artifact_id'], second['artifact_id'])
        payload['citations'][0]['quote'] = 'Выдуманная цитата, которой нет в документе.'
        with self.assertRaises(ValueError): self.tasks.save_draft(self.owner, task, payload, second['artifact_id'])
        self.assertEqual(self.tasks.list_tasks(self.owner, self.matter)['reviews_recorded'], 0)

    def test_snapshot_is_immutable(self):
        task = self.first()
        with self.store.connect(True) as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute('UPDATE benchmark_tasks SET payload=? WHERE id=?', ('{}',task))

    def test_invalid_templates_fail_closed(self):
        path = self.path.parent/'cases.json'
        path.write_text(json.dumps({'lawyer_reviewed':True,'verified':False,'cases':[]}))
        with self.assertRaises(BenchmarkTemplateError): load_templates(path)
        with self.assertRaises(ValueError): self.tasks.import_tasks(self.owner,self.matter,['invented'])
        with self.assertRaises(ValueError): self.tasks.import_tasks(self.owner,self.matter,['LAW-01-B01']*2)
