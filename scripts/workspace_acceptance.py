"""Isolated HTTP acceptance with synthetic accounts and documents; no real provider calls."""
import base64
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api import create_server, API_VERSION
from workspace_store import WorkspaceStore


def run_acceptance():
    checks = 0
    with tempfile.TemporaryDirectory(prefix='legal-workspace-acceptance-') as directory:
        store = WorkspaceStore(Path(directory) / 'workspace.db')
        admin = store.create_user('Синтетический администратор', 'admin')
        server = create_server('127.0.0.1', 0, Path(directory) / 'results', workspace_db=store.path)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def post(path, payload, token=None, expected=200):
            nonlocal checks
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=15)
            try:
                connection.request('POST', path, json.dumps(payload).encode(),
                    {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (token or admin['token'])})
                response = connection.getresponse(); data = json.load(response)
                if response.status != expected:
                    raise ValueError('unexpected_http_status')
                checks += 1
                return data
            finally:
                connection.close()
        try:
            post('/workspace/me', {})
            other = post('/workspace/users/create', {'name': 'Синтетический участник'})
            matter = post('/workspace/matters/create', {'title': 'Синтетическое дело'})['matter_id']
            document = post('/workspace/documents/add', {'matter_id': matter, 'title': 'Договор', 'document': 'Цена отчёта 12500 рублей. Оплата после подписания акта.'})['document_id']
            post('/workspace/documents/add', {'matter_id': matter, 'title': 'Переписка', 'document': 'Стороны обсуждают оплату отчёта. Акт не подписан.'})
            result = post('/workspace/ask', {'matter_id': matter, 'question': 'оплата акт отчёт', 'demo': True})
            assert len({row['document_id'] for row in result['sources']}) == 2 and result['legal_acceptance'] is False
            post('/workspace/documents/read', {'document_id': document}, other['token'], 403)
            post('/workspace/members/set', {'matter_id': matter, 'user_id': other['user_id'], 'role': 'viewer'})
            post('/workspace/documents/read', {'document_id': document}, other['token'])
            post('/workspace/documents/add', {'matter_id': matter, 'title': 'Запрещено', 'document': 'Нет прав'}, other['token'], 403)
            catalog = post('/workspace/sources/list', {})
            for source in catalog['sources']:
                post('/workspace/sources/import', {'matter_id': matter, 'source_id': source['id']})
            decision = next(row for row in catalog['sources'] if row['material_type'] == 'official_court_decision')
            drafted = post('/workspace/sources/decision-draft', {'matter_id': matter, 'source_id': decision['id']})
            assert drafted['review_status'] == 'draft' and drafted['legal_acceptance'] is False
            assert post('/workspace/sources/decision-draft', {'matter_id': matter, 'source_id': decision['id']})['reused'] is True
            post('/workspace/sources/decision-draft', {'matter_id': matter, 'source_id': decision['id']}, other['token'], 403)
            post('/workspace/sources/decision-draft', {'matter_id': matter, 'source_id': catalog['sources'][0]['id']}, expected=400)
            assert post('/workspace/sources/import', {'matter_id': matter, 'source_id': catalog['sources'][0]['id']})['reused'] is True
            post('/documents/export', {'document': 'Цена отчёта 12500 рублей.'})
            assert post('/documents/redact', {'document': 'Контакт: test@example.org'})['redaction_count'] == 1
            post('/documents/parse', {'filename': 'example.txt', 'file_base64': base64.b64encode('Условный документ'.encode()).decode()})
            upload = post('/workspace/documents/upload', {'matter_id': matter, 'filename': 'original.txt',
                'file_base64': base64.b64encode('Синтетический оригинал документа.'.encode()).decode()})
            original = post('/workspace/documents/original', {'document_id': upload['document_id']})
            assert base64.b64decode(original['file_base64']).decode() == 'Синтетический оригинал документа.'
            post('/workspace/users/list', {})
            post('/workspace/evidence/list', {'matter_id': matter})
            imported = post('/workspace/benchmarks/import', {'matter_id': matter})
            assert imported['created'] == 90 and imported['answers_generated'] == 0
            assert post('/workspace/benchmarks/import', {'matter_id': matter})['reused'] == 90
            listing = post('/workspace/benchmarks/list', {'matter_id': matter})
            assert listing['needs_expert_answer'] == 90 and listing['reviews_recorded'] == 0
            task = listing['tasks'][0]['task_id']
            post('/workspace/benchmarks/read', {'task_id': task})
            draft = {'task_id': task, 'expected_answer': 'Учебный ответ для проверки механизма.',
                     'required_risks': ['Учебный риск'], 'forbidden_claims': [],
                     'citations': [{'document_id': document, 'quote': 'Оплата после подписания акта.'}]}
            saved = post('/workspace/benchmarks/draft', draft)
            assert saved['review_status'] == 'draft' and saved['legal_acceptance'] is False
            post('/workspace/benchmarks/draft', draft, expected=409)
            post('/workspace/benchmarks/draft', draft, other['token'], 403)
            post('/workspace/ask', {'matter_id': matter, 'question': 'Оплата', 'demo': True,
                 'as_of': '2025-01-01', 'editions': [{'act_id': 'not-imported', 'provision': '1'}]}, expected=409)
            post('/workspace/audit', {'matter_id': matter})
            assert store.verify_audit()
        finally:
            server.shutdown(); server.server_close(); thread.join(3)
    return {'status': 'passed', 'api_version': API_VERSION, 'http_checks': checks,
            'scope': 'temporary_synthetic_workspace_http', 'external_providers_tested': False,
            'browser_acceptance': False, 'legal_acceptance': False, 'credentials_in_report': False}


if __name__ == '__main__':
    try:
        print(json.dumps(run_acceptance(), ensure_ascii=False, indent=2))
    except Exception:
        print(json.dumps({'status': 'failed', 'code': 'workspace_acceptance_failed', 'legal_acceptance': False}))
        raise SystemExit(1)
