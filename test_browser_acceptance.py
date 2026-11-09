"""Unavailable browser prerequisites cannot become a passing visual acceptance."""
import subprocess
import unittest
from unittest.mock import patch

from scripts.browser_acceptance import run_browser_acceptance


class BrowserAcceptanceTests(unittest.TestCase):
    def test_missing_node_is_blocked_without_starting_api(self):
        with patch('scripts.browser_acceptance.shutil.which', return_value=None), patch('scripts.browser_acceptance.create_server') as server:
            report = run_browser_acceptance()
            server.assert_not_called()
        self.assertEqual(report['status'], 'blocked')
        self.assertFalse(report['browser_acceptance'])

    def test_absent_browser_and_dependency_are_distinct_blockers(self):
        for body, reason in [('{"dependency":true,"browser":false}', 'chromium_missing'),
                             ('{"dependency":false,"browser":false}', 'playwright_missing')]:
            with self.subTest(reason=reason), patch('scripts.browser_acceptance.shutil.which', return_value='node'), patch('scripts.browser_acceptance.subprocess.run', return_value=subprocess.CompletedProcess([], 0, body, '')), patch('scripts.browser_acceptance.create_server') as server:
                report = run_browser_acceptance()
                server.assert_not_called()
            self.assertEqual(report['reason'], reason)
            self.assertFalse(report['browser_acceptance'])

    def test_probe_timeout_is_blocked(self):
        with patch('scripts.browser_acceptance.shutil.which', return_value='node'), patch('scripts.browser_acceptance.subprocess.run', side_effect=subprocess.TimeoutExpired('probe', 15)):
            report = run_browser_acceptance()
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['reason'], 'browser_probe_failed')
