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
                    matches = list(root.glob(name))
                    if not matches:
                        self.fail(f"Docker COPY source has no matches: {name}")
                    for source in matches:
                        if source.is_dir():
                            shutil.copytree(source, destination, dirs_exist_ok=True)
                        else:
                            destination.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source, destination / source.name)
            result = subprocess.run(
                ['python', '-I', '-c',
                 'import sys; sys.path.insert(0, sys.argv[1]); '
                 'import api, legal_history, pravo_portal, pravo_ips; '
                 'from case_report import CaseReport; '
                 'from docx_redline import redline_docx; '
                 'from workspace_backup import create_backup, restore_backup; '
                 'from pathlib import Path; '
                 'from workspace_store import WorkspaceStore; '
                 'store=WorkspaceStore(\"case-report.db\"); user=store.create_user(\"Packaging admin\",\"admin\")[\"user_id\"]; '
                 'matter=store.create_matter(user,\"Packaging case\")[\"matter_id\"]; '
                 'assert CaseReport(store).build(user,matter,\"А00-1/2025\",\"2025-01-01\")[\"report\"][\"status\"]==\"no_evidence\"; '
                 'snapshot=create_backup(Path(\"case-report.db\"),Path(\"snapshot.zip\")); '
                 'assert restore_backup(Path(\"snapshot.zip\"),Path(\"restored.db\"),snapshot[\"snapshot_sha256\"])[\"requires_new_admin_token\"]; '
                 'from local_extract import handle_local_extract; assert handle_local_extract({"document":"Проверка суммы: 1 000 руб. после приёмки."})["matches"]; '
                 'from legal_skills import skill_options; assert len(skill_options()["skills"])==6; '
                 'from statute_corpus import corpus_report; '
                 'statutes=corpus_report(); assert statutes["text_received_count"]==0; '
                 'assert not Path("fixtures").exists(), "Unreviewed fixtures must not ship"; '
                 'from pathlib import Path; '
                 'api.handle_ask({"question":"Тест", "demo":True}, Path("results")); '
                 'import legal_review; legal_review.handle_legal_review({"document":"Условный договор для проверки упаковки.","demo":True,"facts":{"both_businesses":True}}); '
                 'assert Path("web/index.html").exists()',
                 directory], cwd=directory, capture_output=True, text=True,
                timeout=20, env={**os.environ, 'PYTHONPATH': ''})
            self.assertEqual(result.returncode, 0, result.stderr)

