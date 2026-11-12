import tempfile
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

from api import create_server
from scripts.smoke import smoke


class WorkbenchAcceptanceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node not installed; browser acceptance is separate')
    def test_document_input_encoding_and_binary_transport_without_browser(self):
        root = Path(__file__).resolve().parent
        result = subprocess.run(['node', str(root / 'scripts/test_document_input.cjs')],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_demo_workflows_and_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server('127.0.0.1', 0, directory, 'acceptance-token')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f'http://127.0.0.1:{server.server_address[1]}'
                with patch.dict('os.environ', {'API_TOKEN': 'acceptance-token'}):
                    passed = smoke(base)
                self.assertEqual(len(passed), 6)
                self.assertIn('POST /ask session follow-up', passed)
                for route in ('/ask', '/review', '/extract', '/diff'):
                    with self.subTest(route=route):
                        request = urllib.request.Request(
                            base + route, data=b'{}',
                            headers={'Content-Type': 'application/json'},
                        )
                        with self.assertRaises(urllib.error.HTTPError) as denied:
                            urllib.request.urlopen(request, timeout=10)
                        self.assertEqual(denied.exception.code, 401)
                with urllib.request.urlopen(base + '/health', timeout=10) as response:
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
