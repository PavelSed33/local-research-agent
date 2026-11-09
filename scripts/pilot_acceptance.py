"""One isolated technical readiness report for a supervised pilot; no legal approval."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import current_check_date, load_dotenv
from api import API_VERSION
from preflight import inspect_configuration
from scripts.acceptance import _run_isolated, _save

LOCAL_CHECKS = ('data', 'archive', 'demo', 'workspace', 'recovery', 'ui_logic')
CHECKS = LOCAL_CHECKS + ('browser', 'live')


def valid_result(identity, result):
    if (not isinstance(result, dict) or result.get('id') != identity
            or result.get('status') not in ('passed', 'failed', 'blocked')):
        return False
    if result['status'] != 'passed':
        return True
    def positive(name):
        return type(result.get(name)) is int and result[name] > 0
    if identity == 'data':
        return positive('statutory_extracts') and positive('draft_synthetic_cases')
    if identity == 'archive':
        return positive('sources') and positive('pages') and result.get('native_pdf_replay') is True
    if identity in ('demo', 'live'):
        live = identity == 'live'
        return (positive('http_checks') and positive('checks_passed')
                and result.get('checks_failed') == 0 and result.get('checks_blocked') == 0
                and result.get('live_acceptance_passed') is live
                and result.get('external_requests_attempted') is live)
    if identity in ('workspace', 'recovery'):
        return positive('http_checks') and result.get('temporary_synthetic_accounts_and_db') is True
    if identity == 'ui_logic':
        return positive('scenarios') and result.get('browser_acceptance') is False
    if identity == 'browser':
        return positive('scenarios') and result.get('browser_acceptance') is True
    return False


def execute_check(identity):
    """Return only explicit counters/flags; never copy provider text or exceptions."""
    if identity == 'data':
        from scripts.validate_legal_data import validate
        result = validate()
        return {'status': 'passed', 'statutory_extracts': result['statutory_extracts'],
                'draft_synthetic_cases': result['draft_synthetic_cases']}
    if identity == 'archive':
        if not all(shutil.which(name) for name in ('pdfinfo', 'pdftotext')):
            return {'status': 'blocked', 'reason': 'poppler_missing'}
        from scripts.verify_primary_archive import verify
        result = verify(parse_pdfs=True)
        return {'status': 'passed' if result['status'] == 'passed' else 'failed',
                'sources': result['sources'], 'pages': result['total_pages'],
                'native_pdf_replay': result['native_pdf_replay']}
    if identity in ('demo', 'live'):
        from scripts.acceptance import run_acceptance
        result = run_acceptance(live=identity == 'live', timeout_seconds=600)
        return {'status': result['status'],
                'live_acceptance_passed': result['live_acceptance_passed'],
                'external_requests_attempted': result['external_requests_attempted'],
                'http_checks': sum(row.get('passed_http_checks', 0) for row in result['checks']),
                'checks_passed': sum(row['status'] == 'passed' for row in result['checks']),
                'checks_blocked': sum(row['status'] == 'blocked' for row in result['checks']),
                'checks_failed': sum(row['status'] == 'failed' for row in result['checks'])}
    if identity in ('workspace', 'recovery'):
        if identity == 'workspace':
            from scripts.workspace_acceptance import run_acceptance
        else:
            from scripts.backup_acceptance import run_acceptance
        result = run_acceptance()
        return {'status': result['status'], 'http_checks': result['http_checks'],
                'temporary_synthetic_accounts_and_db': True}
    if identity == 'ui_logic':
        node = shutil.which('node')
        if not node:
            return {'status': 'blocked', 'reason': 'node_missing'}
        count = 0
        for script in ('test_workspace_report.cjs', 'test_workspace_context.cjs'):
            result = subprocess.run([node, str(ROOT / 'scripts' / script)], cwd=ROOT,
                                    capture_output=True, text=True, timeout=20)
            if result.returncode:
                return {'status': 'failed', 'reason': 'ui_logic_failed'}
            report = json.loads(result.stdout)
            if (report.get('status') != 'passed' or report.get('browser_acceptance') is not False
                    or type(report.get('checks')) is not int or report['checks'] < 1):
                return {'status': 'failed', 'reason': 'invalid_ui_logic_report'}
            count += report['checks']
        return {'status': 'passed', 'scenarios': count, 'browser_acceptance': False}
    if identity == 'browser':
        from scripts.browser_acceptance import run_browser_acceptance
        result = run_browser_acceptance(timeout_seconds=180)
        report = {'status': result['status'], 'browser_acceptance': result['browser_acceptance']}
        if result['status'] == 'passed':
            report['scenarios'] = len(result['checks'])
        else:
            # Browser runner reasons are fixed internal codes, never raw stderr.
            report['reason'] = result['reason']
        return report
    raise ValueError('unknown_check')


def run_check(identity, *, timeout_seconds=120):
    if identity not in CHECKS:
        raise ValueError('unknown_check')
    with tempfile.TemporaryDirectory(prefix='legal-pilot-check-') as directory:
        output = Path(directory) / 'result.json'
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', identity,
                   '--result', str(output)]
        environment = {**os.environ, 'TMPDIR': directory, 'TMP': directory, 'TEMP': directory}
        try:
            process = _run_isolated(command, environment, timeout_seconds)
            if process.returncode:
                return {'id': identity, 'status': 'failed', 'reason': 'worker_failed'}
            if output.stat().st_size > 64 * 1024:
                raise ValueError('oversized_report')
            result = json.loads(output.read_text(encoding='utf-8'))
            if not valid_result(identity, result):
                raise ValueError('invalid_report')
            return result
        except subprocess.TimeoutExpired:
            return {'id': identity, 'status': 'failed', 'reason': 'deadline_exceeded'}
        except (OSError, ValueError, TypeError):
            return {'id': identity, 'status': 'failed', 'reason': 'invalid_worker_report'}


def summarize(checks, configuration):
    """Missing, duplicated or partial checks must never make the pilot ready."""
    rows = {row['id']: row for row in checks}
    complete = len(rows) == len(checks) and set(rows) == set(CHECKS)
    local_ok = complete and all(rows[name]['status'] == 'passed' for name in LOCAL_CHECKS)
    browser_ok = (complete and rows['browser']['status'] == 'passed'
                  and rows['browser'].get('browser_acceptance') is True)
    live_ok = (complete and rows['live']['status'] == 'passed'
               and rows['live'].get('live_acceptance_passed') is True)
    gates = [{'id': 'lawyer_acceptance', 'status': 'unverified',
              'required': 'Independent lawyer approval of real tasks, source editions and pilot scope.'},
             {'id': 'target_deployment', 'status': 'unverified',
              'required': 'Verify target host, access, retention, backup schedule and recovery.'}]
    external = [row.get('external_requests_attempted', None if row['id'] == 'live' else False) for row in checks]
    attempted = True if True in external else None if None in external else False
    return {'schema_version': 1, 'api_version': API_VERSION, 'checked_on': current_check_date(),
            'scope': 'isolated_supervised_pilot_technical_checks',
            'status': 'failed' if not complete or any(row['status'] == 'failed' for row in checks) else 'blocked',
            'local_checks_passed': local_ok,
            'automated_checks_passed': bool(local_ok and browser_ok and live_ok),
            'production_ready': False, 'legal_acceptance': False,
            'browser_acceptance': browser_ok, 'live_acceptance_passed': live_ok,
            'external_requests_attempted': attempted,
            'configuration': configuration, 'checks': checks, 'manual_gates': gates,
            'limitations': ['No legal approval or target deployment approval is inferred from technical tests.',
                            'Browser smoke covers the demo UI; workspace logic checks are Node VM tests.',
                            'Only synthetic accounts/documents and temporary databases are used.',
                            'Existing production databases are not opened or modified.',
                            'Docker is not required for direct Python deployment and is not tested here.']}


def run_pilot(*, live=False):
    configuration = inspect_configuration()
    checks = [run_check(name, timeout_seconds=120) for name in LOCAL_CHECKS]
    checks.append(run_check('browser', timeout_seconds=210))
    if not live:
        checks.append({'id': 'live', 'status': 'blocked', 'reason': 'live_not_requested',
                       'external_requests_attempted': False, 'live_acceptance_passed': False})
    elif any(row['status'] != 'passed' for row in checks if row['id'] in LOCAL_CHECKS):
        checks.append({'id': 'live', 'status': 'blocked', 'reason': 'local_checks_incomplete',
                       'external_requests_attempted': False, 'live_acceptance_passed': False})
    elif configuration['missing_configuration'] or configuration['invalid_endpoint_configuration']:
        checks.append({'id': 'live', 'status': 'blocked', 'reason': 'missing_or_invalid_configuration',
                       'external_requests_attempted': False, 'live_acceptance_passed': False})
    else:
        checks.append(run_check('live', timeout_seconds=630))
    return summarize(checks, configuration)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true',
                        help='Run real model/search checks with synthetic inputs; API charges may apply.')
    parser.add_argument('--worker', choices=CHECKS, help=argparse.SUPPRESS)
    parser.add_argument('--result', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if args.result is None:
            parser.error('worker_result_required')
        try:
            report = execute_check(args.worker)
        except Exception:
            report = {'status': 'failed', 'reason': 'check_failed'}
        _save(args.result, {'id': args.worker, **report})
        return 0
    load_dotenv(ROOT / '.env')
    report = run_pilot(live=args.live)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report['status'] == 'failed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
