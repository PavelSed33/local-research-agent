"""Synthetic revision checks do not certify legal conclusions or reviewer qualifications."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import unittest

import test_reviewed_evidence as evidence_tests
from primary_sources import load_sources
from workspace_api import dispatch
from workspace_store import WorkspaceError


class DecisionRevisionTests(unittest.TestCase):
    setUp = evidence_tests.ReviewedEvidenceTests.setUp
    add_decision = evidence_tests.ReviewedEvidenceTests.add_decision
    approve = evidence_tests.ReviewedEvidenceTests.approve

    def initial(self):
        return self.add_decision('2025-01-01', 'Учебный суд. Первая инстанция. Дело А00-1/2025 от 01.01.2025\nУсловное требование удовлетворено.')

    def position(self, identity):
        row = self.evidence.get(self.owner, identity)
        return {'side':'distinguishing','citation':{'document_id':row['payload']['identity']['document_id'],
            'quote':'Условное требование удовлетворено.'},'applicability_note':'Синтетические факты; применимость не установлена.'}

    def revise(self, identity, positions=None, links=None, user=None):
        return self.evidence.revise_decision(user or self.owner, identity,
            [self.position(identity)] if positions is None else positions, [] if links is None else links)

    def test_new_version_keeps_old_review_but_requires_independent_review(self):
        initial = self.initial(); self.approve(initial)
        revised = self.revise(initial)['artifact_id']
        old = self.evidence.get(self.owner,initial); new = self.evidence.get(self.owner,revised)
        self.assertEqual(old['review_status'],'approved')
        self.assertEqual(old['version']['superseded_by'],revised)
        self.assertEqual(new['review_status'],'draft')
        self.assertEqual(new['version']['root_artifact_id'],initial)
        self.assertIsNone(new['review'])
        with self.assertRaises(WorkspaceError): self.approve(initial)
        queue = self.evidence.queue(self.owner,self.matter)['artifacts']
        self.assertEqual([row['artifact_id'] for row in queue],[revised])
        timeline = self.evidence.timeline(self.owner,self.matter,'А00-1/2025','2025-01-01')
        self.assertEqual([row['id'] for row in timeline['decisions']],[revised])
        self.assertEqual(timeline['superseded_versions_excluded'],1)
        self.assertFalse(timeline['complete_history'])

    def test_concurrent_revision_allows_one_successor(self):
        initial=self.initial()
        def create(_):
            try:return self.revise(initial)['artifact_id']
            except WorkspaceError as exc:return exc.code
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(create,range(8)))
        self.assertEqual(results.count('decision_version_changed'),7)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM decision_revisions').fetchone()[0],1)
        self.assertTrue(self.store.verify_audit())

    def test_acl_noop_bad_quote_and_duplicate_positions(self):
        initial=self.initial()
        with self.assertRaises(WorkspaceError): self.revise(initial,user=self.other)
        self.store.grant(self.owner,self.matter,self.other,'viewer')
        with self.assertRaises(WorkspaceError): self.revise(initial,user=self.other)
        original=self.evidence.get(self.owner,initial)['payload']
        unchanged=[{**row,'citation':{key:row['citation'][key] for key in ('document_id','quote')}} for row in original['positions']]
        with self.assertRaises(ValueError): self.revise(initial,positions=unchanged)
        position=self.position(initial)
        with self.assertRaises(ValueError): self.revise(initial,positions=[position,position])
        position['citation']['quote']='Выдуманный результат, отсутствующий в документе.'
        with self.assertRaises(ValueError): self.revise(initial,positions=[position])
        position=self.position(initial)
        position['applicability_note']=[]
        with self.assertRaises(ValueError): self.revise(initial,positions=[position])
        self.assertIsNone(self.evidence.get(self.owner,initial)['version']['superseded_by'])

    def test_old_relations_are_explicit_when_target_is_revised(self):
        initial=self.initial()
        body='Учебный суд. Первая инстанция. Дело А00-1/2025 от 01.02.2025\nУсловное требование удовлетворено.\nАкт по делу А00-1/2025 от 01.01.2025 оставлен без изменения.'
        later=self.add_decision('2025-02-01',body,[{'target_id':initial,'relation':'upheld'}])
        revised=self.revise(initial)['artifact_id']
        timeline=self.evidence.timeline(self.owner,self.matter,'А00-1/2025','2025-02-01')
        self.assertEqual(timeline['relationships'][0]['target_superseded_by'],revised)
        self.assertFalse(timeline['relationships'][0]['legal_effect_verified'])
        self.assertTrue(timeline['relationships'][0]['requires_expert_review'])
        with self.assertRaises(WorkspaceError): self.approve(later)
        payload=self.evidence.get(self.owner,later)['payload']
        link={'target_id':initial,'relation':'upheld','citation':{key:payload['links'][0]['citation'][key] for key in ('document_id','quote')}}
        with self.assertRaises(ValueError): self.revise(later,links=[link])
        self.assertEqual(self.evidence.timeline(self.owner,self.matter,'А00-1/2025','2025-01-01')['relationships'],[])

    def test_same_document_cannot_link_to_itself_and_revision_ledger_is_append_only(self):
        initial=self.initial(); revised=self.revise(initial)['artifact_id']
        document=self.evidence.get(self.owner,revised)['payload']['identity']['document_id']
        link={'target_id':revised,'relation':'upheld','citation':{'document_id':document,'quote':'Учебный суд. Первая инстанция. Дело А00-1/2025 от 01.01.2025'}}
        with self.assertRaises(ValueError): self.revise(revised,positions=[],links=[link])
        for sql in ('DELETE FROM decision_revisions','UPDATE decision_revisions SET root_artifact_id=artifact_id'):
            with self.store.connect(True) as db,self.assertRaises(sqlite3.IntegrityError):db.execute(sql)

    def test_real_archived_card_can_be_enriched_and_reimport_reuses_latest(self):
        args={'matter_id':self.matter,'source_id':'vsrf-decision-2485538'}
        initial=dispatch(self.store,self.owner,'/workspace/sources/decision-draft',args)['artifact_id']
        original=self.evidence.get(self.owner,initial)
        source=next(row for row in load_sources() if row['id']==args['source_id'])
        position={'side':'distinguishing','citation':{'document_id':original['payload']['identity']['document_id'],
            'quote':source['disposition_quote']},'applicability_note':'Необходимо проверить факты договора и действие моратория; это черновая заметка.'}
        revised=self.revise(initial,positions=[position])['artifact_id']
        reused=dispatch(self.store,self.owner,'/workspace/sources/decision-draft',args)
        self.assertEqual(reused['artifact_id'],revised)
        self.assertTrue(reused['reused'])
        new=self.evidence.get(self.owner,revised)
        self.assertEqual(new['payload']['identity'],original['payload']['identity'])
        self.assertEqual(new['review_status'],'draft')
