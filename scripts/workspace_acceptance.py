"""Isolated HTTP acceptance with synthetic accounts and documents; no real provider calls."""
import base64
import http.client
import hashlib
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
            original_card = post('/workspace/evidence/read', {'artifact_id': drafted['artifact_id']})
            citation = original_card['payload']['outcome']
            revision = {'artifact_id': drafted['artifact_id'], 'positions': [{'side':'distinguishing',
                'citation': {'document_id': citation['document_id'], 'quote':citation['quote']},
                'applicability_note': 'Синтетическая заметка проверки API; юридическая применимость не установлена.'}], 'links': []}
            post('/workspace/cases/revise', revision, other['token'], 403)
            revised = post('/workspace/cases/revise', revision)
            assert revised['review_status'] == 'draft' and revised['legal_acceptance'] is False
            post('/workspace/cases/revise', revision, expected=409)
            assert post('/workspace/sources/decision-draft', {'matter_id': matter, 'source_id': decision['id']})['artifact_id'] == revised['artifact_id']
            timeline = post('/workspace/cases/timeline', {'matter_id': matter, 'case_number': decision['case_number'], 'as_of': decision['signed_on']})
            assert [row['id'] for row in timeline['decisions']] == [revised['artifact_id']]
            assert timeline['superseded_versions_excluded'] == 1 and timeline['complete_history'] is False
            report_args = {'matter_id': matter, 'case_number': decision['case_number'], 'as_of': decision['signed_on']}
            report = post('/workspace/cases/report', report_args)
            assert report['report']['act_count'] == 1 and report['report']['legal_acceptance'] is False
            assert report['report']['acts'][0]['artifact_id'] == revised['artifact_id']
            assert report['report']['schema_version'] == 2
            assert len(report['report']['acts'][0]['referenced_acts']) == 2
            assert all(not ref['original_act_automatically_verified'] for ref in report['report']['acts'][0]['referenced_acts'])
            assert 'Пробел: отдельная карточка' in report['markdown']
            assert report['markdown_sha256'] == hashlib.sha256(report['markdown'].encode('utf-8')).hexdigest()
            assert post('/workspace/cases/report', report_args, other['token'])['report']['act_count'] == 1
            outsider = post('/workspace/users/create', {'name': 'Синтетический посторонний'})
            post('/workspace/cases/report', report_args, outsider['token'], 403)
            empty = post('/workspace/cases/report', {**report_args, 'as_of': '2010-01-01'})
            assert empty['report']['act_count'] == 0 and empty['report']['excluded']['future_acts'] == 1
            post('/workspace/cases/report', {**report_args, 'as_of': '2025-02-30'}, expected=400)
            post('/workspace/cases/report', report_args, 'invalid-token', 401)

            post('/workspace/sources/decision-draft', {'matter_id': matter, 'source_id': decision['id']}, other['token'], 403)
            post('/workspace/sources/decision-draft', {'matter_id': matter, 'source_id': catalog['sources'][0]['id']}, expected=400)
            assert post('/workspace/sources/import', {'matter_id': matter, 'source_id': catalog['sources'][0]['id']})['reused'] is True
            plain = post('/documents/export', {'document': 'Цена отчёта 12500 рублей.'})
            redline_payload = {'filename':'original.docx','file_base64':plain['document_base64'],
                               'replacements':[{'quote':'12500 рублей','replacement':'15000 рублей'}]}
            tracked = post('/documents/redline', redline_payload)
            assert tracked['word_track_changes'] is True and tracked['other_parts_preserved'] is True
            post('/documents/redline', {**redline_payload,'file_base64':tracked['document_base64']}, expected=400)

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
            pilot_args = {'matter_id': matter, 'question': 'Оплата', 'analysis_mode': 'services_pilot',
                          'contract_document_id': document, 'as_of': '2025-01-01', 'demo': False,
                          'facts': {'both_businesses': True, 'paid_activity': True, 'mixed_contract': False, 'special_regime': False}}
            pilot = post('/workspace/ask', pilot_args)
            assert pilot['status'] == 'needs_sources' and pilot['model_called'] is False
            learning_args = {'matter_id': matter, 'question': 'Оплата', 'as_of': '2025-01-01'}
            memory = post('/workspace/learning/preview', learning_args)
            assert memory['used_count'] == 0 and memory['weights_updated'] is False
            assert post('/workspace/quality/sets/list', {'matter_id': matter})['sets'] == []
            post('/workspace/quality/sets/create', {'matter_id': matter, 'title': 'Пустой контроль', 'benchmark_ids': []}, expected=400)
            post('/workspace/quality/sets/list', {'matter_id': matter}, outsider['token'], 403)
            post('/workspace/quality/compare', {'matter_id': matter, 'set_id': 'missing', 'baseline_label': 'v1', 'candidate_label': 'v2'}, expected=403)
            post('/workspace/learning/preview', learning_args, outsider['token'], 403)
            post('/workspace/learning/preview', {**learning_args, 'learning_exclude_artifact_ids': 'bad'}, expected=400)
            assert pilot['report_saved'] is True
            read_args = {'matter_id': matter, 'report_id': pilot['report_id'], 'markdown_sha256': pilot['markdown_sha256']}
            stored_report = post('/workspace/analysis/read', read_args)
            assert stored_report['markdown'] == pilot['markdown'] and stored_report['report_authenticity_verified'] is True
            assert stored_report['legal_acceptance'] is False and stored_report['digital_signature'] is False
            post('/workspace/analysis/read', {**read_args, 'markdown': 'FORGED CLIENT BYTES'}, expected=400)
            post('/workspace/analysis/read', {**read_args, 'markdown_sha256': 'f' * 64}, expected=409)
            post('/workspace/analysis/read', read_args, outsider['token'], 403)
            post('/workspace/evidence/read', {'artifact_id': pilot['report_id']}, expected=403)
            assert post('/workspace/ask', {**pilot_args, 'facts': {}})['status'] == 'needs_facts'
            context = {'matter_id': matter, 'as_of': pilot_args['as_of'], 'editions': [],
                       'documents': list({row['document_id']: {'document_id': row['document_id'], 'sha256': row['document_sha256']}
                                          for row in pilot['sources']}.values())}
            assert post('/workspace/analysis/validate', context)['context_current'] is True
            post('/workspace/ask', pilot_args, outsider['token'], 403)
            assert post('/workspace/benchmarks/import', {'matter_id': matter,
                'case_ids': [f'SERV-P{index:02}' for index in range(1, 26)]})['created'] == 25
            pilot_tasks = post('/workspace/benchmarks/list', {'matter_id': matter})
            assert pilot_tasks['total'] == 115 and pilot_tasks['reviews_recorded'] == 0
            pilot_task = next(row for row in pilot_tasks['tasks'] if row['case_id'] == 'SERV-P01')
            assert post('/workspace/benchmarks/read', {'task_id': pilot_task['task_id']})['source_case']['approved_legal_answer'] is None
            reviewer = post('/workspace/users/create', {'name': 'СИНТЕТИЧЕСКИЙ эксперт оценок', 'role': 'reviewer'})
            post('/workspace/members/set', {'matter_id': matter, 'user_id': reviewer['user_id'], 'role': 'viewer'})
            review_args = {'artifact_id': saved['artifact_id'], 'decision': 'approved',
                           'note': 'СИНТЕТИЧЕСКАЯ проверка механизма, не юридическая приёмка.',
                           'attestations': dict.fromkeys(['source_authenticity','completeness','temporal_applicability','legal_reasoning'], True)}
            post('/workspace/evidence/review', review_args, reviewer['token'])
            score_args = {'artifact_id': saved['artifact_id'], 'answer': 'Учебный ответ первой версии.',
                          'correctness': 2, 'missed_risks': [0], 'unsupported_claim_count': 1,
                          'note': 'СИНТЕТИЧЕСКАЯ оценка, не проверка права.', 'run_label': 'synthetic-v1'}
            post('/workspace/evaluations/record', score_args, reviewer['token'])
            post('/workspace/evaluations/record', {**score_args,'answer':'Учебный ответ второй версии.',
                'correctness':4,'missed_risks':[],'unsupported_claim_count':0,'run_label':'synthetic-v2'}, reviewer['token'])
            quality = post('/workspace/evaluations/metrics', {'matter_id': matter})
            assert quality['shared_benchmark_count'] == 1 and len(quality['shared_benchmark_comparison']) == 2
            post('/workspace/evaluations/record', score_args, other['token'], 403)
            post('/workspace/evidence/review', {**review_args, 'decision':'revoked'}, reviewer['token'])
            post('/workspace/evidence/review', review_args, reviewer['token'])
            quality = post('/workspace/evaluations/metrics', {'matter_id': matter})
            assert quality['human_evaluations'] == 0 and quality['excluded_due_to_review_change'] == 2
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
