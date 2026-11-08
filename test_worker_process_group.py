"""PDF/OCR descendants must die with a cancelled/deadline-limited worker on POSIX."""
import os
import socket
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

from request_jobs import JobError, RequestJobs


@unittest.skipUnless(os.name == 'posix', 'POSIX process groups required')
class WorkerProcessGroupTests(unittest.TestCase):
    def test_deadline_stops_worker_and_its_converter_child(self):
        spawn = subprocess.Popen
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen(); listener.settimeout(3)
            code = ('import socket,time; '
                    f's=socket.create_connection(("127.0.0.1",{listener.getsockname()[1]}));time.sleep(30)')
            def converter_worker(command, **kwargs):
                return spawn([sys.executable, '-c', 'import subprocess,sys,time; '
                              'subprocess.Popen([sys.executable,"-c",sys.argv[1]]);time.sleep(30)', code], **kwargs)
            jobs = RequestJobs(timeout_seconds=.8)
            with patch('request_jobs.subprocess.Popen', side_effect=converter_worker):
                with self.assertRaises(JobError) as caught:
                    jobs.execute(uuid.uuid4().hex, {'path': '/documents/parse', 'payload': {}})
            self.assertEqual(caught.exception.status, 504)
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(2)
                self.assertEqual(connection.recv(1), b'')
            self.assertFalse(jobs.active)
