import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import (
    AgentError,
    OpenAICompatibleModel,
    Source,
    build_validated_report,
    deduplicate,
    extract_json,
    run,
    validate_report,
)


SOURCE = Source("Источник", "https://example.org/source", "Подтверждённый факт.", "запрос")
VALID_REPORT = """## Краткий вывод

Подтверждённый вывод [1].

## Основные факты

- Подтверждённый факт [1].

## Подробный анализ

Подтверждённый анализ [1].

## Противоречия и ограничения

Противоречий не обнаружено.

## Выводы

Итоговый вывод [1].
"""


class JsonParsingTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(extract_json('{"queries": ["тема"]}'), {"queries": ["тема"]})

    def test_markdown_json(self):
        self.assertEqual(extract_json('```json\n{"queries": ["тема"]}\n```'), {"queries": ["тема"]})

    def test_invalid_json(self):
        with self.assertRaises(AgentError):
            extract_json("не JSON")


class SourceTests(unittest.TestCase):
    def test_deduplicates_and_limits(self):
        sources = [
            Source("A", "https://example.org/a", "1", "q"),
            Source("A copy", "https://example.org/a/", "2", "q"),
            Source("B", "https://example.org/b", "3", "q"),
        ]
        result = deduplicate(sources, 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, "A")


class ReportValidationTests(unittest.TestCase):
    def test_valid_report(self):
        self.assertEqual(validate_report(VALID_REPORT, [SOURCE]), [])

    def test_rejects_unknown_citation(self):
        report = VALID_REPORT.replace("[1]", "[2]")
        self.assertTrue(any("несуществующие" in issue for issue in validate_report(report, [SOURCE])))

    def test_rejects_unknown_external_link(self):
        report = VALID_REPORT + "\n[Неизвестный сайт](https://example.org/unknown)\n"
        self.assertTrue(any("которых нет" in issue for issue in validate_report(report, [SOURCE])))

    def test_rejects_missing_section(self):
        report = VALID_REPORT.replace("## Выводы", "### Выводы")
        self.assertTrue(any("Выводы" in issue for issue in validate_report(report, [SOURCE])))


class RepairingModel:
    def complete(self, system, user):
        if "Проблемы:" in user:
            return VALID_REPORT
        return VALID_REPORT.replace("[1]", "[9]")


class ReportRepairTests(unittest.TestCase):
    def test_repairs_report_once(self):
        report, repaired = build_validated_report(RepairingModel(), "Тема", [SOURCE])
        self.assertTrue(repaired)
        self.assertEqual(validate_report(report, [SOURCE]), [])


class ConfigurationTests(unittest.TestCase):
    def test_model_requires_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AgentError):
                OpenAICompatibleModel()


class DemoRunTests(unittest.TestCase):
    def test_complete_demo_run(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path, run_path = run("Тестовая тема", 3, 6, Path(directory), demo=True)
            report = report_path.read_text(encoding="utf-8")
            journal = json.loads(run_path.read_text(encoding="utf-8"))
            self.assertIn("# Тестовая тема", report)
            self.assertIn("Демонстрационный режим", report)
            self.assertIn("## Источники", report)
            self.assertTrue(journal["demo"])
            self.assertTrue(journal["report_validated"])
            self.assertEqual(journal["queries"][0], "Тестовая тема")
            self.assertEqual(len(journal["queries"]), 3)
            self.assertEqual(len(journal["sources"]), 2)
            self.assertTrue(journal["research_rounds"][0]["sufficient"])


class IterativeModel:
    def __init__(self):
        self.assessments = 0

    def complete(self, system, user):
        if "additional_queries" in system:
            self.assessments += 1
            if self.assessments == 1:
                return json.dumps(
                    {"sufficient": False, "additional_queries": ["второй"], "reason": "Нужны дополнительные данные."}
                )
            return json.dumps({"sufficient": True, "additional_queries": [], "reason": "Данных достаточно."})
        if "queries" in system:
            return json.dumps({"queries": ["первый"]})
        return VALID_REPORT


class IterativeSearch:
    def __init__(self):
        self.calls = []

    def search(self, query, limit):
        self.calls.append(query)
        return [Source(f"Источник: {query}", f"https://example.org/{query}", "Материал.", query)]


class IterativeResearchTests(unittest.TestCase):
    def test_runs_additional_search_when_needed(self):
        model = IterativeModel()
        search = IterativeSearch()
        with patch("agent.OpenAICompatibleModel", return_value=model), patch("agent.TavilySearch", return_value=search):
            with tempfile.TemporaryDirectory() as directory:
                _, run_path = run("Тема", 1, 6, Path(directory))
                journal = json.loads(run_path.read_text(encoding="utf-8"))
        self.assertEqual(search.calls, ["Тема", "второй"])
        self.assertEqual(len(journal["research_rounds"]), 2)
        self.assertEqual(len(journal["sources"]), 2)


if __name__ == "__main__":
    unittest.main()
