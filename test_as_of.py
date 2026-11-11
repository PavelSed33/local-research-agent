import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent
from api import handle_ask


class SituationDateTests(unittest.TestCase):
    def test_invalid_dates_are_rejected_before_research(self):
        for value in ('2023-02-29', '20240101', '2024-1-01', '', 2024, True):
            with self.subTest(value=value), patch('api.run') as run:
                with self.assertRaises(ValueError):
                    handle_ask({'question': 'Тест', 'as_of': value}, Path('.'))
                run.assert_not_called()

    def test_explicit_date_survives_api_journal_and_report(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            agent, 'current_check_date', return_value='2026-11-07'
        ):
            result = handle_ask({'question': 'Тест', 'demo': True, 'as_of': '2024-02-29'}, Path(directory))
            self.assertEqual(result['as_of'], '2024-02-29')
            self.assertEqual(result['checked_on'], '2026-11-07')
            self.assertIn('**Дата правовой ситуации:** 2024-02-29', result['answer'])
            journal = json.loads(Path(result['journal_file']).read_text())
            self.assertEqual(journal['as_of'], '2024-02-29')

    def test_default_is_date_of_check(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            agent, 'current_check_date', return_value='2026-11-07'
        ):
            result = handle_ask({'question': 'Тест', 'demo': True}, Path(directory))
            self.assertEqual(result['as_of'], result['checked_on'])

    def test_ips_and_report_receive_situation_date(self):
        model = agent.DemoModel()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            agent, 'OpenAICompatibleModel', return_value=model
        ), patch.object(agent, 'TavilySearch', return_value=agent.DemoSearch()), patch(
            'pravo_portal.PravoPortalSearch.search', return_value=[]
        ), patch('pravo_ips.PravoIpsBank.search', return_value=[]) as search, patch.object(
            agent, 'build_validated_report', wraps=agent.build_validated_report
        ) as report, patch.object(agent, 'plan_court_practice_check', wraps=agent.plan_court_practice_check) as court:
            agent.run('Тест', 1, 2, Path(directory), as_of='2023-03-01')
            self.assertEqual(court.call_args.args[2], '2023-03-01')
            self.assertTrue(search.called)
            self.assertTrue(all(c.kwargs['as_of'] == '2023-03-01' for c in search.call_args_list))
            self.assertIn('Дата правовой ситуации: 2023-03-01', report.call_args.args[1])
