import tempfile
from pathlib import Path
import unittest

from api import handle_ask
from legal_history import select_provision_period


class PeriodSelectionTests(unittest.TestCase):
    def timeline(self):
        return {'provision_id': 'Условный закон:ст.1:ч.2', 'verified': True,
                'periods': [
                    {'valid_from': '2020-01-01', 'valid_to': '2023-02-28'},
                    {'valid_from': '2023-03-01', 'valid_to': None},
                ]}

    def test_day_before_on_and_after_change(self):
        for day, expected in [('2023-02-28', '2020-01-01'),
                              ('2023-03-01', '2023-03-01'),
                              ('2023-03-02', '2023-03-01')]:
            with self.subTest(day=day):
                result = select_provision_period(self.timeline(), day)
                self.assertEqual(result['period']['valid_from'], expected)
                self.assertEqual(result['status'], 'candidate_period_unverified')
                self.assertFalse(result['verified'])

    def test_date_before_known_history_is_not_extrapolated(self):
        result = select_provision_period(self.timeline(), '2019-12-31')
        self.assertEqual(result['status'], 'date_outside_known_periods')
        self.assertIsNone(result['period'])

    def test_gap_is_not_filled(self):
        timeline = self.timeline()
        timeline['periods'][0]['valid_to'] = '2023-01-31'
        self.assertIsNone(select_provision_period(timeline, '2023-02-01')['period'])

    def test_overlap_is_rejected(self):
        timeline = self.timeline()
        timeline['periods'][0]['valid_to'] = '2023-03-01'
        self.assertEqual(select_provision_period(timeline, '2023-03-01')['status'], 'conflicting_period_evidence')

    def test_invalid_date_or_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            select_provision_period(self.timeline(), '2023-02-29')
        timeline = self.timeline()
        timeline['periods'][0]['valid_to'] = '2019-01-01'
        self.assertEqual(select_provision_period(timeline, '2023-03-01')['status'], 'invalid_period_evidence')

    def test_missing_periods_stay_unknown(self):
        self.assertEqual(select_provision_period({}, '2023-03-01')['status'], 'no_period_evidence')

    def test_api_returns_selection_for_requested_date(self):
        with tempfile.TemporaryDirectory() as directory:
            result = handle_ask({'question': 'Тест', 'demo': True, 'as_of': '2023-03-01'}, Path(directory))
        self.assertEqual(result['provision_period']['as_of'], '2023-03-01')
        self.assertFalse(result['provision_period']['verified'])
