"""Server-owned bytes, changing ACL/reviews and recovery; no legal approval."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from analysis_reports import AnalysisReports, KIND, MAX_MARKDOWN_BYTES
from matter_research import answer
import test_service_pilot as pilot_tests
from workspace_api import dispatch
from workspace_backup import create_backup, restore_backup, inspect_database
from workspace_store import WorkspaceError, WorkspaceStore, canonical


class AnalysisReportTests(unittest.TestCase):
    setUp = pilot_tests.ServicePilotTests.setUp
    edition = pilot_tests.ServicePilotTests.edition
    approve = pilot_tests.ServicePilotTests.approve
    payload = pilot_tests.ServicePilotTests.payload
    prepared = pilot_tests.ServicePilotTests.prepared
    with_edition = pilot_tests.ServicePilotTests.with_edition

    def save(self, prepared=None, result=None, principal=None):
        prepared = prepared or self.with_edition()
        with self.store.connect(True) as db:
            saved = AnalysisReports(self.store).save(db, principal or self.owner, prepared, result or answer(prepared))
        return prepared, saved

    def read_payload(self, result):
        return {'matter_id': self.matter, 'report_id': result['report_id'],
                'markdown_sha256': result['markdown_sha256']}

    def read(self, result, **extra):
        return dispatch(self.store, self.owner, '/workspace/analysis/read', {**self.read_payload(result), **extra})

    def tamper(self, identity, transform, rehash=False):
        with self.store.connect(True) as db:
            trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='artifacts_no_update'").fetchone()[0]
            db.execute('DROP TRIGGER artifacts_no_update')
            row = db.execute('SELECT payload,sha256 FROM artifacts WHERE id=?', (identity,)).fetchone()
            payload = json.loads(row['payload']); transform(payload); raw = canonical(payload)
            digest = hashlib.sha256(raw.encode()).hexdigest() if rehash else row['sha256']
            db.execute('UPDATE artifacts SET payload=?,sha256=? WHERE id=?', (raw, digest, identity))
            db.execute(trigger)

    def test_saved_bytes_survive_restart_and_have_no_legal_approval(self):
        _, saved = self.save()
        store = WorkspaceStore(self.path)
        result = dispatch(store, self.owner, '/workspace/analysis/read', self.read_payload(saved))
        self.assertEqual(result['markdown'], saved['markdown'])
        self.assertEqual(result['report_snapshot_sha256'], saved['report_snapshot_sha256'])
        self.assertTrue(result['report_authenticity_verified'])
        self.assertTrue(result['context_current'])
        self.assertFalse(result['legal_acceptance']); self.assertFalse(result['digital_signature'])
        self.assertTrue(store.verify_audit())

    def test_only_server_snapshot_is_returned_and_client_bytes_are_rejected(self):
        _, saved = self.save()
        local = copy.deepcopy(saved); local['markdown'] = 'FORGED CLIENT REPORT'
        self.assertEqual(self.read(local)['markdown'], saved['markdown'])
        with self.assertRaises(ValueError):
            self.read(saved, markdown='FORGED CLIENT REPORT')
        with self.assertRaises(WorkspaceError) as caught:
            self.read(saved, markdown_sha256='f' * 64)
        self.assertEqual(caught.exception.code, 'analysis_report_changed')

    def test_other_matter_missing_report_and_revoked_access_fail_without_text(self):
        _, saved = self.save()
        other = self.store.create_matter(self.owner, 'Другой проект')['matter_id']
        for extra in ({'matter_id': other}, {'report_id': 'unknown'}, {'report_id': self.document_id}):
            with self.subTest(extra=extra), self.assertRaises(WorkspaceError) as caught:
                self.read(saved, **extra)
            self.assertEqual(caught.exception.status, 403)
        with self.assertRaises(WorkspaceError):
            dispatch(self.store, self.other, '/workspace/analysis/read', self.read_payload(saved))
        self.store.grant(self.owner, self.matter, self.other, 'viewer')
        self.assertEqual(dispatch(self.store, self.other, '/workspace/analysis/read', self.read_payload(saved))['markdown'], saved['markdown'])
        self.store.grant(self.owner, self.matter, self.other, 'remove')
        with self.assertRaises(WorkspaceError):
            dispatch(self.store, self.other, '/workspace/analysis/read', self.read_payload(saved))

    def test_revoked_user_cannot_read_saved_report(self):
        _, saved = self.save()
        self.store.grant(self.owner, self.matter, self.other, 'viewer')
        self.store.revoke_user(self.owner, self.other)
        with self.assertRaises(WorkspaceError):
            dispatch(self.store, self.other, '/workspace/analysis/read', self.read_payload(saved))

    def test_revoked_reapproved_and_overlapping_editions_block_old_snapshot(self):
        prepared, saved = self.save()
        identity = prepared['selected_editions'][0]['artifact_id']
        self.evidence.review(self.reviewer, identity, 'revoked', 'СИНТЕТИЧЕСКИЙ отзыв.', self.attestations)
        with self.assertRaises(WorkspaceError): self.read(saved)
        self.approve(identity)
        with self.assertRaises(WorkspaceError): self.read(saved)
        prepared = self.prepared(editions=[{'act_id': 'synthetic', 'provision': '1'}])
        _, fresh = self.save(prepared)
        self.approve(self.edition())
        with self.assertRaises(WorkspaceError): self.read(fresh)

    def test_generic_evidence_routes_cannot_bypass_report_checks(self):
        _, saved = self.save()
        self.assertNotIn(saved['report_id'], [row['artifact_id'] for row in self.evidence.queue(self.owner, self.matter)['artifacts']])
        with self.assertRaises(WorkspaceError): self.evidence.get(self.owner, saved['report_id'])
        with self.assertRaises(WorkspaceError):
            self.evidence.review(self.reviewer, saved['report_id'], 'approved', 'СИНТЕТИЧЕСКИЙ отзыв.', self.attestations)
        with self.assertRaises(ValueError):
            self.evidence.add(self.owner, self.matter, KIND, {'markdown': 'FORGED CLIENT REPORT'})

    def test_corrupt_payload_and_corrupt_markdown_hash_fail_closed(self):
        _, saved = self.save()
        self.tamper(saved['report_id'], lambda value: value.update(markdown='CORRUPTED'))
        with self.assertRaises(WorkspaceError) as caught: self.read(saved)
        self.assertEqual(caught.exception.code, 'analysis_report_integrity')
        prepared = self.prepared(editions=[{'act_id': 'synthetic', 'provision': '1'}])
        _, fresh = self.save(prepared)
        self.tamper(fresh['report_id'], lambda value: value.update(markdown='CORRUPTED'), rehash=True)
        with self.assertRaises(WorkspaceError) as caught: self.read(fresh)
        self.assertEqual(caught.exception.code, 'analysis_report_integrity')

    def test_untrusted_hashes_and_oversize_results_never_save(self):
        prepared = self.with_edition(); result = answer(prepared)
        for markdown, digest in ((result['markdown'], 'f' * 64), ('', hashlib.sha256(b'').hexdigest()),
                                 ('x' * (MAX_MARKDOWN_BYTES + 1), 'f' * 64)):
            with self.subTest(size=len(markdown)), self.assertRaises(WorkspaceError):
                self.save(prepared, {**result, 'markdown': markdown, 'markdown_sha256': digest})
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM artifacts WHERE kind=?', (KIND,)).fetchone()[0], 0)

    def test_source_change_during_work_prevents_save(self):
        prepared = self.with_edition(); prepared['sources'][0]['document_sha256'] = 'f' * 64
        with self.assertRaises(WorkspaceError) as caught:
            self.save(prepared)
        self.assertEqual(caught.exception.code, 'analysis_source_changed')

    def test_source_hash_change_after_save_prevents_download(self):
        _, saved = self.save()
        original = self.store._document
        def changed(db, principal, identity):
            row = original(db, principal, identity)
            return {**row, 'sha256': 'f' * 64}
        with patch.object(self.store, '_document', side_effect=changed), self.assertRaises(WorkspaceError) as caught:
            self.read(saved)
        self.assertEqual(caught.exception.code, 'analysis_source_changed')

    def test_failed_audit_rolls_back_the_snapshot(self):
        prepared = self.with_edition()
        with patch.object(self.store, '_audit', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.save(prepared)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM artifacts WHERE kind=?', (KIND,)).fetchone()[0], 0)

    def test_strict_download_fields_and_hash_types(self):
        _, saved = self.save()
        for wrong in ('', None, True, {}, 'z' * 64):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                self.read(saved, markdown_sha256=wrong)
        for wrong in (None, True, {}, ''):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                self.read(saved, report_id=wrong)

    def test_backup_restores_bytes_but_not_outdated_review_authority(self):
        prepared, saved = self.save()
        archive = self.path.parent / 'reports.zip'
        report = create_backup(self.path, archive)
        restored_path = self.path.parent / 'restored.db'
        restore_backup(archive, restored_path, report['snapshot_sha256'])
        restored = WorkspaceStore(restored_path)
        self.assertIsNone(restored.authenticate(self.admin['token']))
        self.assertEqual(dispatch(restored, self.owner, '/workspace/analysis/read', self.read_payload(saved))['markdown'], saved['markdown'])
        self.assertTrue(inspect_database(restored_path)['integrity_checked'])
        self.evidence.review(self.reviewer, prepared['selected_editions'][0]['artifact_id'], 'revoked', 'СИНТЕТИЧЕСКИЙ отзыв.', self.attestations)
        stale_archive = self.path.parent / 'stale.zip'
        stale = create_backup(self.path, stale_archive)
        stale_path = self.path.parent / 'stale.db'
        restore_backup(stale_archive, stale_path, stale['snapshot_sha256'])
        with self.assertRaises(WorkspaceError):
            dispatch(WorkspaceStore(stale_path), self.owner, '/workspace/analysis/read', self.read_payload(saved))


if __name__ == '__main__':
    unittest.main()
