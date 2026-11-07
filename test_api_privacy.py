import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from agent import AgentError, post_json
from api import create_server, handle_ask


class ApiPrivacyTests(unittest.TestCase):
    def test_default_http_mode_does_not_retain_research(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'persistent'
            server = create_server('127.0.0.1', 0, output)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(
                    f'http://127.0.0.1:{server.server_address[1]}/ask',
                    data=json.dumps({'question': 'PRIVATE-MARKER', 'demo': True}).encode(),
                    headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=5) as response:
                    result = json.load(response)
                self.assertFalse(result['results_persisted'])
                self.assertIsNone(result['report_file'])
                self.assertIsNone(result['journal_file'])
                self.assertFalse(output.exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_temporary_files_are_removed_on_failure(self):
        captured = []
        def fail(**kwargs):
            directory = kwargs['output_dir']
            directory.mkdir(parents=True)
            (directory / 'secret.txt').write_text('private')
            captured.append(directory)
            raise AgentError('failed')
        with patch('api.run', side_effect=fail), self.assertRaises(AgentError):
            handle_ask({'question': 'test'}, Path('.'), persist_results=False)
        self.assertFalse(captured[0].exists())

    def test_explicit_persistence_keeps_files_without_logging_question(self):
        log = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(log):
            result = handle_ask({'question': 'PRIVATE-MARKER', 'demo': True}, Path(directory), persist_results=True)
            self.assertTrue(Path(result['report_file']).exists())
            self.assertTrue(result['results_persisted'])
        self.assertNotIn('PRIVATE-MARKER', log.getvalue())

    def test_upstream_error_body_is_not_exposed(self):
        error = urllib.error.HTTPError('https://example.test', 400, 'bad', {}, io.BytesIO(b'PRIVATE-DOCUMENT'))
        with patch('agent.open_json_request', side_effect=error), self.assertRaises(AgentError) as caught:
            post_json('https://example.test', {}, {})
        self.assertIn('400', str(caught.exception))
        self.assertNotIn('PRIVATE-DOCUMENT', str(caught.exception))
