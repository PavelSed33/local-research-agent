"""Technical snapshot/export acceptance; synthetic reviews are not legal acceptance."""
import hashlib
import json
from unittest.mock import patch
import unittest

import test_reviewed_evidence as evidence_tests
from case_report import CaseReport, fence
from primary_sources import load_sources
from workspace_api import dispatch
from workspace_store import WorkspaceError, canonical


class CaseReportTests(unittest.TestCase):
    setUp = evidence_tests.ReviewedEvidenceTests.setUp
    add_decision = evidence_tests.ReviewedEvidenceTests.add_decision
    approve = evidence_tests.ReviewedEvidenceTests.approve

    def initial(self, day='2025-01-01', suffix=''):
        return self.add_decision(day, 'Учебный суд. Первая инстанция. Дело А00-1/2025 от '+day+'\nУсловное требование удовлетворено.'+suffix)

    def report(self, **kwargs):
        return CaseReport(self.store).build(kwargs.get('user',self.owner),kwargs.get('matter',self.matter),kwargs.get('case','А00-1/2025'),kwargs.get('as_of','2025-06-01'))

    def test_real_archive_quotes_hashes_pages_and_export_agree(self):
        source=next(row for row in load_sources() if row['id']=='vsrf-decision-2485538')
        dispatch(self.store,self.owner,'/workspace/sources/decision-draft',{'matter_id':self.matter,'source_id':source['id']})
        result=self.report(case=source['case_number'],as_of='2025-09-04')
        self.assertEqual(result['report']['act_count'],1)
        self.assertEqual(result['report']['acts'][0]['review_status'],'draft')
        self.assertFalse(result['report']['legal_acceptance'])
        self.assertFalse(result['report']['model_called'])
        self.assertFalse(result['report']['external_requests_attempted'])
        self.assertEqual(result['markdown_sha256'],hashlib.sha256(result['markdown'].encode('utf-8')).hexdigest())
        for key in ('identity','outcome'):
            ref=result['report']['acts'][0][key]
            document=self.store.document(self.owner,ref['document_id'])
            self.assertEqual(document['text'][ref['start']:ref['end']],ref['quote'])
            self.assertEqual(ref['document_sha256'],document['sha256'])
            self.assertTrue(ref['pages'])
            self.assertIn(ref['quote'],result['markdown'])
            self.assertIn(source['source_url'],result['markdown'].replace('\\',''))
        self.assertIn('комплект нижестоящих оригиналов',result['markdown'])
        self.assertTrue(result['report']['generated_on'].endswith('+03:00'))

    def test_acl_viewer_and_revoked_user(self):
        self.initial()
        with self.assertRaises(WorkspaceError):self.report(user=self.other)
        self.store.grant(self.owner,self.matter,self.other,'viewer')
        self.assertEqual(self.report(user=self.other)['report']['act_count'],1)
        self.store.revoke_user(self.owner,self.other)
        with self.assertRaises(WorkspaceError):self.report(user=self.other)

    def test_other_case_and_other_matter_contents_do_not_leak(self):
        self.initial()
        extra=self.store.create_matter(self.owner,'Чужой проект')['matter_id']
        document=self.store.add_document(self.owner,extra,{'title':'PRIVATE TITLE','document':'PRIVATE OTHER MATTER CONTENT'})
        # Another judicial case in the same workspace matter.
        body='Учебный суд. Первая инстанция. Дело А00-2/2025 от 01.01.2025\nPRIVATE OTHER CASE CONTENT'
        doc=self.store.add_document(self.owner,self.matter,{'title':'Другой спор','document':body})['document_id']
        self.evidence.add(self.owner,self.matter,'decision',{'case_number':'А00-2/2025','court':'Учебный суд','decision_date':'2025-01-01','instance':'Первая инстанция','identity':{'document_id':doc,'quote':body.split('\n')[0]},'outcome':{'document_id':doc,'quote':'PRIVATE OTHER CASE CONTENT'},'positions':[],'links':[],'full_text_declared':True})
        result=json.dumps(self.report(),ensure_ascii=False)
        self.assertNotIn('PRIVATE',result)
        self.assertNotIn(document['document_id'],result)

    def test_latest_version_review_snapshot_changes_and_stable_content_hash(self):
        initial=self.initial();self.approve(initial)
        before=self.report()['report']['snapshot_sha256']
        row=self.evidence.get(self.owner,initial)
        position={'side':'opposing','citation':{key:row['payload']['outcome'][key] for key in ('document_id','quote')},'applicability_note':'Синтетическое отличие требует проверки фактов.'}
        revised=self.evidence.revise_decision(self.owner,initial,[position],[])['artifact_id']
        first=self.report(); second=self.report()
        self.assertEqual(first['report']['snapshot_sha256'],second['report']['snapshot_sha256'])
        self.assertNotEqual(first['report']['snapshot_sha256'],before)
        self.assertEqual(first['report']['excluded']['superseded_versions'],1)
        self.assertEqual(first['report']['act_count'],1)
        self.assertEqual(first['report']['acts'][0]['artifact_id'],revised)
        self.assertEqual(first['report']['acts'][0]['review_status'],'draft')
        self.assertIn(position['applicability_note'],first['markdown'])
        self.approve(revised)
        self.assertNotEqual(self.report()['report']['snapshot_sha256'],first['report']['snapshot_sha256'])

    def test_future_acts_absent_and_empty_is_explicit(self):
        self.initial(day='2025-02-01',suffix=' FUTURE PRIVATE FACT')
        result=self.report(as_of='2025-01-31')
        self.assertEqual(result['report']['act_count'],0)
        self.assertEqual(result['report']['excluded']['future_acts'],1)
        self.assertEqual(result['report']['status'],'no_evidence')
        self.assertNotIn('FUTURE PRIVATE FACT',result['markdown'])
        self.assertIn('Нет загруженных актов',result['markdown'])
        self.assertEqual(self.report(as_of='2025-02-01')['report']['act_count'],1)
        self.assertEqual(self.report(case='А00-неизвестное')['report']['act_count'],0)

    def test_stale_relationship_targets_are_not_silently_rebound(self):
        initial=self.initial()
        later=self.add_decision('2025-02-01','Учебный суд. Первая инстанция. Дело А00-1/2025 от 01.02.2025\nУсловное требование удовлетворено.\nАкт по делу А00-1/2025 от 01.01.2025 оставлен без изменения.',[{'target_id':initial,'relation':'upheld'}])
        row=self.evidence.get(self.owner,initial)
        revised=self.evidence.revise_decision(self.owner,initial,[],[])['artifact_id']
        result=self.report();acts={row['artifact_id']:row for row in result['report']['acts']}
        self.assertEqual(len(acts),2)
        link=acts[later]['links'][0]
        self.assertEqual(link['target']['artifact_id'],initial)
        self.assertEqual(link['target']['superseded_by'],revised)
        self.assertFalse(link['legal_effect_verified'])
        self.assertIn('цель заменена версией',result['markdown'])
        self.assertNotIn(later,json.dumps(self.report(as_of='2025-01-01')['report']['acts']))

    def tamper(self, identity, change):
        row=self.evidence.get(self.owner,identity);change(row['payload']);raw=canonical(row['payload'])
        with self.store.connect(True) as db:
            db.execute('DROP TRIGGER artifacts_no_update')
            db.execute('UPDATE artifacts SET payload=?,sha256=? WHERE id=?',(raw,hashlib.sha256(raw.encode()).hexdigest(),identity))

    def test_invalid_stored_offsets_are_rechecked(self):
        identity=self.initial()
        self.tamper(identity,lambda p:p['outcome'].update(start=0))
        with self.assertRaises(WorkspaceError) as caught:self.report()
        self.assertEqual(caught.exception.code,'case_report_integrity')

    def test_cross_matter_source_is_rejected_even_if_user_has_both(self):
        identity=self.initial();other=self.store.create_matter(self.owner,'Другой проект')['matter_id']
        document=self.store.add_document(self.owner,other,{'title':'Private','document':'SECRET OTHER SOURCE TEXT'})['document_id']
        foreign=self.store.document(self.owner,document)
        self.tamper(identity,lambda p:p['identity'].update(document_id=document,document_sha256=foreign['sha256'],quote=foreign['text'],start=0,end=len(foreign['text'])))
        with self.assertRaises(WorkspaceError) as caught:self.report()
        self.assertEqual(caught.exception.code,'case_report_integrity')
        self.assertNotIn('SECRET',str(caught.exception))

    def test_limits_fail_explicitly_without_success_audit(self):
        self.initial()
        for constant in ('MAX_CANDIDATES','MAX_ACTS','MAX_REPORT_BYTES'):
            with patch('case_report.'+constant,0),self.assertRaises(WorkspaceError) as caught:self.report()
            self.assertEqual(caught.exception.status,413)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit WHERE action='case_report_generated'").fetchone()[0],0)

    def test_markdown_literal_fences_keep_untrusted_quotes_and_notes(self):
        injection='```\n<script>alert(1)</script>\n# Внедрённый заголовок\n````'
        block=fence(injection)
        self.assertEqual(block,'`````text\n'+injection+'\n`````')
        identity=self.initial(suffix='\n'+injection)
        document=self.evidence.get(self.owner,identity)['payload']['identity']['document_id']
        self.evidence.revise_decision(self.owner,identity,[{'side':'opposing','citation':{'document_id':document,'quote':injection},'applicability_note':injection}],[])
        result=self.report()
        self.assertIn(block,result['markdown'])
        self.assertEqual(result['report']['acts'][0]['positions'][0]['citation']['quote'],injection)

    def test_audit_contains_only_snapshot_identifier_not_quotes_or_tokens(self):
        self.initial(suffix=' SECRET LITERAL CASE FACT')
        result=self.report()
        with self.store.connect() as db:
            event=dict(db.execute("SELECT * FROM audit WHERE action='case_report_generated'").fetchone())
        self.assertEqual(event['resource_id'],result['report']['snapshot_sha256'])
        self.assertNotIn('SECRET LITERAL',json.dumps(event))
        self.assertTrue(self.store.verify_audit())

    def test_input_schema_and_real_dates_are_required(self):
        for payload in ({'matter_id':self.matter,'case_number':'А00-1/2025'},
                        {'matter_id':self.matter,'case_number':'А00-1/2025','as_of':'2025-02-30'},
                        {'matter_id':self.matter,'case_number':'А00-1/2025','as_of':'2025-01-01','allow_cloud':True}):
            with self.assertRaises(ValueError):dispatch(self.store,self.owner,'/workspace/cases/report',payload)
