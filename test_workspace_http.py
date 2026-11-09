import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from api import create_server
from workspace_api import namespace
from workspace_store import WorkspaceStore


class WorkspaceHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'workspace.db'
        self.store = WorkspaceStore(self.path)
        self.admin = self.store.create_user('Admin', 'admin')
        self.other = self.store.create_user('Member', actor=self.admin['user_id'])
        self.server = create_server('127.0.0.1', 0, Path(self.directory.name) / 'results', 'legacy-shared-token', workspace_db=self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3); self.directory.cleanup()

    def request(self, path, payload=None, token=None, request_id=None, method='POST'):
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (self.admin['token'] if token is None else token)}
        if request_id:
            headers['X-Request-ID'] = request_id
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            connection.request(method, path, json.dumps(payload or {}).encode() if method == 'POST' else None, headers)
            response = connection.getresponse()
            return response.status, json.load(response)
        finally:
            connection.close()

    def matter(self):
        status, data = self.request('/workspace/matters/create', {'title': 'СИНТЕТИЧЕСКОЕ дело'})
        self.assertEqual(status, 200)
        return data['matter_id']

    def test_individual_auth_rejects_shared_token_and_missing_tokens(self):
        for token in ('', 'wrong', 'legacy-shared-token'):
            self.assertEqual(self.request('/workspace/me', token=token)[0], 401)
        self.assertEqual(self.request('/workspace/me')[1]['id'], self.admin['user_id'])
        self.assertEqual(self.request('/workspace/users/create', {'name': 'Escalation', 'role': 'admin'}, token=self.other['token'])[0], 403)

    def test_matter_membership_and_document_access_are_enforced(self):
        matter = self.matter()
        document = self.request('/workspace/documents/add', {'matter_id': matter, 'title': 'Договор', 'document': 'Оплата производится после подписания акта.'})[1]['document_id']
        self.assertEqual(self.request('/workspace/documents/read', {'document_id': document}, token=self.other['token'])[0], 403)
        self.assertEqual(self.request('/workspace/members/set', {'matter_id': matter, 'user_id': self.other['user_id'], 'role': 'viewer'})[0], 200)
        self.assertEqual(self.request('/workspace/documents/read', {'document_id': document}, token=self.other['token'])[0], 200)
        self.assertEqual(self.request('/workspace/documents/add', {'matter_id': matter, 'title': 'Denied', 'document': 'Запрещено'}, token=self.other['token'])[0], 403)

    def test_multiple_documents_are_retrieved_and_demo_quotes_are_grounded(self):
        matter = self.matter()
        for title, content in [('Договор', 'Оплата производится после подписания акта.'), ('Переписка', 'Акт не подписан; оплата отчёта обсуждается сторонами.')]:
            self.assertEqual(self.request('/workspace/documents/add', {'matter_id': matter, 'title': title, 'document': content})[0], 200)
        status, result = self.request('/workspace/ask', {'matter_id': matter, 'question': 'оплата акт', 'demo': True})
        self.assertEqual(status, 200)
        self.assertEqual(len({row['document_id'] for row in result['sources']}), 2)
        for finding in result['findings']:
            for ref in finding['citations']:
                document = self.store.document(self.admin['user_id'], ref['document_id'])
                self.assertEqual(document['text'][ref['start']:ref['end']], ref['quote'])
        self.assertFalse(result['legal_acceptance'])

    def test_request_ids_cancellation_and_sessions_are_namespaced_per_user(self):
        request_id = uuid.uuid4().hex
        self.assertEqual(self.request('/requests/cancel', {'request_id': request_id}, token=self.other['token'])[0], 200)
        self.assertEqual(self.request('/ask', {'question': 'PRIVATE-OWNER-QUESTION', 'demo': True, 'session_id': 'a'}, request_id=request_id)[0], 200)
        self.assertEqual(self.request('/ask', {'question': 'PRIVATE-OTHER-QUESTION', 'demo': True, 'session_id': 'a'}, token=self.other['token'], request_id=request_id)[0], 409)
        self.assertEqual(self.request('/ask', {'question': 'PRIVATE-OTHER-QUESTION', 'demo': True, 'session_id': 'a'}, token=self.other['token'])[0], 200)
        owner_context = self.server.sessions.context(namespace(self.admin['user_id'], 'a'))
        self.assertNotIn('PRIVATE-OTHER', owner_context)
        self.request('/sessions/delete', {'session_id': 'a'}, token=self.other['token'])
        self.assertIn('PRIVATE-OWNER', self.server.sessions.context(namespace(self.admin['user_id'], 'a')))

    def test_revocation_before_answer_commit_blocks_late_response(self):
        matter = self.matter()
        self.store.grant(self.admin['user_id'], matter, self.other['user_id'], 'viewer')
        def finish(request_id, envelope, commit):
            self.store.grant(self.admin['user_id'], matter, self.other['user_id'], 'remove')
            return commit({'summary': 'PRIVATE-LATE-ANSWER'})
        with patch.object(self.server.jobs, 'execute', side_effect=finish):
            status, data = self.request('/workspace/ask', {'matter_id': matter, 'question': 'оплата', 'demo': True}, token=self.other['token'])
        self.assertEqual(status, 403)
        self.assertNotIn('PRIVATE-', json.dumps(data))

    def test_original_source_import_and_document_tools_work_via_http(self):
        matter = self.matter()
        status, source = self.request('/workspace/sources/import', {'matter_id': matter, 'source_id': 'vsrf-plenum-49-2018'})
        self.assertEqual(status, 200)
        self.assertGreater(source['text_char_count'], 40000)
        archived = self.request('/workspace/documents/read', {'document_id': source['document_id']})[1]
        self.assertEqual(archived['provenance']['origin'], 'pinned_primary_archive')
        self.assertEqual(len(archived['provenance']['original_sha256']), 64)
        self.assertFalse(archived['provenance']['current_validity_verified'])
        status, parsed = self.request('/documents/parse', {'filename': 'test.txt', 'file_base64': base64.b64encode('Оплата после подписания акта.'.encode()).decode()})
        self.assertEqual(status, 200)
        self.assertIn('Оплата', parsed['document'])
        self.assertEqual(self.request('/documents/export', {'document': parsed['document']})[0], 200)
        self.assertEqual(self.request('/documents/redact', {'document': 'Email: test@example.org'})[1]['redaction_count'], 1)

    def test_workspace_api_rejects_unrecognized_fields_and_bad_types(self):
        for path, payload in [('/workspace/matters/create', {'title': []}),
                              ('/workspace/search', {'matter_id': [], 'query': 'test'}),
                              ('/workspace/ask', {'matter_id': 'id', 'question': 'test', 'demo': 'true'}),
                              ('/workspace/me', {'role': 'admin'}),
                              ('/documents/parse', {'filename': 'test.txt', 'file_base64': '!!!'})]:
            self.assertEqual(self.request(path, payload)[0], 400)

    def test_archive_catalog_and_repeat_import_are_available_without_duplicates(self):
        status, catalog = self.request('/workspace/sources/list')
        self.assertEqual(status, 200)
        self.assertEqual(len(catalog['sources']), 7)
        self.assertFalse(catalog['legal_acceptance'])
        matter = self.matter()
        payload = {'matter_id': matter, 'source_id': 'vsrf-plenum-54-2016'}
        first = self.request('/workspace/sources/import', payload)[1]
        second = self.request('/workspace/sources/import', payload)[1]
        self.assertEqual(first['document_id'], second['document_id'])
        self.assertTrue(second['reused'])
        search = self.request('/workspace/search', {'matter_id': matter, 'query': 'обязательств'})[1]
        self.assertTrue(search['matches'])
        self.assertEqual(search['matches'][0]['source_context']['origin'], 'pinned_primary_archive')
        self.assertFalse(search['matches'][0]['source_context']['current_validity_verified'])
        self.assertEqual(len(self.store.documents(self.admin['user_id'], matter)['documents']), 1)

    def test_archive_failure_is_503_without_breaking_health_or_document_work(self):
        from primary_sources import PrimarySourceError
        with patch('primary_sources.source_catalog', side_effect=PrimarySourceError('PRIVATE-PATH')):
            status, result = self.request('/workspace/sources/list')
        self.assertEqual(status, 503)
        self.assertEqual(result['code'], 'primary_archive_unavailable')
        self.assertNotIn('PRIVATE-', json.dumps(result))
        self.assertEqual(self.request('/health', method='GET')[0], 200)
        self.assertEqual(self.request('/workspace/matters/list')[0], 200)

    def test_uploaded_original_is_bound_to_parsed_text_and_protected_by_acl(self):
        import hashlib
        matter = self.matter()
        raw = 'Оплата отчёта после подписания акта.\n'.encode('utf-8')
        status, stored = self.request('/workspace/documents/upload', {'matter_id': matter,
            'filename': 'original.txt', 'file_base64': base64.b64encode(raw).decode(), 'title': 'Оригинал'})
        self.assertEqual(status, 200)
        doc = self.request('/workspace/documents/read', {'document_id': stored['document_id']})[1]
        self.assertEqual(doc['text'], raw.decode().strip())
        self.assertEqual(doc['provenance']['original_sha256'], hashlib.sha256(raw).hexdigest())
        status, original = self.request('/workspace/documents/original', {'document_id': stored['document_id']})
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(original['file_base64']), raw)
        self.assertEqual(self.request('/workspace/documents/original', {'document_id': stored['document_id']}, token=self.other['token'])[0], 403)

    def test_schema_and_assets_expose_the_new_workflow_without_leaking_database_path(self):
        status, spec = self.request('/openapi.json', method='GET')
        self.assertEqual(status, 200)
        self.assertIn('/workspace/evidence/review', spec['paths'])
        self.assertTrue(spec['paths']['/workspace/me']['post']['security'])
        self.assertNotIn(str(self.path), json.dumps(spec))
        for path in ('/workspace', '/workspace.js'):
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
            connection.request('GET', path)
            response = connection.getresponse(); body = response.read()
            self.assertEqual(response.status, 200)
            self.assertTrue(body)
            connection.close()


    def test_case_report_http_schema_limits_and_revoked_access(self):
        matter = self.matter()
        args = {'matter_id': matter, 'case_number': 'А56-119496/2023', 'as_of': '2025-09-04'}
        self.assertEqual(self.request('/workspace/sources/decision-draft', {'matter_id':matter, 'source_id':'vsrf-decision-2485538'})[0],200)
        status, report = self.request('/workspace/cases/report',args)
        self.assertEqual(status,200)
        self.assertEqual(report['report']['act_count'],1)
        self.assertIn('Материалы судебного дела',report['markdown'])
        self.assertEqual(self.request('/workspace/cases/report',args,token=self.other['token'])[0],403)
        with patch('case_report.MAX_REPORT_BYTES',0):
            status, failure = self.request('/workspace/cases/report',args)
        self.assertEqual(status,413)
        self.assertEqual(failure['code'],'case_report_limit')
        self.assertEqual(self.request('/workspace/cases/report',{**args,'unexpected':True})[0],400)
        self.assertEqual(self.request('/workspace/cases/report',{**args,'as_of':'2025-02-30'})[0],400)
        self.store.grant(self.admin['user_id'],matter,self.other['user_id'],'viewer')
        self.assertEqual(self.request('/workspace/cases/report',args,token=self.other['token'])[0],200)
        self.store.grant(self.admin['user_id'],matter,self.other['user_id'],'remove')
        status, blocked = self.request('/workspace/cases/report',args,token=self.other['token'])
        self.assertEqual(status,403)
        self.assertNotIn('markdown',blocked)
        self.assertNotIn(str(self.path),json.dumps(blocked))
        spec = self.request('/openapi.json',method='GET')[1]['paths']['/workspace/cases/report']['post']
        self.assertEqual(set(spec['requestBody']['content']['application/json']['schema']['required']),{'matter_id','case_number','as_of'})
        self.assertIn('413',spec['responses'])
