"""Run isolated local acceptance, optionally with configured live providers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import OpenAICompatibleModel, TavilySearch, extract_json, load_dotenv, current_check_date
from api import API_VERSION, create_server
from preflight import inspect_runtime
from scripts.smoke import smoke

DOCUMENT = ('Технический пример договора без персональных данных. '
            'ООО «Тест-А» оказывает ООО «Тест-Б» услуги по подготовке отчёта. '
            'Стоимость услуг составляет 1 000 рублей. Оплата производится после передачи отчёта.')


def initialize_environment(root=ROOT):
    """Create a local template exclusively; never overwrite existing credentials."""
    root = Path(root)
    template = (root / '.env.example').read_text(encoding='utf-8')
    try:
        descriptor = os.open(root / '.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(template)
    except FileExistsError:
        return False
    return True


def _save(path: Path, report: dict):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def _failure(exc):
    """Never include provider bodies, model output, keys or arbitrary error strings."""
    if isinstance(exc, TimeoutError):
        return {'reason': 'request_timeout'}
    cause = exc
    for _ in range(4):
        if isinstance(cause, urllib.error.HTTPError):
            return {'reason': 'http_error', 'http_status': cause.code}
        cause = getattr(cause, '__cause__', None)
        if cause is None:
            break
    return {'reason': 'check_failed', 'error_type': type(exc).__name__}


def _response_check(path, data):
    if not isinstance(data, dict):
        raise ValueError('Response must be an object')
    if path == '/ask':
        if not isinstance(data.get('answer'), str) or not data['answer'].strip():
            raise ValueError('Missing answer')
        return {'source_count': len(data.get('sources', []))}
    if path in {'/review', '/review/legal'}:
        if data.get('status') != 'draft' or not isinstance(data.get('risks'), list) or data.get('verified') is not False:
            raise ValueError('Invalid draft')
        if any(risk.get('verified') is not False for risk in data['risks']):
            raise ValueError('Untrusted risk verification')
        return {'risk_count': len(data['risks'])}
    if path == '/extract':
        if not isinstance(data.get('amounts'), list) or not data['amounts'] or data.get('verified') is not False:
            raise ValueError('Synthetic amount was not extracted')
        return {'amount_count': len(data['amounts'])}
    if path == '/case-law/check':
        if not isinstance(data.get('decisions'), list) or data.get('verified') is not False:
            raise ValueError('Invalid court result')
        return {'decision_count': len(data['decisions']), 'evidence_found': bool(data['decisions'])}
    raise ValueError('Unknown acceptance route')


def _finish(report):
    statuses = [check['status'] for check in report['checks']]
    required = {'local_demo_workflows'}
    if report['mode'] == 'live':
        required.update({'live_model_connection', 'live_search_connection',
                         'live /ask', 'live /review', 'live /extract', 'live /review/legal', 'live /case-law/check'})
    complete = required <= {item['id'] for item in report['checks'] if item['status'] == 'passed'}
    report['status'] = ('failed' if not statuses or 'failed' in statuses or 'running' in statuses else
                        'blocked' if 'blocked' in statuses else 'passed')
    if report['status'] == 'passed' and not complete:
        report['status'] = 'failed'
        report['checks'].append({'id': 'required_checks', 'status': 'failed', 'reason': 'incomplete_check_set'})
    report['live_acceptance_passed'] = report['mode'] == 'live' and report['status'] == 'passed'
    return report


def _worker(path: Path, live: bool):
    readiness = inspect_runtime()
    report = {'api_version': API_VERSION, 'mode': 'live' if live else 'demo', 'status': 'running',
              'checked_on': current_check_date(),
              'checks': [], 'configuration': {
                  'missing': readiness['missing_configuration'], 'invalid_endpoints': readiness['invalid_endpoint_configuration'],
                  'documents_permitted_by_configuration': readiness['document_transfer_permitted_by_configuration']},
              'external_requests_attempted': False, 'live_acceptance_passed': False,
              'legal_acceptance': False, 'browser_acceptance': False,
              'scope': 'technical_http_acceptance_with_synthetic_inputs'}
    _save(path, report)

    def check(identity, operation):
        item = {'id': identity, 'status': 'running'}
        report['checks'].append(item)
        _save(path, report)
        try:
            detail = operation()
            item.update(status='passed', **(detail or {}))
        except Exception as exc:
            item.update(status='failed', **_failure(exc))
        _save(path, report)
        return item['status'] == 'passed'

    token = secrets.token_urlsafe(32)
    os.environ['API_TOKEN'] = token  # Isolated child only, never the user's running server.
    with tempfile.TemporaryDirectory(prefix='server-', dir=path.parent) as directory:
        server = create_server('127.0.0.1', 0, directory, token, persist_results=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            demo_ok = check('local_demo_workflows', lambda: {'passed_http_checks': len(smoke(base))})
            if live:
                if not demo_ok:
                    report['checks'].append({'id': 'live_configuration', 'status': 'blocked',
                                             'reason': 'local_baseline_failed'})
                elif readiness['missing_configuration'] or readiness['invalid_endpoint_configuration']:
                    report['checks'].append({'id': 'live_configuration', 'status': 'blocked',
                                             'reason': 'missing_or_invalid_configuration'})
                else:
                    report['external_requests_attempted'] = True

                    def model_probe():
                        reply = extract_json(OpenAICompatibleModel().complete(
                            'Техническая проверка подключения. Верни только JSON {"ok":true}.', 'Верни {"ok":true}.'))
                        if reply.get('ok') is not True:
                            raise ValueError('Unexpected probe reply')
                        return {'reply_shape_checked': True}

                    def search_probe():
                        sources = TavilySearch().search('Гражданский кодекс РФ существенные условия договора', 2)
                        if not sources:
                            raise ValueError('Search returned no materials')
                        return {'source_count': len(sources)}

                    model_ok = check('live_model_connection', model_probe)
                    search_ok = check('live_search_connection', search_probe)
                    if model_ok and search_ok:
                        cases = [('/review', {'document': DOCUMENT}),
                                 ('/extract', {'document': DOCUMENT}),
                                 ('/review/legal', {'document': DOCUMENT, 'facts': {'both_businesses': True}}),
                                 ('/case-law/check', {'question': 'Существенные условия договора оказания услуг между организациями'}),
                                 ('/ask', {'question': 'Как исследовать существенные условия договора оказания услуг между организациями?',
                                           'queries': 1, 'sources': 2})]
                        for route, payload in cases:
                            if 'document' in payload and not readiness['document_transfer_permitted_by_configuration']:
                                report['checks'].append({'id': 'live ' + route, 'status': 'blocked',
                                                         'reason': 'document_transfer_not_permitted'})
                                _save(path, report)
                                continue

                            def request(route=route, payload=payload):
                                req = urllib.request.Request(base + route,
                                    data=json.dumps({**payload, 'demo': False}).encode('utf-8'),
                                    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
                                with urllib.request.urlopen(req, timeout=180) as response:
                                    result = json.load(response)
                                return _response_check(route, result)

                            check('live ' + route, request)
                            if report['checks'][-1].get('reason') == 'request_timeout':
                                # Exit the worker instead of starting more calls while its API handler is still running.
                                break
                    else:
                        report['checks'].append({'id': 'live_workflows', 'status': 'blocked', 'reason': 'provider_probe_failed'})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    _save(path, _finish(report))


def run_acceptance(*, live=False, timeout_seconds=600):
    if type(timeout_seconds) is not int or not 10 <= timeout_seconds <= 1800:
        raise ValueError('timeout_seconds: целое число от 10 до 1800.')
    with tempfile.TemporaryDirectory(prefix='legal-acceptance-') as directory:
        result_path = Path(directory) / 'report.json'
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', str(result_path)]
        if live:
            command.append('--live')
        try:
            environment = {**os.environ, 'TMPDIR': directory, 'TMP': directory, 'TEMP': directory}
            completed = subprocess.run(command, cwd=ROOT, env=environment,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout_seconds)
            failed = completed.returncode != 0
            timeout = False
        except subprocess.TimeoutExpired:
            failed, timeout = True, True
        try:
            report = json.loads(result_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            report = {'mode': 'live' if live else 'demo', 'checks': [], 'legal_acceptance': False,
                      'browser_acceptance': False, 'external_requests_attempted': False}
        if failed:
            for item in report['checks']:
                if item['status'] == 'running':
                    item.update(status='failed', reason='deadline_exceeded' if timeout else 'worker_failed')
            report['checks'].append({'id': 'acceptance_worker', 'status': 'failed',
                                     'reason': 'deadline_exceeded' if timeout else 'worker_failed'})
        report['deadline_seconds'] = timeout_seconds
        return _finish(report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true', help='Реальные запросы провайдерам только с техническими примерами; возможны расходы API.')
    mode.add_argument('--init-env', action='store_true', help='Создать .env из шаблона, только если его ещё нет; ключи не выводятся.')
    parser.add_argument('--timeout-seconds', type=int, default=600, help='Общий предел проверки; при превышении дочерний процесс завершается.')
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.init_env:
        print(json.dumps({'status': 'created' if initialize_environment() else 'already_exists',
                          'file': '.env', 'next_step': 'configure_environment'}, ensure_ascii=False))
        return 0
    if args.worker:
        _worker(args.worker, args.live)
        return 0
    load_dotenv(ROOT / '.env')
    if not 10 <= args.timeout_seconds <= 1800:
        parser.error('--timeout-seconds должен быть от 10 до 1800.')
    report = run_acceptance(live=args.live, timeout_seconds=args.timeout_seconds)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {'passed': 0, 'blocked': 2, 'failed': 1}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
