"""Bounded, interruptible subprocess work. No threads survive cancelled work."""
from collections import OrderedDict
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

ID_PATTERN = re.compile(r'^[A-Za-z0-9-]{16,64}$')


class JobError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code = status, code
        super().__init__(message)


class RequestJobs:
    def __init__(self, timeout_seconds=300, max_active=3):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 3600:
            raise ValueError('API_REQUEST_TIMEOUT_SECONDS должен быть больше 0 и не больше 3600.')
        self.timeout_seconds = timeout_seconds
        if type(max_active) is not int or not 1 <= max_active <= 32:
            raise ValueError('max_active должен быть целым числом от 1 до 32.')
        self.max_active = max_active
        self.active = {}
        self.finished = OrderedDict()
        self.lock = threading.Lock()
        self.closed = False

    def close(self):
        with self.lock:
            self.closed = True
            for event in self.active.values():
                event.set()
        # Handlers reap their processes and remove temporary directories.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with self.lock:
                if not self.active:
                    return
            time.sleep(0.01)

    def _purge(self):
        now = time.monotonic()
        for key, (_, stamp) in list(self.finished.items()):
            if now - stamp > 120:
                del self.finished[key]
        while len(self.finished) > 1000:
            self.finished.popitem(last=False)

    def cancel(self, request_id):
        if not isinstance(request_id, str) or not ID_PATTERN.fullmatch(request_id):
            raise ValueError('Некорректный request_id: 16–64 латинских букв, цифр или дефисов.')
        with self.lock:
            self._purge()
            if request_id in self.finished:
                return self.finished[request_id][0] == 'cancelled'
            event = self.active.get(request_id)
            if event is not None:
                event.set()
            else:
                # Cancellation may arrive just before the original POST registers.
                self.finished[request_id] = ('cancelled', time.monotonic())
                self._purge()
            return True

    def execute(self, request_id, envelope, commit=lambda result: result):
        if not isinstance(request_id, str) or not ID_PATTERN.fullmatch(request_id):
            raise ValueError('Некорректный X-Request-ID: 16–64 латинских букв, цифр или дефисов.')
        started = time.monotonic()
        with self.lock:
            self._purge()
            if self.closed:
                raise JobError(503, 'server_closing', 'Сервер останавливается.')
            if request_id in self.active or request_id in self.finished:
                cancelled = self.finished.get(request_id, (None,))[0] == 'cancelled'
                raise JobError(409, 'request_cancelled' if cancelled else 'duplicate_request_id',
                               'Запрос отменён.' if cancelled else 'Идентификатор запроса уже использован.')
            if len(self.active) >= self.max_active:
                raise JobError(429, 'request_capacity', 'Сервер занят. Повторите запрос позже.')
            event = threading.Event()
            self.active[request_id] = event
        state = 'finished'
        try:
            with tempfile.TemporaryDirectory(prefix='legal-api-job-') as directory:
                env = {**os.environ, 'TMPDIR': directory, 'TEMP': directory, 'TMP': directory}
                envelope = {**envelope, 'output_dir': directory}
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('api_worker.py'))],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, env=env,
                                           start_new_session=os.name == 'posix')
                try:
                    data = json.dumps(envelope, ensure_ascii=False).encode('utf-8')
                    while True:
                        if event.is_set():
                            state = 'cancelled'
                            raise JobError(409, 'request_cancelled', 'Запрос отменён.')
                        remaining = self.timeout_seconds - (time.monotonic() - started)
                        if remaining <= 0:
                            raise JobError(504, 'request_deadline', 'Истёк общий срок выполнения запроса.')
                        try:
                            stdout, _ = process.communicate(input=data, timeout=min(0.05, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            data = None
                    if process.returncode != 0:
                        raise JobError(500, 'worker_failed', 'Рабочий процесс завершился с ошибкой.')
                    try:
                        if len(stdout) > 16 * 1024 * 1024:
                            raise ValueError('reply too large')
                        reply = json.loads(stdout)
                        if (not isinstance(reply, dict) or type(reply.get('status')) is not int
                                or reply['status'] not in {200, 400, 500, 502, 503}
                                or (reply['status'] == 200 and not isinstance(reply.get('result'), dict))
                                or (reply['status'] != 200 and (not isinstance(reply.get('code'), str)
                                    or not isinstance(reply.get('error'), str)))):
                            raise ValueError('invalid reply')
                    except (ValueError, TypeError, RecursionError):
                        raise JobError(500, 'worker_protocol', 'Некорректный ответ рабочего процесса.') from None
                    # Completion and cancellation are serialized. No late session update.
                    with self.lock:
                        if event.is_set():
                            state = 'cancelled'
                            raise JobError(409, 'request_cancelled', 'Запрос отменён.')
                        if time.monotonic() - started >= self.timeout_seconds:
                            raise JobError(504, 'request_deadline', 'Истёк общий срок выполнения запроса.')
                        if reply['status'] != 200:
                            raise JobError(reply['status'], reply['code'], reply['error'])
                        result = commit(reply['result'])
                        self.active.pop(request_id, None)
                        self.finished[request_id] = ('finished', time.monotonic())
                        return result
                finally:
                    if os.name == 'posix':
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    elif process.poll() is None:
                        process.kill()
                    process.communicate()
        finally:
            with self.lock:
                self.active.pop(request_id, None)
                self.finished[request_id] = (state, time.monotonic())
                self._purge()
