"""Synthetic review-ledger integration checks, not approval of real laws."""
import unittest
import test_reviewed_evidence as evidence_tests
from workspace_api import prepare_answer
from workspace_store import WorkspaceError

class EditionResearchTests(unittest.TestCase):
    setUp = evidence_tests.ReviewedEvidenceTests.setUp
    edition = evidence_tests.ReviewedEvidenceTests.edition
    approve = evidence_tests.ReviewedEvidenceTests.approve

    def payload(self, **extra):
        return {'matter_id':self.matter,'question':'Несовпадающий поисковый запрос','as_of':'2025-06-01','editions':[{'act_id':'synthetic','provision':'1'}],**extra}

    def test_required_date_review_and_unique_period(self):
        payload=self.payload(); del payload['as_of']
        with self.assertRaises(ValueError): prepare_answer(self.store,self.owner,payload)
        with self.assertRaises(WorkspaceError): prepare_answer(self.store,self.owner,self.payload())
        self.approve(self.edition())
        selected=prepare_answer(self.store,self.owner,self.payload())
        self.assertEqual(len(selected['selected_editions']),1)
        self.assertTrue(any(row['edition_references'] for row in selected['sources']))
        self.assertFalse(selected['sources'][0]['source_context']['current_validity_verified'])
        with self.assertRaises(WorkspaceError): prepare_answer(self.store,self.owner,self.payload(as_of='2026-01-01'))
        self.approve(self.edition())
        with self.assertRaises(WorkspaceError): prepare_answer(self.store,self.owner,self.payload())

    def test_revocation_and_new_overlap_block_publish(self):
        identity=self.edition(); self.approve(identity)
        selected=prepare_answer(self.store,self.owner,self.payload())
        with self.store.connect() as db:
            self.evidence.assert_selection(db,self.owner,self.matter,selected['as_of'],selected['selected_editions'])
        self.evidence.review(self.reviewer,identity,'revoked','Синтетический отзыв проверки.',self.attestations)
        with self.store.connect() as db, self.assertRaises(WorkspaceError):
            self.evidence.assert_selection(db,self.owner,self.matter,selected['as_of'],selected['selected_editions'])
        self.approve(identity)
        selected=prepare_answer(self.store,self.owner,self.payload())
        self.approve(self.edition())
        with self.store.connect() as db, self.assertRaises(WorkspaceError):
            self.evidence.assert_selection(db,self.owner,self.matter,selected['as_of'],selected['selected_editions'])

    def test_duplicate_norms_rejected(self):
        self.approve(self.edition())
        refs=self.payload()['editions']*2
        with self.assertRaises(ValueError): prepare_answer(self.store,self.owner,self.payload(editions=refs))
