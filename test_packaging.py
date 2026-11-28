"""Run the packaged modules without imports or data from the checkout."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


class DockerPackagingTests(unittest.TestCase):
    def test_shipped_files_support_demo_and_real_mode_imports(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'app'
            for line in (root / 'Dockerfile').read_text().splitlines():
                if not line.startswith('COPY '):
                    continue
                _, *patterns, destination = line.split()
                target = image / destination
                target.mkdir(parents=True, exist_ok=True)
                for pattern in patterns:
                    sources = list(root.glob(pattern))
                    self.assertTrue(sources, f'Missing Docker source: {pattern}')
                    for source in sources:
                        if source.is_dir():
                            shutil.copytree(source, target, dirs_exist_ok=True)
                        else:
                            shutil.copy(source, target)

            # Run from a separate data directory so relative data lookups surface.
            check = textwrap.dedent("""
                from pathlib import Path
                import sys
                image = Path(sys.argv[1])
                sys.path.insert(0, str(image))

                import api
                import legal_history
                import pravo_portal
                import pravo_ips
                from api_worker import execute
                from case_report import CaseReport
                from docx_redline import redline_docx
                from legal_evidence import evidence_report
                from legal_review import handle_legal_review
                from legal_skills import skill_options
                from local_extract import handle_local_extract
                from statute_corpus import corpus_report
                from workspace_backup import create_backup, restore_backup
                from workspace_store import WorkspaceStore

                for filename in ('index.html', 'workspace.html', 'app.js', 'workspace.js', 'style.css'):
                    assert (image / 'web' / filename).is_file()
                store = WorkspaceStore('case-report.db')
                user = store.create_user('Packaging admin', 'admin')['user_id']
                matter = store.create_matter(user, 'Packaging case')['matter_id']
                report = CaseReport(store).build(user, matter, 'А00-1/2025', '2025-01-01')
                assert report['report']['status'] == 'no_evidence'
                snapshot = create_backup(Path('case-report.db'), Path('snapshot.zip'))
                restored = restore_backup(Path('snapshot.zip'), Path('restored.db'), snapshot['snapshot_sha256'])
                assert restored['requires_new_admin_token']
                assert handle_local_extract({'document': 'Проверка суммы: 1 000 руб. после приёмки.'})['matches']
                assert len(skill_options()['skills']) == 6
                evidence = evidence_report()
                assert len(evidence['entries']) == 5 and not evidence['errors']
                statutes = corpus_report()
                assert not statutes['errors'], statutes['errors']
                assert statutes['refresh_gaps'] and statutes['text_received_count'] == 0
                assert api.handle_ask({'question': 'Тест', 'demo': True}, Path('results'))['answer']
                review = handle_legal_review({'document': 'Условный договор для проверки упаковки.',
                                              'demo': True, 'facts': {'both_businesses': True}})
                assert review['verified'] is False
                assert execute({'path': '/duty', 'payload': {'mode': 'claim', 'court': 'general',
                                                              'amount': 750000}})['status'] == 200
            """)
            result = subprocess.run([sys.executable, '-I', '-c', check, str(image)],
                                    cwd=directory, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
