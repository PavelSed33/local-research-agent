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


SOURCE = Source("Правовой источник", "https://pravo.gov.ru/example", "Текст условной нормы.", "запрос")
VALID_REPORT = """## Краткий ответ

Ответ на основе источника [1].

## Правовое основание

Условная норма [1].

## Актуальность и изменения

Изменения проверены по источнику [1].

## Как применить к ситуации

Следует сверить обстоятельства с текстом нормы.

## Ограничения

Это информационная справка.
"""


class JsonParsingTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(extract_json('{"queries": ["норма"]}'), {"queries": ["норма"]})

    def test_markdown_json(self):
        self.assertEqual(extract_json('```json\n{"queries": ["норма"]}\n```'), {"queries": ["норма"]})

    def test_invalid_json(self):
        with self.assertRaises(AgentError):
            extract_json("не JSON")


class SourceTests(unittest.TestCase):
    def test_deduplicates_and_limits(self):
        sources = [
            Source("A", "https://pravo.gov.ru/a", "1", "q"),
            Source("A copy", "https://pravo.gov.ru/a/", "2", "q"),
            Source("B", "https://pravo.gov.ru/b", "3", "q"),
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
        report = VALID_REPORT.replace("## Ограничения", "### Ограничения")
        self.assertTrue(any("Ограничения" in issue for issue in validate_report(report, [SOURCE])))


class RepairingModel:
    def complete(self, system, user):
        if "Проблемы:" in user:
            return VALID_REPORT
        return VALID_REPORT.replace("[1]", "[9]")


class ReportRepairTests(unittest.TestCase):
    def test_repairs_report_once(self):
        report, repaired = build_validated_report(RepairingModel(), "Вопрос", [SOURCE])
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
            report_path, run_path = run("Тестовый правовой вопрос", 3, 6, Path(directory), demo=True)
            report = report_path.read_text(encoding="utf-8")
            journal = json.loads(run_path.read_text(encoding="utf-8"))
            self.assertIn("# Тестовый правовой вопрос", report)
            self.assertIn("Дата проверки изменений", report)
            self.assertIn("не индивидуальная юридическая консультация", report)
            self.assertTrue(journal["demo"])
            self.assertTrue(journal["report_validated"])
            self.assertTrue(journal["legal_update_check"]["performed"])
            self.assertGreaterEqual(len(journal["legal_update_check"]["queries"]), 1)
            self.assertEqual(len(journal["sources"]), 2)


class IterativeModel:
    def __init__(self):
        self.assessments = 0

    def complete(self, system, user):
        if "legal_update_queries" in system:
            return json.dumps({"legal_update_queries": ["проверка поправок"], "notes": "Обязательная проверка."})
        if "additional_queries" in system:
            self.assessments += 1
            if self.assessments == 1:
                return json.dumps(
                    {"sufficient": False, "additional_queries": ["второй"], "reason": "Нужна ещё одна норма."}
                )
            return json.dumps({"sufficient": True, "additional_queries": [], "reason": "Норм достаточно."})
        if "queries" in system:
            return json.dumps({"queries": ["первый"]})
        return VALID_REPORT


class IterativeSearch:
    def __init__(self):
        self.calls = []

    def search(self, query, limit):
        self.calls.append(query)
        return [Source(f"Источник: {query}", f"https://pravo.gov.ru/{query}", "Материал.", query)]


class IterativeResearchTests(unittest.TestCase):
    def test_additional_research_and_mandatory_update_check(self):
        model = IterativeModel()
        search = IterativeSearch()
        with patch("agent.OpenAICompatibleModel", return_value=model), patch("agent.TavilySearch", return_value=search):
            with tempfile.TemporaryDirectory() as directory:
                _, run_path = run("Вопрос", 1, 6, Path(directory))
                journal = json.loads(run_path.read_text(encoding="utf-8"))
        self.assertEqual(search.calls[:3], ["Вопрос", "второй", "проверка поправок"])
        self.assertEqual(len(search.calls), 4)
        self.assertEqual(len(journal["research_rounds"]), 2)
        self.assertTrue(journal["legal_update_check"]["performed"])
        self.assertEqual(journal["legal_update_check"]["queries"][0], "проверка поправок")


if __name__ == "__main__":
    unittest.main()
