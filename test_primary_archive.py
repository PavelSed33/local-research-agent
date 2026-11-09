import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from primary_sources import ROOT, PrimarySourceError, load_sources, original_bytes, source_catalog
from workspace_api import dispatch, prepare_answer
from reviewed_evidence import ReviewedEvidence, date_quoted
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

    def test_seven_physical_originals_have_identity_and_page_maps_without_legal_acceptance(self):
        sources = source_catalog()
        self.assertEqual(len(sources['sources']), 7)
        self.assertEqual(sum(row['page_count'] for row in sources['sources']), 90)
        self.assertEqual({row['document_number'] for row in sources['sources']}, {'49', '7', '54', '307-ЭС24-20538', '307-ЭС25-3481', '305-ЭС23-19099', '305-ЭС18-10125'})
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


class OfficialDecisionTests(unittest.TestCase):
    setUp = PrimaryArchiveImportTests.setUp
    do_import = PrimaryArchiveImportTests.do_import
    def test_official_decision_metadata_is_literal_and_never_promotes_trust(self):
        for mutate in [lambda row: row.update(case_number='А56-999999/2024'),
                       lambda row: row.update(document_number='307-ЭС24-205380'),
                       lambda row: row.update(signed_on='2025-02-27'),
                       lambda row: row.update(disposition_quote='Выдуманный результат судебного рассмотрения.')]:
            PrimaryArchiveValidationTests().mutation(lambda data: mutate(data['sources'][3]))

    def test_real_first_pages_and_dispositions_can_form_unreviewed_dossiers(self):
        evidence = ReviewedEvidence(self.store)
        for source in load_sources():
            if source['material_type'] != 'official_court_decision': continue
            imported=self.do_import(source['id'])
            identity=source['text'][:source['pages'][0]['end']]
            artifact=evidence.add(self.owner,self.matter,'decision',{
                'case_number':source['case_number'],'court':'Верховный Суд Российской Федерации',
                'decision_date':source['signed_on'],'instance':'Судебная коллегия по экономическим спорам',
                'identity':{'document_id':imported['document_id'],'quote':identity},
                'outcome':{'document_id':imported['document_id'],'quote':source['disposition_quote']},
                'positions':[],'links':[],'full_text_declared':True})
            row=evidence.get(self.owner,artifact['artifact_id'])
            self.assertEqual(row['review_status'],'draft')
            self.assertFalse(row['legal_acceptance'])
            timeline=evidence.timeline(self.owner,self.matter,source['case_number'],'2026-11-09')
            self.assertEqual(len(timeline['decisions']),1)
            self.assertFalse(timeline['complete_history'])

    def test_future_archived_decisions_are_excluded_on_date(self):
        self.do_import('vsrf-decision-2485538')
        before=prepare_answer(self.store,self.owner,{'matter_id':self.matter,'question':'поставка неустойка','as_of':'2025-09-03','demo':True})
        self.assertEqual(before['sources'],[])
        self.assertGreater(before['future_decision_fragments_excluded'],0)
        on=prepare_answer(self.store,self.owner,{'matter_id':self.matter,'question':'поставка неустойка','as_of':'2025-09-04','demo':True})
        self.assertGreater(len(on['sources']),0)
        self.assertTrue(all(row['source_context']['decision_date']=='2025-09-04' for row in on['sources']))

    def test_date_quotes_accept_russian_months_but_reject_numeric_prefixes(self):
        self.assertTrue(date_quoted('2025-09-04','4 сентября\n2025 г.'))
        self.assertFalse(date_quoted('2025-09-04','14 сентября 2025 г.'))
        self.assertFalse(date_quoted('2025-09-04','2025-09-040'))


    def test_decision_card_import_is_atomic_idempotent_and_keeps_review_empty(self):
        def create(_):
            return dispatch(self.store,self.owner,'/workspace/sources/decision-draft',{'matter_id':self.matter,'source_id':'vsrf-decision-2485538'})
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows=list(pool.map(create,range(8)))
        self.assertEqual(len({row['artifact_id'] for row in rows}),1)
        self.assertEqual(sum(row['reused'] for row in rows),7)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0],0)
        self.assertTrue(self.store.verify_audit())

    def test_citation_whitespace_keeps_returned_quote_and_offsets_consistent(self):
        imported=self.do_import('vsrf-decision-2485538')
        # Use an actual literal fragment, preserving the extraction as the source of truth.
        body=self.store.document(self.owner,imported['document_id'])['text']
        quote=body[:50]
        with self.store.connect() as db:
            ref=ReviewedEvidence(self.store)._quote(db,self.owner,self.matter,{'document_id':imported['document_id'],'quote':'  '+quote+'  '})
        self.assertEqual(body[ref['start']:ref['end']],ref['quote'])

    def test_eleven_procedural_mentions_are_literal_not_archived_lower_court_acts(self):
        sources=load_sources()
        mentions=[(source,mention) for source in sources for mention in source.get('procedural_mentions',[])]
        self.assertEqual(len(mentions),11)
        self.assertEqual(len(sources),7)
        for source,mention in mentions:
            self.assertIn(mention['quote'],source['text'])
            self.assertIn(source['case_number'],mention['quote'])
            self.assertLessEqual(mention['decision_date'],source['signed_on'])
            self.assertFalse(source['lawyer_reviewed'])

    def test_procedural_mention_mutations_fail_closed(self):
        for mutation in [lambda row:row.update(court_mention='Выдуманный суд без подтверждения'),
                         lambda row:row.update(decision_date='2025-03-01'),
                         lambda row:row.update(decision_date='2023-05-07'),
                         lambda row:row.update(quote='Поддельная цитата об отсутствующем судебном акте.')]:
            PrimaryArchiveValidationTests().mutation(lambda data:mutation(data['sources'][3]['procedural_mentions'][0]))
        PrimaryArchiveValidationTests().mutation(lambda data:data['sources'][3]['procedural_mentions'].append(data['sources'][3]['procedural_mentions'][0].copy()))
