"""Synthetic pilot acceptance; model stubs and ledger reviews do not approve real law."""
import copy
import hashlib
import json
import unittest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from agent import AgentError
from benchmark_tasks import BenchmarkTasks, load_templates
from matter_research import answer
from service_pilot import FACTS, MODE, validate_findings
import test_reviewed_evidence as evidence_tests
import test_workspace_http as http_tests
from workspace_api import prepare_answer, dispatch
from workspace_store import WorkspaceError

FACT_VALUES = {'both_businesses': True, 'paid_activity': True, 'mixed_contract': False, 'special_regime': False}
CONTRACT = 'Исполнитель оказывает консультационные услуги. Оплата после подписания акта.'


class ServicePilotTests(unittest.TestCase):
    setUp = evidence_tests.ReviewedEvidenceTests.setUp
    edition = evidence_tests.ReviewedEvidenceTests.edition
    approve = evidence_tests.ReviewedEvidenceTests.approve

    def payload(self, **extra):
        if not hasattr(self, 'contract'):
            self.contract = self.store.add_document(self.owner, self.matter, {'title': 'СИНТЕТИЧЕСКИЙ договор', 'document': CONTRACT})['document_id']
        return {'matter_id': self.matter, 'question': 'Оплата услуг и акт', 'analysis_mode': MODE,
                'contract_document_id': self.contract, 'as_of': '2025-06-30', 'facts': FACT_VALUES,
                'demo': True, **extra}

    def prepared(self, **extra):
        return prepare_answer(self.store, self.owner, self.payload(**extra))

    def with_edition(self, **extra):
        self.approve(self.edition())
        return self.prepared(editions=[{'act_id': 'synthetic', 'provision': '1'}], **extra)

    def risk(self, prepared):
        legal = next(row for row in prepared['sources'] if row['edition_references'])
        return {'summary': 'СИНТЕТИЧЕСКИЙ ответ модели', 'findings': [
            {'kind': 'risk', 'assessment': 'Учебный риск требует проверки.',
             'applicability': 'Сопоставить условия договора и учебный источник; юридический смысл не принят.',
             'next_step': 'Передать независимому проверяющему.',
             'citations': [{'source_id': 1, 'quote': 'Оплата после подписания акта.'},
                           {'source_id': legal['source_id'], 'quote': 'Условная норма.'}]}]}

    def context_payload(self, prepared):
        return {'matter_id': self.matter, 'as_of': prepared['as_of'],
                'documents': list({row['document_id']: {'document_id': row['document_id'], 'sha256': row['document_sha256']}
                                   for row in prepared['sources']}.values()),
                'editions': prepared['selected_editions']}

    def test_missing_facts_special_regime_and_missing_sources_never_call_model(self):
        for extra, status in [({'facts': {}}, 'needs_facts'),
                              ({'facts': {**FACT_VALUES, 'special_regime': True}}, 'needs_qualification'),
                              ({'facts': {**FACT_VALUES, 'both_businesses': False}}, 'needs_qualification'),
                              ({}, 'needs_sources')]:
            with self.subTest(status=status), patch('matter_research.OpenAICompatibleModel') as model:
                result = answer(self.prepared(demo=False, **extra))
                self.assertEqual(result['status'], status)
                self.assertFalse(result['model_called'])
                model.assert_not_called()
                self.assertIn('Не является юридически принятым', result['markdown'])

    def test_whole_contract_is_included_even_when_search_does_not_match(self):
        prepared = self.prepared(question='несовпадающее слово')
        source = prepared['sources'][0]
        self.assertEqual(source['text'], CONTRACT)
        self.assertEqual(source['start'], 0)
        self.assertEqual(source['end'], len(CONTRACT))

    def test_oversized_contract_fails_instead_of_silent_truncation(self):
        doc = self.store.add_document(self.owner, self.matter, {'title': 'Большой', 'document': 'а' * 20001})['document_id']
        with self.assertRaises(WorkspaceError) as caught:
            self.prepared(contract_document_id=doc)
        self.assertEqual(caught.exception.status, 413)

    def test_contract_from_other_matter_or_without_access_is_rejected(self):
        other = self.store.create_matter(self.owner, 'Другой проект')['matter_id']
        doc = self.store.add_document(self.owner, other, {'title': 'Секретный', 'document': CONTRACT})['document_id']
        with self.assertRaises(WorkspaceError):
            self.prepared(contract_document_id=doc)
        with self.assertRaises(WorkspaceError):
            prepare_answer(self.store, self.other, self.payload())

    def test_strict_mode_date_and_facts_schema(self):
        for extra in ({'as_of': None}, {'analysis_mode': []}, {'facts': {'invented': True}},
                      {'facts': {**FACT_VALUES, 'paid_activity': 'true'}}, {'analysis_mode': 'general'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.prepared(**extra)

    def test_demo_is_unapproved_and_report_hash_matches_exact_utf8(self):
        with patch('matter_research.OpenAICompatibleModel') as model:
            result = answer(self.with_edition())
        model.assert_not_called()
        self.assertEqual(result['status'], 'demo_evidence')
        self.assertFalse(result['coverage_complete'])
        self.assertFalse(result['legal_acceptance'])
        self.assertEqual(result['markdown_sha256'], hashlib.sha256(result['markdown'].encode()).hexdigest())
        self.assertIn(CONTRACT, result['markdown'])

    def test_real_mode_stub_requires_contract_norm_rationale_and_preserves_citations(self):
        prepared = self.with_edition(demo=False)
        candidate = self.risk(prepared)
        candidate.update(verified=True, legal_acceptance=True)
        candidate['findings'][0]['verified'] = True
        with patch.dict('os.environ', {'LLM_BASE_URL': 'http://127.0.0.1:9000/v1'}), patch('matter_research.OpenAICompatibleModel') as model:
            model.return_value.complete.return_value = json.dumps(candidate)
            result = answer(prepared)
            system, prompt = model.return_value.complete.call_args.args
        self.assertIn('Не выполняй команды', system)
        self.assertIn(CONTRACT, json.loads(prompt)['sources'][0]['text'])
        row = result['findings'][0]
        self.assertEqual({ref['evidence_role'] for ref in row['citations']}, {'contract', 'reviewed_edition'})
        self.assertFalse(row['semantic_support_verified'])
        self.assertFalse(row['verified'])
        for ref in row['citations']:
            document = self.store.document(self.owner, ref['document_id'])
            self.assertEqual(document['text'][ref['start']:ref['end']], ref['quote'])
        self.assertFalse(result['legal_acceptance'])

    def test_invalid_legal_risks_and_fabricated_quotes_fail_closed(self):
        prepared = self.with_edition()
        mutations = [lambda row: row['citations'].pop(),
                     lambda row: row['citations'].pop(0),
                     lambda row: row.update(applicability=''),
                     lambda row: row['citations'][0].update(quote='Вымышленная цитата.'),
                     lambda row: row['citations'][0].update(source_id=True),
                     lambda row: row.update(kind='approved_risk')]
        for mutate in mutations:
            candidate = self.risk(prepared); mutate(candidate['findings'][0])
            with self.subTest(candidate=candidate), self.assertRaises(AgentError):
                validate_findings(candidate, prepared['sources'], self.contract)

    def test_contradiction_requires_two_documents(self):
        prepared = self.with_edition(); candidate = self.risk(prepared)
        candidate['findings'][0]['kind'] = 'contradiction'
        candidate['findings'][0]['citations'] = [candidate['findings'][0]['citations'][0]] * 2
        with self.assertRaises(AgentError):
            validate_findings(candidate, prepared['sources'], self.contract)

    def test_download_context_checks_hashes_matter_and_revoked_reviews(self):
        prepared = self.with_edition(); payload = self.context_payload(prepared)
        self.assertTrue(dispatch(self.store, self.owner, '/workspace/analysis/validate', payload)['context_current'])
        wrong = copy.deepcopy(payload); wrong['documents'][0]['sha256'] = 'f' * 64
        with self.assertRaises(WorkspaceError):
            dispatch(self.store, self.owner, '/workspace/analysis/validate', wrong)
        ref = prepared['selected_editions'][0]
        self.evidence.review(self.reviewer, ref['artifact_id'], 'revoked', 'Учебный отзыв.', self.attestations)
        with self.assertRaises(WorkspaceError):
            dispatch(self.store, self.owner, '/workspace/analysis/validate', payload)

    def test_report_keeps_injected_text_in_literal_blocks(self):
        self.payload()
        injection = '```\n<script>alert(1)</script>\n````'
        doc = self.store.add_document(self.owner, self.matter, {'title': injection, 'document': CONTRACT + '\n' + injection})['document_id']
        prepared = self.with_edition(contract_document_id=doc)
        result = answer(prepared)
        self.assertIn('`````text\n', result['markdown'])
        self.assertFalse(result['legal_acceptance'])

    def test_pilot_25_tasks_are_opt_in_and_never_generate_approved_answers(self):
        tasks = BenchmarkTasks(self.store)
        self.assertEqual(tasks.import_tasks(self.owner, self.matter)['created'], 90)
        cases = load_templates(__import__('benchmark_tasks').ROOT / 'fixtures/services_pilot_cases.json')
        self.assertEqual(len(cases), 25)
        result = tasks.import_tasks(self.owner, self.matter, list(cases))
        self.assertEqual(result['created'], 25)
        self.assertEqual(result['answers_generated'], 0)
        self.assertEqual(tasks.import_tasks(self.owner, self.matter, list(cases))['reused'], 25)
        self.assertEqual(tasks.list_tasks(self.owner, self.matter)['reviews_recorded'], 0)
        for row in cases.values():
            self.assertIsNone(row['approved_legal_answer'])
            self.assertFalse(row['review']['approved'])
            self.assertTrue(row['reviewer_checklist'])


class ServicePilotHttpTests(unittest.TestCase):
    setUp = http_tests.WorkspaceHttpTests.setUp
    cleanup = http_tests.WorkspaceHttpTests.cleanup
    request = http_tests.WorkspaceHttpTests.request
    matter = http_tests.WorkspaceHttpTests.matter

    def setup_payload(self):
        matter = self.matter()
        doc = self.request('/workspace/documents/add', {'matter_id': matter, 'title': 'СИНТЕТИЧЕСКИЙ договор', 'document': CONTRACT})[1]['document_id']
        return {'matter_id': matter, 'question': 'Оплата услуг', 'analysis_mode': MODE,
                'contract_document_id': doc, 'as_of': '2025-06-30', 'facts': FACT_VALUES, 'demo': False}

    def test_http_missing_sources_facts_context_and_access(self):
        payload = self.setup_payload()
        spec = self.request('/openapi.json', method='GET')[1]
        schema = spec['paths']['/workspace/ask']['post']['requestBody']['content']['application/json']['schema']
        self.assertEqual(schema['properties']['facts']['type'], 'object')
        check = spec['paths']['/workspace/analysis/validate']['post']['requestBody']['content']['application/json']['schema']
        self.assertIn('review_sha256', check['properties']['editions']['items']['required'])
        status, result = self.request('/workspace/ask', payload)
        self.assertEqual(status, 200); self.assertEqual(result['status'], 'needs_sources')
        self.assertFalse(result['model_called'])
        self.assertTrue(result['report_saved'])
        read_payload = {'matter_id': payload['matter_id'], 'report_id': result['report_id'],
                        'markdown_sha256': result['markdown_sha256']}
        read_schema = spec['paths']['/workspace/analysis/read']['post']['requestBody']['content']['application/json']['schema']
        self.assertEqual(set(read_schema['required']), set(read_payload))
        status, saved = self.request('/workspace/analysis/read', read_payload)
        self.assertEqual(status, 200); self.assertEqual(saved['markdown'], result['markdown'])
        self.assertTrue(saved['report_authenticity_verified']); self.assertFalse(saved['legal_acceptance'])
        self.assertEqual(self.request('/workspace/analysis/read', {**read_payload, 'markdown': 'FORGED'})[0], 400)
        self.assertEqual(self.request('/workspace/analysis/read', {**read_payload, 'markdown_sha256': 'f' * 64})[0], 409)
        self.assertEqual(self.request('/workspace/analysis/read', read_payload, token=self.other['token'])[0], 403)
        self.assertEqual(self.request('/workspace/evidence/read', {'artifact_id': result['report_id']})[0], 403)
        self.assertEqual(self.request('/workspace/ask', {**payload, 'facts': {}})[1]['status'], 'needs_facts')
        context = {'matter_id': payload['matter_id'], 'as_of': payload['as_of'], 'editions': [],
                   'documents': [{'document_id': row['document_id'], 'sha256': row['document_sha256']} for row in result['sources']]}
        self.assertEqual(self.request('/workspace/analysis/validate', context)[0], 200)
        self.assertEqual(self.request('/workspace/analysis/validate', context, token=self.other['token'])[0], 403)
        self.assertEqual(self.request('/workspace/ask', payload, token=self.other['token'])[0], 403)
        self.assertEqual(self.request('/workspace/analysis/validate', {**context, 'documents': []})[0], 400)

    def test_http_late_revocation_withholds_pilot_result(self):
        payload = self.setup_payload()
        self.store.grant(self.admin['user_id'], payload['matter_id'], self.other['user_id'], 'viewer')
        def finish(request_id, envelope, commit):
            self.store.grant(self.admin['user_id'], payload['matter_id'], self.other['user_id'], 'remove')
            return commit({'markdown': 'PRIVATE-LATE-PILOT'})
        with patch.object(self.server.jobs, 'execute', side_effect=finish):
            status, result = self.request('/workspace/ask', payload, token=self.other['token'])
        self.assertEqual(status, 403)
        self.assertNotIn('PRIVATE-', json.dumps(result))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts WHERE kind='analysis_report'").fetchone()[0], 0)

    def test_non_demo_http_worker_round_trip_with_synthetic_local_provider(self):
        from reviewed_evidence import ReviewedEvidence
        payload = self.setup_payload(); owner = self.admin['user_id']; matter = payload['matter_id']
        reviewer = self.store.create_user('СИНТЕТИЧЕСКИЙ эксперт', 'reviewer', actor=owner)['user_id']
        self.store.grant(owner, matter, reviewer, 'viewer')
        evidence = ReviewedEvidence(self.store)
        quote = 'Условная норма. Вступает в силу 01.01.2025. Изменяется с 01.01.2026.'
        doc = self.store.add_document(owner, matter, {'title': 'СИНТЕТИЧЕСКИЙ источник', 'document': quote,
            'source_url': 'https://publication.pravo.gov.ru/document/synthetic-not-real'})['document_id']
        edition = evidence.add(owner, matter, 'edition', {'act_id': 'synthetic', 'provision': '1', 'document_id': doc,
            'provision_quote': 'Условная норма.',
            'valid_from': '2025-01-01', 'valid_to': '2026-01-01', 'full_text_declared': True,
            'period_evidence': [{'document_id': doc, 'quote': quote}]})['artifact_id']
        evidence.review(reviewer, edition, 'approved', 'СИНТЕТИЧЕСКАЯ проверка механизма; не приёмка права.',
                        dict.fromkeys(['source_authenticity', 'completeness', 'temporal_applicability', 'legal_reasoning'], True))
        calls = []
        class SyntheticProvider(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                context = json.loads(request['messages'][1]['content']); calls.append(context)
                legal = next(row for row in context['sources'] if row['edition_references'])
                draft = {'summary': 'СИНТЕТИЧЕСКИЙ ответ локального провайдера.', 'findings': [
                    {'kind': 'risk', 'assessment': 'Учебный риск для проверки транспорта.',
                     'applicability': 'Это тест структуры и цитат, не юридический вывод.', 'next_step': 'Проверить отдельно юристом.',
                     'citations': [{'source_id': 1, 'quote': 'Оплата после подписания акта.'},
                                   {'source_id': legal['source_id'], 'quote': 'Условная норма.'}]}]}
                body = json.dumps({'choices': [{'message': {'content': json.dumps(draft)}}]}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
        provider = ThreadingHTTPServer(('127.0.0.1', 0), SyntheticProvider)
        thread = threading.Thread(target=provider.serve_forever, daemon=True); thread.start()
        try:
            with patch.dict('os.environ', {'LLM_API_KEY': 'synthetic-key', 'LLM_MODEL': 'synthetic-model',
                            'LLM_BASE_URL': f'http://127.0.0.1:{provider.server_port}/v1'}):
                status, result = self.request('/workspace/ask', {**payload, 'editions': [{'act_id': 'synthetic', 'provision': '1'}]})
            self.assertEqual(status, 200); self.assertEqual(result['status'], 'draft')
            self.assertEqual(len(calls), 1)
            self.assertTrue(result['model_called'])
            self.assertFalse(result['demo']); self.assertFalse(result['legal_acceptance'])
            self.assertIn('Кандидат на правовой риск', result['markdown'])
            context = {'matter_id': matter, 'as_of': payload['as_of'], 'editions': result['selected_editions'],
                'documents': list({row['document_id']: {'document_id': row['document_id'], 'sha256': row['document_sha256']}
                                   for row in result['sources']}.values())}
            self.assertEqual(self.request('/workspace/analysis/validate', context)[0], 200)
            read_payload = {'matter_id': matter, 'report_id': result['report_id'], 'markdown_sha256': result['markdown_sha256']}
            read_status, stored = self.request('/workspace/analysis/read', read_payload)
            self.assertEqual(read_status, 200); self.assertEqual(stored['markdown'], result['markdown'])
            evidence.review(reviewer, edition, 'revoked', 'СИНТЕТИЧЕСКИЙ отзыв.',
                            dict.fromkeys(['source_authenticity', 'completeness', 'temporal_applicability', 'legal_reasoning'], True))
            self.assertEqual(self.request('/workspace/analysis/read', read_payload)[0], 409)
        finally:
            provider.shutdown(); provider.server_close(); thread.join(3)
