import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import Source
from inventory_report import build_inventory_report
from publication_audit import archive_act


class InventoryReportTests(unittest.TestCase):
    def inventory(self):
        return {'consolidated_citation_inventory': [
            {'signed_date': '2022-07-14', 'act_number': '266-ФЗ'},
            {'signed_date': '2009-11-25', 'act_number': '266-ФЗ'},
        ]}

    def test_same_number_in_another_year_is_a_separate_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Source('Акт', 'https://publication.pravo.gov.ru/api/Document?eoNumber=123', 'excerpt', 'q')
            with patch('publication_audit.PravoPortalSearch.search_act', return_value=[source]), patch(
                'publication_audit.PravoPortalSearch._get', return_value={'Text': 'Текст акта'}
            ):
                archive_act('2022-07-14', '266-ФЗ', Path(directory))
            report = build_inventory_report(self.inventory(), Path(directory))
        self.assertEqual(report['summary']['candidate_text_unverified'], 1)
        self.assertEqual(report['summary']['missing_archive'], 1)
        self.assertFalse(report['coverage_verified'])

    def test_missing_archives_are_counted_without_trusting_fixture_flags(self):
        inventory = self.inventory()
        inventory['consolidated_citation_inventory'][0]['official_text_retrieved'] = True
        with tempfile.TemporaryDirectory() as directory:
            report = build_inventory_report(inventory, Path(directory))
        self.assertEqual(report['summary']['missing_archive'], 2)

    def test_failed_and_malformed_archives_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a').mkdir()
            (root / 'a' / 'manifest.json').write_text(json.dumps({
                'signed_date': '2022-07-14', 'act_number': '266-ФЗ',
                'status': 'retrieval_failed', 'publications': [],
            }))
            (root / 'b').mkdir()
            (root / 'b' / 'manifest.json').write_text('broken')
            report = build_inventory_report(self.inventory(), root)
        self.assertEqual(report['summary']['invalid_or_failed_archive'], 1)
        self.assertEqual(len(report['archive_errors']), 1)

    def test_duplicate_identity_is_rejected(self):
        inventory = self.inventory()
        inventory['consolidated_citation_inventory'] *= 2
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            build_inventory_report(inventory, Path(directory))
