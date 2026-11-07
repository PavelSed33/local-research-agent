"""Exercise the Python file set shipped by the Docker image."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class DockerPackagingTests(unittest.TestCase):
    def test_shipped_files_support_demo_and_real_mode_imports(self):
        root = Path(__file__).parent
        copy_line = next(line for line in (root / 'Dockerfile').read_text().splitlines()
                         if line.startswith('COPY '))
        with tempfile.TemporaryDirectory() as directory:
            for name in copy_line.split()[1:-1]:
                shutil.copy(root / name, directory)
            result = subprocess.run(
                ['python', '-I', '-c',
                 'import sys; sys.path.insert(0, sys.argv[1]); '
                 'import api, legal_history, pravo_portal, pravo_ips; '
                 'from pathlib import Path; '
                 'api.handle_ask({"question":"Тест", "demo":True}, Path("results"))',
                 directory], cwd=directory, capture_output=True, text=True,
                timeout=20, env={**os.environ, 'PYTHONPATH': ''})
            self.assertEqual(result.returncode, 0, result.stderr)
