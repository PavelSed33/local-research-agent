"""Run actual workspace JavaScript with a small DOM harness; not a browser check."""
from pathlib import Path
import shutil
import subprocess
import unittest


class WorkspaceReportUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is unavailable')
    def test_fixed_control_creation_comparison_and_context_guards(self):
        result = subprocess.run(['node', 'scripts/test_control_comparison.cjs'], cwd=Path(__file__).parent,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"checks":8', result.stdout)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is unavailable')
    def test_quality_scoring_and_metrics_context_guards(self):
        result = subprocess.run(['node', 'scripts/test_quality_scoring.cjs'], cwd=Path(__file__).parent,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"checks":6', result.stdout)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is unavailable')
    def test_services_pilot_form_and_download_context(self):
        result = subprocess.run(['node', 'scripts/test_services_pilot.cjs'], cwd=Path(__file__).parent,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"checks":15', result.stdout)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is unavailable')
    def test_preview_download_and_privacy_resets(self):
        root = Path(__file__).parent
        result = subprocess.run(['node', 'scripts/test_workspace_report.cjs'], cwd=root,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"browser_acceptance":false', result.stdout)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is unavailable')
    def test_all_requests_reject_responses_from_previous_context(self):
        root = Path(__file__).parent
        result = subprocess.run(['node', 'scripts/test_workspace_context.cjs'], cwd=root,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"checks":9', result.stdout)
        self.assertIn('"browser_acceptance":false', result.stdout)
