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
                    self.assertEqual(len(smoke(base)), 26)
                for route in ('/review/legal', '/case-law/check', '/legal/coverage', '/privacy/check', '/legal/skills/run', '/extract/local'):
                    request = urllib.request.Request(base + route, data=b'{}', headers={'Content-Type': 'application/json'})
                    with self.assertRaises(urllib.error.HTTPError) as denied:
                        urllib.request.urlopen(request)
                    self.assertEqual(denied.exception.code, 401)
                with urllib.request.urlopen(base) as response:
                    self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

