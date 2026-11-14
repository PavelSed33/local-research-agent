"""Structural diagnostics must not turn quarantined legal sources into accepted law."""
import unittest
from unittest.mock import patch

from scripts.validate_legal_data import validate


class ValidateLegalDataModesTests(unittest.TestCase):
    def test_structure_only_reports_gaps_without_legal_acceptance(self):
        report = validate(require_corpus=False)
        self.assertEqual(report['validation_mode'], 'structure_only')
        self.assertFalse(report['legal_acceptance'])
        self.assertEqual(report['source_coverage_complete'],
                         report['missing_statutory_extracts'] == 0)
        self.assertEqual(report['unexpected_statutory_extracts'], 0)

    def test_strict_mode_never_accepts_missing_extracts(self):
        report = validate(require_corpus=False)
        if report['missing_statutory_extracts']:
            with self.assertRaisesRegex(ValueError, 'Statutory corpus coverage incomplete'):
                validate()
        else:
            strict = validate()
            self.assertTrue(strict['source_coverage_complete'])
            self.assertFalse(strict['legal_acceptance'])

    def test_unexpected_card_ids_fail_in_both_modes(self):
        from scripts import validate_legal_data as module
        genuine_report = module.corpus_report()
        counterfeit = {**genuine_report,
                       'entries': [*genuine_report['entries'], {'card_id': 'LAW-INVALID:999'}]}
        with patch.object(module, 'corpus_report', return_value=counterfeit):
            for require_corpus in (True, False):
                with self.subTest(require_corpus=require_corpus):
                    with self.assertRaisesRegex(ValueError, 'unexpected card IDs'):
                        validate(require_corpus=require_corpus)


if __name__ == '__main__':
    unittest.main()
