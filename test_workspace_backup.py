"""Local recovery exercises use temporary databases; no real user credentials printed."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
import zipfile
import zlib

import test_workspace_store as store_tests
from workspace_backup import BackupError,create_backup,verify_backup,restore_backup,inspect_database
from workspace_store import WorkspaceStore,WorkspaceError
from workspace_api import dispatch
from reviewed_evidence import ReviewedEvidence
from benchmark_tasks import BenchmarkTasks


class WorkspaceBackupTests(unittest.TestCase):
    setUp = store_tests.WorkspaceStoreTests.setUp

    def backup(self):
        return create_backup(self.path,self.path.parent/'snapshot.zip')

    def tamper(self,trigger,sql,args):
        with self.store.connect(True) as db:
            definition=db.execute('SELECT sql FROM sqlite_master WHERE name=?',(trigger,)).fetchone()[0]
            db.execute('DROP TRIGGER '+trigger);db.execute(sql,args);db.execute(definition)

    def test_wal_snapshot_preserves_originals_tasks_revisions_acl_and_disables_old_tokens(self):
        with self.store.connect() as keeper:
            keeper.execute('SELECT COUNT(*) FROM documents').fetchone()
            document=self.store.add_document(self.owner,self.matter,{'title':'Private fixture','document':'PRIVATE synthetic original for recovery.'},original=('fixture.txt',b'PRIVATE synthetic original for recovery.'))['document_id']
            self.store.grant(self.owner,self.matter,self.other,'viewer')
            BenchmarkTasks(self.store).import_tasks(self.owner,self.matter)
            decision=dispatch(self.store,self.owner,'/workspace/sources/decision-draft',{'matter_id':self.matter,'source_id':'vsrf-decision-2485538'})['artifact_id']
            evidence=ReviewedEvidence(self.store);row=evidence.get(self.owner,decision)
            evidence.revise_decision(self.owner,decision,[{'side':'distinguishing','citation':{key:row['payload']['outcome'][key] for key in ('document_id','quote')},'applicability_note':'Синтетическая заметка только для проверки восстановления.'}],[])
            self.assertTrue(Path(str(self.path)+'-wal').exists())
            created=self.backup()
        self.assertEqual(verify_backup(self.path.parent/'snapshot.zip',created['snapshot_sha256'])['status'],'verified')
        restored_path=self.path.parent/'restored.db'
        result=restore_backup(self.path.parent/'snapshot.zip',restored_path,created['snapshot_sha256'])
        restored=WorkspaceStore(restored_path)
        self.assertIsNone(restored.authenticate(self.admin['token']))
        self.assertIsNotNone(self.store.authenticate(self.admin['token']))
        self.assertGreater(result['tokens_disabled'],0)
        self.assertEqual(restored.document(self.other,document)['text'],'PRIVATE synthetic original for recovery.')
        self.assertEqual(restored.original(self.owner,document)['filename'],'fixture.txt')
        self.assertTrue(restored.search(self.other,self.matter,'recovery')['matches'])
        self.assertEqual(BenchmarkTasks(restored).list_tasks(self.owner,self.matter)['total'],90)
        self.assertEqual(len(ReviewedEvidence(restored).timeline(self.owner,self.matter,'А56-119496/2023','2026-11-09')['decisions']),1)
        with self.assertRaises(WorkspaceError):restored.add_document(self.other,self.matter,{'title':'Forbidden','document':'Viewer cannot write.'})
        self.assertTrue(restored.verify_audit())
        self.assertTrue(inspect_database(restored_path)['integrity_checked'])
        self.assertEqual(restored_path.stat().st_mode&0o777,0o600)
        self.assertEqual((self.path.parent/'snapshot.zip').stat().st_mode&0o777,0o600)

    def test_existing_destination_and_source_are_never_overwritten(self):
        created=self.backup();before=self.path.read_bytes()
        with self.assertRaises(BackupError):self.backup()
        with self.assertRaises(BackupError):restore_backup(self.path.parent/'snapshot.zip',self.path,created['snapshot_sha256'])
        self.assertEqual(self.path.read_bytes(),before)
        self.assertIsNotNone(self.store.authenticate(self.admin['token']))

    def test_wrong_expected_hash_and_corrupt_backup_do_not_publish_restore(self):
        self.backup();destination=self.path.parent/'restored.db'
        with self.assertRaises(BackupError):restore_backup(self.path.parent/'snapshot.zip',destination,'0'*64)
        self.assertFalse(destination.exists())
        archive=self.path.parent/'snapshot.zip';archive.write_bytes(b'not a zip archive')
        with self.assertRaises(BackupError):verify_backup(archive)
        self.assertFalse(destination.exists())

    def test_document_original_audit_and_fts_corruption_are_detected(self):
        cases=[('document', 'document_hash'),('original','original_hash'),('audit','audit_chain'),('fts','search_index')]
        for kind,code in cases:
            with self.subTest(kind=kind):
                # Independent private store per corruption, preserving normal schema triggers.
                root=self.path.parent/kind;root.mkdir(mode=0o700);store=WorkspaceStore(root/'db')
                user=store.create_user('Synthetic admin','admin')['user_id'];matter=store.create_matter(user,'Synthetic')['matter_id']
                document=store.add_document(user,matter,{'title':'Synthetic','document':'PRIVATE corruption check text'},original=('file.txt',b'original bytes'))['document_id']
                with store.connect(True) as db:
                    if kind=='fts':db.execute('DELETE FROM chunks')
                    else:
                        trigger={'document':'documents_no_update','original':'originals_no_update','audit':'audit_no_update'}[kind]
                        definition=db.execute('SELECT sql FROM sqlite_master WHERE name=?',(trigger,)).fetchone()[0]
                        db.execute('DROP TRIGGER '+trigger)
                        if kind=='document':db.execute('UPDATE documents SET text=? WHERE id=?',('CORRUPTED',document))
                        elif kind=='original':db.execute('UPDATE originals SET bytes=?',(b'bad',))
                        else:db.execute("UPDATE audit SET event_hash='broken' WHERE id=1")
                        db.execute(definition)
                with self.assertRaises(BackupError) as caught:create_backup(store.path,root/'snapshot.zip')
                self.assertEqual(caught.exception.code,code)
                self.assertFalse((root/'snapshot.zip').exists())

    def test_schema_missing_triggers_or_extra_triggers_is_rejected_without_repair(self):
        with self.store.connect(True) as db:db.execute('DROP TRIGGER audit_no_delete')
        with self.assertRaises(BackupError) as caught:self.backup()
        self.assertEqual(caught.exception.code,'schema_mismatch')
        with self.store.connect() as db:self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='audit_no_delete'").fetchone())
        root=self.path.parent/'extra-schema';root.mkdir(mode=0o700);extra=WorkspaceStore(root/'db')
        with extra.connect(True) as db:db.execute('CREATE TRIGGER sqlitex_extra BEFORE UPDATE ON tokens BEGIN SELECT 1; END')
        with self.assertRaises(BackupError) as caught:create_backup(extra.path,root/'snapshot.zip')
        self.assertEqual(caught.exception.code,'schema_mismatch')


    def test_symlink_and_shared_destination_directory_are_rejected(self):
        alias=self.path.parent/'alias.db';alias.symlink_to(self.path)
        with self.assertRaises(BackupError):create_backup(alias,self.path.parent/'snapshot.zip')
        shared=self.path.parent/'shared';shared.mkdir(mode=0o755)
        with self.assertRaises(BackupError):create_backup(self.path,shared/'snapshot.zip')
        bad=self.path.parent/'restored.db-wal';bad.symlink_to(self.path.parent/'missing')
        self.backup()
        with self.assertRaises(BackupError):restore_backup(self.path.parent/'snapshot.zip',self.path.parent/'restored.db',None)
        digest=hashlib.sha256((self.path.parent/'snapshot.zip').read_bytes()).hexdigest()
        with self.assertRaises(BackupError):restore_backup(self.path.parent/'snapshot.zip',self.path.parent/'restored.db',digest)

    def test_live_writer_and_backup_keep_a_consistent_snapshot(self):
        def write():
            for index in range(12):self.store.add_document(self.owner,self.matter,{'title':'Synthetic '+str(index),'document':'Synthetic concurrent record '+str(index)})
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(write);result=self.backup();future.result()
        verified=verify_backup(self.path.parent/'snapshot.zip',result['snapshot_sha256'])
        self.assertEqual(verified['counts'],result['counts'])
        self.assertTrue(self.store.verify_audit())

    def test_cli_admin_recovery_requires_existing_admin_and_rotates_credentials(self):
        created=self.backup();target=self.path.parent/'restored.db'
        restore_backup(self.path.parent/'snapshot.zip',target,created['snapshot_sha256'])
        command=[sys.executable,'scripts/manage_workspace.py','recover-admin-token','--db',str(target),'--user-id']
        invalid=subprocess.run(command+[self.other],capture_output=True,text=True)
        self.assertNotEqual(invalid.returncode,0)
        self.assertNotIn(str(target),invalid.stderr)
        success=subprocess.run(command+[self.owner],capture_output=True,text=True)
        self.assertEqual(success.returncode,0,success.stderr)
        token=json.loads(success.stdout)['token']
        self.assertEqual(WorkspaceStore(target).authenticate(token)['id'],self.owner)
        self.assertIsNone(WorkspaceStore(target).authenticate(self.admin['token']))
        with WorkspaceStore(target).connect() as db:
            event=db.execute('SELECT user_id,action,resource_id FROM audit ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual(tuple(event),('local-recovery','token_reissued_local',self.owner))

    def test_verify_command_does_not_create_a_missing_database(self):
        target=self.path.parent/'missing.db'
        result=subprocess.run([sys.executable,'scripts/manage_workspace.py','verify-audit','--db',str(target)],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(target.exists())
        self.assertNotIn(str(target),result.stderr)


    def test_copy_limits_and_timeout_never_publish_partial_backups(self):
        for constant in ('MAX_DB_BYTES','TIMEOUT_SECONDS'):
            with patch('workspace_backup.'+constant,0),self.assertRaises(BackupError):self.backup()
            self.assertFalse((self.path.parent/'snapshot.zip').exists())
        with patch('workspace_backup.os.fsync',side_effect=[None,OSError('synthetic sync failure')]),self.assertRaises(BackupError):self.backup()
        self.assertFalse((self.path.parent/'snapshot.zip').exists())

    def test_archive_manifest_and_extra_entries_are_verified_not_extracted(self):
        self.backup();path=self.path.parent/'snapshot.zip'
        with zipfile.ZipFile(path) as archive:parts={name:archive.read(name) for name in archive.namelist()}
        for mutate in [lambda rows:rows.update({'../outside':b'bad'}),
                       lambda rows:rows.update({'manifest.json':b'{}'}),
                       lambda rows:rows.update({'workspace.sqlite3':b'not a database'})]:
            changed=dict(parts);mutate(changed)
            altered=self.path.parent/'altered.zip'
            with zipfile.ZipFile(altered,'w') as archive:
                for name,raw in changed.items():archive.writestr(name,raw)
            with self.assertRaises(BackupError):verify_backup(altered)
            self.assertFalse((self.path.parent/'outside').exists())

        with patch('workspace_backup.zipfile.ZipFile',side_effect=zlib.error('synthetic damaged compressed stream')),self.assertRaises(BackupError):
            verify_backup(path)
