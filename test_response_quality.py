"""Adversarial snapshots are not real model execution or lawyer acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from api import handle_ask
from response_quality import ROOT, evaluate_responses, read_dataset


class ResponseQualityTests(unittest.TestCase):
    def data(self):
        return read_dataset(ROOT / 'fixtures/response_quality_examples.json')

    def evaluate_case(self, case):
        return evaluate_responses({'schema_version': 1, 'cases': [case]})

    def test_examples_pass_technical_checks_without_network_or_legal_acceptance(self):
        with patch.object(socket, 'create_connection', side_effect=AssertionError('network')):
            report = evaluate_responses(self.data())
        self.assertEqual(report['technical_counts']['passed'], 2)
        self.assertTrue(report['dataset_synthetic'])
        self.assertFalse(report['model_execution_verified'])
        self.assertFalse(report['source_authenticity_verified'])
        self.assertFalse(report['legal_acceptance'])
        self.assertTrue(all(row['manual_legal_review_required'] for row in report['results']))

    def test_demo_cannot_count_as_completed_response_quality(self):
        case = self.data()['cases'][0]
        case['response']['demo'] = True
        report = self.evaluate_case(case)
        self.assertEqual(report['technical_status'], 'blocked')

    def test_absent_texts_and_missing_fingerprints_are_blocked(self):
        for change in ['absent', 'no_hash']:
            case = self.data()['cases'][0]
            if change == 'absent':
                del case['response']['source_materials']
            else:
                del case['response']['source_materials'][0]['sha256']
            with self.subTest(change=change):
                self.assertEqual(self.evaluate_case(case)['technical_status'], 'blocked')

    def test_changed_source_text_title_url_and_boolean_id_fail(self):
        for key, value in [('text', 'PRIVATE-CHANGED-TEXT'), ('title', 'PRIVATE-TITLE'),
                           ('url', 'https://example.org/PRIVATE-URL'), ('id', True)]:
            case = self.data()['cases'][0]
            case['response']['source_materials'][0][key] = value
            with self.subTest(key=key):
                report = self.evaluate_case(case)
                self.assertEqual(report['technical_status'], 'failed')
                self.assertNotIn('PRIVATE-', json.dumps(report))

    def test_unknown_citation_and_wrong_article_identity_fail(self):
        for sentence in ['Условный вывод [999].', 'Статья 999 ГК РФ устанавливает условное правило [1].']:
            case = self.data()['cases'][0]
            case['response']['answer'] += '\n\n'+sentence
            self.assertEqual(self.evaluate_case(case)['technical_status'], 'failed')

    def test_wrong_situation_date_and_benchmark_override_fail(self):
        case = self.data()['cases'][0]
        case['response']['as_of'] = '2025-01-01'
        self.assertEqual(self.evaluate_case(case)['technical_status'], 'failed')
        case['as_of'] = '2025-01-01'
        self.assertEqual(self.evaluate_case(case)['technical_status'], 'failed')

    def test_nested_model_trust_promotions_fail(self):
        for key in ['verified', 'lawyer_reviewed', 'period_supported', 'legal_acceptance']:
            case = self.data()['cases'][0]
            case['response']['extra'] = {key: True}
            with self.subTest(key=key):
                self.assertEqual(self.evaluate_case(case)['technical_status'], 'failed')

    def test_fabricated_contract_quote_fails_without_echoing_document(self):
        case = self.data()['cases'][1]
        case['response']['risks'][0]['quote'] = 'PRIVATE-INVENTED-CLAUSE'
        report = self.evaluate_case(case)
        self.assertEqual(report['technical_status'], 'failed')
        self.assertNotIn('PRIVATE-', json.dumps(report))
        self.assertNotIn(case['document'], json.dumps(report, ensure_ascii=False))

    def test_original_document_and_completed_review_are_required(self):
        for change in ['document', 'status']:
            case = self.data()['cases'][1]
            if change == 'document':
                del case['document']
            else:
                case['response']['status'] = 'needs_facts'
            self.assertEqual(self.evaluate_case(case)['technical_status'], 'blocked')

    def test_legal_review_risk_without_basis_is_blocked(self):
        case = self.data()['cases'][1]
        case['kind'] = 'legal_review'
        case['response']['sources'] = []
        self.assertEqual(self.evaluate_case(case)['technical_status'], 'blocked')

    def test_plain_review_cannot_hide_legal_bases_from_source_checks(self):
        case = self.data()['cases'][1]
        case['response']['risks'][0]['legal_basis'] = [{'source_id': 999, 'quote': 'PRIVATE-UNVERIFIED-BASIS'}]
        report = self.evaluate_case(case)
        self.assertEqual(report['technical_status'], 'blocked')
        self.assertNotIn('PRIVATE-', json.dumps(report))

    def test_invalid_bounded_dataset_inputs_rejected(self):
        for value in [{}, {'schema_version': 1, 'cases': []},
                      {'schema_version': 1, 'cases': self.data()['cases'] * 101},
                      {'schema_version': 1, 'cases': [self.data()['cases'][0]] * 2}]:
            with self.assertRaises(ValueError):
                evaluate_responses(value)
        case = self.data()['cases'][0]
        case['benchmark_id'] = 'not-a-benchmark'
        with self.assertRaises(ValueError):
            self.evaluate_case(case)

    def test_overdeep_response_is_failed_not_crashed(self):
        case = self.data()['cases'][0]
        nested = case['response']
        for _ in range(30):
            nested['extra'] = {}
            nested = nested['extra']
        self.assertEqual(self.evaluate_case(case)['technical_status'], 'failed')

    def test_ask_source_export_is_opt_in_and_contains_only_material_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            default = handle_ask({'question': 'Технический пример', 'demo': True}, Path(directory), persist_results=False)
            exported = handle_ask({'question': 'Технический пример', 'demo': True,
                                  'include_source_materials': True}, Path(directory), persist_results=False)
        self.assertNotIn('source_materials', default)
        self.assertEqual(len(exported['sources']), len(exported['source_materials']))
        for index, snapshot in enumerate(exported['source_materials'], 1):
            self.assertEqual(snapshot['id'], index)
            self.assertEqual(hashlib.sha256(snapshot['text'].encode()).hexdigest(), snapshot['sha256'])
            self.assertNotIn('query', snapshot)
            self.assertFalse(snapshot['period_supported'])
        report = self.evaluate_case({'id': 'exported-demo', 'kind': 'ask', 'response': exported})
        self.assertEqual(report['technical_status'], 'blocked')

    def test_invalid_export_flag_rejected_before_running_model(self):
        with tempfile.TemporaryDirectory() as directory, patch('api.run') as run:
            with self.assertRaises(ValueError):
                handle_ask({'question': 'Тест', 'include_source_materials': 'true'}, Path(directory))
            run.assert_not_called()

    def test_declared_human_approval_still_cannot_auto_accept_law(self):
        data = self.data()
        benchmark = {'cases': [{'id': 'LAW-02-B01', 'as_of': '2026-11-08',
                     'approved_legal_answer': 'PRIVATE-GOLDEN', 'review': {'approved': True}}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'benchmarks.json'
            path.write_text(json.dumps(benchmark))
            report = evaluate_responses(data, path)
        self.assertFalse(report['legal_acceptance'])
        self.assertEqual(report['results'][0]['benchmark_status'], 'declared_reviewed')
        self.assertNotIn('PRIVATE-GOLDEN', json.dumps(report))

    def test_reader_rejects_oversized_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'too-large.json'
            path.write_text('x' * 100)
            with patch('response_quality.MAX_BYTES', 50), self.assertRaises(ValueError):
                read_dataset(path)

    def test_cli_distinguishes_failed_blocked_and_invalid_inputs(self):
        passed = self.data()
        failed = copy.deepcopy(passed)
        failed['cases'][0]['response']['verified'] = True
        blocked = copy.deepcopy(passed)
        blocked['cases'][0]['response']['demo'] = True
        for data, expected in [(passed, 0), (failed, 1), (blocked, 2), ({}, 2)]:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'answers.json'
                path.write_text(json.dumps(data))
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/evaluate_answers.py'), str(path)],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertFalse(json.loads(result.stdout)['legal_acceptance'])
