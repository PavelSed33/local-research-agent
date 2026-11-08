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
        copy_lines = [line for line in (root / 'Dockerfile').read_text().splitlines() if line.startswith('COPY ')]
        with tempfile.TemporaryDirectory() as directory:
            for line in copy_lines:
                fields = line.split()
                destination = Path(directory) / fields[-1]
                for name in fields[1:-1]:
                    source = root / name
                    if source.is_dir():
                        shutil.copytree(source, destination, dirs_exist_ok=True)
                    else:
                        destination.mkdir(parents=True, exist_ok=True)
                        shutil.copy(source, destination)
            result = subprocess.run(
                ['python', '-I', '-c',
                 'import sys; sys.path.insert(0, sys.argv[1]); '
                 'import api, legal_history, pravo_portal, pravo_ips; '
                 'from legal_skills import skill_options; assert len(skill_options()["skills"])==3; '
                 'from legal_evidence import evidence_report; '
                 'corpus=evidence_report(); assert len(corpus["entries"])==5 and not corpus["errors"]; '
                 'from statute_corpus import corpus_report; '
                 'statutes=corpus_report(); assert statutes["text_received_count"]==454 and not statutes["errors"]; '
                 'from pathlib import Path; '
                 'api.handle_ask({"question":"Тест", "demo":True}, Path("results")); '
                 'import legal_review; legal_review.handle_legal_review({"document":"Условный договор для проверки упаковки.","demo":True,"facts":{"both_businesses":True}}); '
                 'assert Path("web/index.html").exists()',
                 directory], cwd=directory, capture_output=True, text=True,
                timeout=20, env={**os.environ, 'PYTHONPATH': ''})
            self.assertEqual(result.returncode, 0, result.stderr)

