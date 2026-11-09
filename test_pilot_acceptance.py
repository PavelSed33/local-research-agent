"""Pilot gates must not confuse synthetic success with live or legal acceptance."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from scripts.pilot_acceptance import CHECKS, LOCAL_CHECKS, run_check, run_pilot, summarize


def passed_checks():
    rows = [{'id': name, 'status': 'passed'} for name in CHECKS]
    rows[-2]['browser_acceptance'] = True
    rows[-1].update(live_acceptance_passed=True, external_requests_attempted=True)
    return rows


class PilotAcceptanceTests(unittest.TestCase):
    def test_even_all_technical_checks_cannot_approve_legal_or_production_readiness(self):
        report = summarize(passed_checks(), {})
        self.assertTrue(report['automated_checks_passed'])
        self.assertFalse(report['production_ready'])
        self.assertFalse(report['legal_acceptance'])
        self.assertEqual(report['status'], 'blocked')
        self.assertTrue(all(row['status'] == 'unverified' for row in report['manual_gates']))

    def test_missing_and_duplicated_check_sets_fail_closed(self):
        for rows in (passed_checks()[:-1], passed_checks() + [passed_checks()[0]], []):
            with self.subTest(rows=rows):
                result = summarize(rows, {})
                self.assertEqual(result['status'], 'failed')
                self.assertFalse(result['automated_checks_passed'])

    def test_pass_labels_without_explicit_live_and_browser_acceptance_are_insufficient(self):
        rows = [{'id': name, 'status': 'passed'} for name in CHECKS]
        result = summarize(rows, {})
        self.assertTrue(result['local_checks_passed'])
        self.assertFalse(result['browser_acceptance'])
        self.assertFalse(result['live_acceptance_passed'])
        self.assertFalse(result['automated_checks_passed'])

    def test_failed_local_check_overrides_other_successes(self):
        rows = passed_checks(); rows[0]['status'] = 'failed'
        result = summarize(rows, {})
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['local_checks_passed'])

    def test_default_never_dispatches_live_even_with_valid_credentials(self):
        values = {'LLM_API_KEY': 'PRIVATE-KEY', 'LLM_MODEL': 'PRIVATE-MODEL',
                  'TAVILY_API_KEY': 'PRIVATE-SEARCH', 'ALLOW_CLOUD_DOCUMENTS': 'true'}
        with patch.dict(os.environ, values, clear=True), patch('scripts.pilot_acceptance.run_check',
                side_effect=lambda name, **kw: {'id': name, 'status': 'passed'}) as check:
            result = run_pilot()
        self.assertEqual([call.args[0] for call in check.call_args_list], list(LOCAL_CHECKS) + ['browser'])
        self.assertEqual(result['checks'][-1]['reason'], 'live_not_requested')
        self.assertFalse(result['external_requests_attempted'])
        self.assertNotIn('PRIVATE-', json.dumps(result))

    def test_live_requires_local_success_and_configured_credentials(self):
        for local_failed in (True, False):
            with self.subTest(local_failed=local_failed), patch.dict(os.environ, {}, clear=True), \
                    patch('scripts.pilot_acceptance.run_check', side_effect=lambda name, **kw:
                          {'id': name, 'status': 'failed' if local_failed and name == 'data' else 'passed'}) as check:
                result = run_pilot(live=True)
                self.assertNotIn('live', [call.args[0] for call in check.call_args_list])
                self.assertEqual(result['checks'][-1]['reason'],
                                 'local_checks_incomplete' if local_failed else 'missing_or_invalid_configuration')

    def test_unknown_live_outcome_never_claims_no_external_requests(self):
        rows = passed_checks(); rows[-1] = {'id': 'live', 'status': 'failed', 'reason': 'deadline_exceeded'}
        result = summarize(rows, {})
        self.assertIsNone(result['external_requests_attempted'])
        self.assertFalse(result['live_acceptance_passed'])

    def test_worker_timeout_is_failure_without_raw_exception(self):
        with patch('scripts.pilot_acceptance._run_isolated', side_effect=subprocess.TimeoutExpired('PRIVATE-COMMAND', 1)):
            result = run_check('data')
        self.assertEqual(result['reason'], 'deadline_exceeded')
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_missing_invalid_or_mismatched_worker_reports_fail(self):
        for body in (None, 'not-json', '[]', '{"id":"live","status":"passed"}', '{"id":"data","status":"running"}', '{"id":"data","status":"passed"}'):
            def worker(command, environment, timeout):
                if body is not None:
                    Path(command[-1]).write_text(body)
                return subprocess.CompletedProcess(command, 0)
            with self.subTest(body=body), patch('scripts.pilot_acceptance._run_isolated', side_effect=worker):
                result = run_check('data')
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['reason'], 'invalid_worker_report')

    def test_crashed_worker_cannot_return_a_passing_report(self):
        def worker(command, environment, timeout):
            Path(command[-1]).write_text('{"id":"data","status":"passed"}')
            return subprocess.CompletedProcess(command, 1)
        with patch('scripts.pilot_acceptance._run_isolated', side_effect=worker):
            self.assertEqual(run_check('data')['reason'], 'worker_failed')

    def test_real_data_worker_uses_temporary_output_and_returns_counts_only(self):
        result = run_check('data')
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['statutory_extracts'], 454)
        self.assertEqual(result['draft_synthetic_cases'], 90)
        self.assertEqual(result['services_pilot_tasks'], 25)
        self.assertEqual(set(result), {'id', 'status', 'statutory_extracts', 'draft_synthetic_cases', 'services_pilot_tasks'})

    def test_unknown_worker_is_rejected_before_process_start(self):
        with patch('scripts.pilot_acceptance._run_isolated') as worker, self.assertRaises(ValueError):
            run_check('arbitrary-command')
        worker.assert_not_called()
