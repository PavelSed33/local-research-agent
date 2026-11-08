"""Real HTTP and child-process tests with a deliberately stalled local LLM."""
import json
import os
from pathlib import Path
import tempfile
import subprocess
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from api import create_server
from request_jobs import RequestJobs


class RequestControlTests(unittest.TestCase):
    def setUp(self):
        self.started = threading.Event()
        self.release = threading.Event()
        owner = self
        class SlowProvider(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                owner.started.set()
                owner.release.wait(10)
                try:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{}')
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *args):
                pass
        self.provider = ThreadingHTTPServer(('127.0.0.1', 0), SlowProvider)
        threading.Thread(target=self.provider.serve_forever, daemon=True).start()
        self.env = patch.dict(os.environ, {
            'LLM_BASE_URL': f'http://127.0.0.1:{self.provider.server_port}/v1',
            'LLM_API_KEY': 'synthetic-only', 'LLM_MODEL': 'synthetic-model',
            'TAVILY_API_KEY': 'synthetic-only',
            'TAVILY_API_URL': f'http://127.0.0.1:{self.provider.server_port}/search',
            'ALLOW_CLOUD_DOCUMENTS': 'true'})
        self.env.start()
        self.children = []
        spawn = subprocess.Popen
        def track(*args, **kwargs):
            process = spawn(*args, **kwargs)
            self.children.append((process, Path(kwargs['env']['TMPDIR'])))
            return process
        self.spawn_patch = patch('request_jobs.subprocess.Popen', side_effect=track)
        self.spawn_patch.start()
        self.directory = tempfile.TemporaryDirectory()
        self.server = create_server('127.0.0.1', 0, self.directory.name, 'test-token', request_timeout_seconds=4)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.server_close()
        self.server.shutdown()
        self.release.set()
        self.provider.shutdown()
        self.provider.server_close()
        self.directory.cleanup()
        self.env.stop()
        self.spawn_patch.stop()

    def post(self, path, payload, request_id=None, token='test-token'):
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}
        if request_id:
            headers['X-Request-ID'] = request_id
        request = urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}{path}',
            data=json.dumps(payload).encode(), headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=8)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response), response.headers

    def slow(self, request_id, results):
        results.append(self.post('/extract', {'document': 'Условный договор для проверки отмены.'}, request_id))

    def test_deadline_kills_blocked_provider_work_and_next_request_succeeds(self):
        self.server.jobs.timeout_seconds = 0.8
        started = time.monotonic()
        status, data, headers = self.post('/extract', {'document': 'Условный договор для проверки дедлайна.'}, uuid.uuid4().hex)
        self.assertEqual(status, 504)
        self.assertEqual(data['code'], 'request_deadline')
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(self.started.is_set())
        self.assertFalse(self.server.jobs.active)
        for process, directory in self.children:
            self.assertIsNotNone(process.poll())
            self.assertFalse(directory.exists())
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        self.assertEqual(self.post('/ask', {'question': 'Тест', 'demo': True})[0], 200)

    def test_cancel_stalled_work_requires_auth_and_no_late_result_is_published(self):
        request_id = uuid.uuid4().hex
        results = []
        thread = threading.Thread(target=self.slow, args=(request_id, results))
        thread.start()
        self.assertTrue(self.started.wait(3))
        self.assertEqual(self.post('/requests/cancel', {'request_id': request_id}, token='wrong')[0], 401)
        self.assertTrue(thread.is_alive())
        status, data, _ = self.post('/requests/cancel', {'request_id': request_id})
        self.assertEqual(status, 200)
        self.assertTrue(data['cancelled'])
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0][0], 409)
        self.assertEqual(results[0][1]['code'], 'request_cancelled')
        self.assertEqual(results[0][2]['X-Request-ID'], request_id)
        self.assertFalse(self.server.jobs.active)
        self.assertEqual(self.server.sessions.context('a'), '')
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_skill_work_is_cancellable_and_does_not_persist_document(self):
        key, results = uuid.uuid4().hex, []
        thread = threading.Thread(target=lambda: results.append(self.post('/legal/skills/run', {
            'skill_id': 'contract-risk', 'document': 'Условный договор для проверки отмены навыка.',
            'user_position': 'Заказчик'}, key)))
        thread.start()
        self.assertTrue(self.started.wait(3))
        self.assertEqual(self.post('/requests/cancel', {'request_id': key})[0], 200)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0][0], 409)
        self.assertFalse(self.server.jobs.active)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_cancelled_research_cannot_repopulate_deleted_session_or_keep_reports(self):
        self.server.persist_results = True
        key = uuid.uuid4().hex
        results = []
        thread = threading.Thread(target=lambda: results.append(self.post('/ask', {
            'question': 'Условный вопрос для проверки отмены', 'session_id': 'a'}, key)))
        thread.start()
        self.assertTrue(self.started.wait(3))
        self.assertEqual(self.post('/sessions/delete', {'session_id': 'a'})[0], 200)
        self.assertEqual(self.post('/requests/cancel', {'request_id': key})[0], 200)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0][1]['code'], 'request_cancelled')
        self.assertEqual(self.server.sessions.context('a'), '')
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        for process, directory in self.children:
            self.assertIsNotNone(process.poll())
            self.assertFalse(directory.exists())

    def test_early_cancel_and_duplicate_ids_do_not_execute_again(self):
        key = uuid.uuid4().hex
        self.assertTrue(self.post('/requests/cancel', {'request_id': key})[1]['cancelled'])
        self.assertEqual(self.post('/ask', {'question': 'Тест', 'demo': True}, key)[1]['code'], 'request_cancelled')
        key = uuid.uuid4().hex
        self.assertEqual(self.post('/ask', {'question': 'Тест', 'demo': True}, key)[0], 200)
        self.assertFalse(self.post('/requests/cancel', {'request_id': key})[1]['cancelled'])
        self.assertEqual(self.post('/ask', {'question': 'Тест', 'demo': True}, key)[0], 409)
        self.assertEqual(self.post('/requests/cancel', {'request_id': '../bad'})[0], 400)
        self.assertEqual(self.post('/ask', {'question': 'Тест', 'demo': True}, 'bad')[0], 400)

    def test_capacity_keeps_control_handler_available(self):
        self.server.jobs.max_active = 3
        results = []
        keys = [uuid.uuid4().hex for _ in range(3)]
        threads = [threading.Thread(target=self.slow, args=(key, results)) for key in keys]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 3
        while len(self.server.jobs.active) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.server.jobs.active), 3)
        self.assertEqual(self.post('/ask', {'question': 'Тест', 'demo': True})[0], 429)
        for key in keys:
            self.assertEqual(self.post('/requests/cancel', {'request_id': key})[0], 200)
        for thread in threads:
            thread.join(2)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r[0] == 409 for r in results))

    def test_persistence_is_published_only_after_success(self):
        self.server.persist_results = True
        status, data, _ = self.post('/ask', {'question': 'Тест', 'demo': True, 'session_id': 'a'})
        self.assertEqual(status, 200)
        self.assertTrue(Path(data['report_file']).is_file())
        self.assertTrue(Path(data['journal_file']).is_file())
        self.assertIn('Тест', self.server.sessions.context('a'))

    def test_shutdown_cancels_and_reaps_inflight_work(self):
        results = []
        thread = threading.Thread(target=self.slow, args=(uuid.uuid4().hex, results))
        thread.start()
        self.assertTrue(self.started.wait(3))
        self.server.server_close()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0][1]['code'], 'request_cancelled')
        self.assertFalse(self.server.jobs.active)

    def test_timeout_configuration_is_bounded(self):
        for value in (0, -1, 3601, True, float('nan'), float('inf'), '5'):
            with self.assertRaises(ValueError):
                RequestJobs(value)


@unittest.skipUnless(os.name == 'posix', 'POSIX process-group cleanup test')
class AcceptanceProcessTreeTests(unittest.TestCase):
    def test_acceptance_deadline_also_stops_its_api_child_process(self):
        from scripts.acceptance import _run_isolated
        import sys
        import socket
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            listener.settimeout(3)
            child_code = ('import socket,time; '
                f's=socket.create_connection(("127.0.0.1",{listener.getsockname()[1]})); '
                'time.sleep(30)')
            command = [sys.executable, '-c',
                'import subprocess,sys,time; '
                'p=subprocess.Popen([sys.executable,"-c",sys.argv[1]]); time.sleep(30)', child_code]
            with self.assertRaises(subprocess.TimeoutExpired):
                _run_isolated(command, dict(os.environ), 0.8)
            # The grandchild holds the connection while alive; EOF proves it exited.
            # Avoid /proc PID inspection: host and task PID namespaces may differ.
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(2)
                self.assertEqual(connection.recv(1), b'')
