"""Isolated browser acceptance with synthetic demo inputs and honest blocked results."""
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api import create_server


def run_browser_acceptance(timeout_seconds=180):
    base = {'scope': 'synthetic_demo_browser_acceptance', 'browser_acceptance': False,
            'external_providers_tested': False, 'legal_acceptance': False}
    node = shutil.which('node')
    if not node:
        return {**base, 'status': 'blocked', 'reason': 'node_missing'}
    try:
        probe = subprocess.run([node, '-e',
            "const fs=require('node:fs');try{const {chromium}=require('playwright');"
            "const p=process.env.BROWSER_SMOKE_EXECUTABLE_PATH||chromium.executablePath();"
            "console.log(JSON.stringify({dependency:true,browser:fs.existsSync(p)}));}"
            "catch{console.log(JSON.stringify({dependency:false,browser:false}));}"],
            cwd=ROOT, capture_output=True, text=True, timeout=15)
        available = json.loads(probe.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {**base, 'status': 'blocked', 'reason': 'browser_probe_failed'}
    if not available.get('dependency'):
        return {**base, 'status': 'blocked', 'reason': 'playwright_missing'}
    if not available.get('browser'):
        return {**base, 'status': 'blocked', 'reason': 'chromium_missing'}
    with tempfile.TemporaryDirectory(prefix='legal-browser-acceptance-') as directory:
        token = secrets.token_urlsafe(24)
        server = create_server('127.0.0.1', 0, directory, token, persist_results=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        process = None
        try:
            env = {**os.environ, 'BROWSER_SMOKE_URL': f'http://127.0.0.1:{server.server_port}',
                   'BROWSER_SMOKE_TOKEN': token, 'PYTHON': sys.executable}
            process = subprocess.Popen([node, str(ROOT / 'scripts/browser_smoke.cjs')],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=os.name == 'posix')
            try:
                stdout, _ = process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                return {**base, 'status': 'failed', 'reason': 'browser_timeout'}
            if process.returncode:
                return {**base, 'status': 'failed', 'reason': 'browser_checks_failed'}
            try:
                result = json.loads(stdout)
            except ValueError:
                return {**base, 'status': 'failed', 'reason': 'invalid_browser_report'}
            if (result.get('status') != 'passed' or result.get('real_browser') is not True
                    or result.get('synthetic_demo_only') is not True
                    or not isinstance(result.get('checks'), list) or not result['checks']):
                return {**base, 'status': 'failed', 'reason': 'incomplete_browser_report'}
            return {**base, 'status': 'passed', 'browser_acceptance': True, 'checks': result['checks']}
        finally:
            if process is not None:
                if os.name == 'posix':
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif process.poll() is None:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                process.communicate()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    report = run_browser_acceptance()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['status'] == 'passed' else 2)
