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
    reflect_on_report,
    run,
    validate_report,
    validate_service_url,
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


class RecordingModel:
    def __init__(self):
        self.users = []

    def complete(self, system, user):
        self.users.append(user)
        return VALID_REPORT


class SessionContextTests(unittest.TestCase):
    def test_session_context_reaches_report_prompt(self):
        model = RecordingModel()
        build_validated_report(model, "Вопрос", [SOURCE], session_context="Ранее обсуждали аренду.")
        self.assertTrue(any("Ранее обсуждали аренду." in user for user in model.users))

    def test_without_session_context_prompt_unchanged(self):
        model = RecordingModel()
        build_validated_report(model, "Вопрос", [SOURCE])
        self.assertTrue(all("Контекст предыдущих вопросов" not in user for user in model.users))


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
            self.assertTrue(journal["court_practice_check"]["performed"])
            self.assertTrue(journal["self_reflection"]["performed"])
            self.assertTrue(journal["self_reflection"]["consistent"])
            self.assertEqual(len(journal["sources"]), 2)


class IterativeModel:
    def __init__(self):
        self.assessments = 0

    def complete(self, system, user):
        if "legal_update_queries" in system:
            return json.dumps({"legal_update_queries": ["проверка поправок"], "notes": "Обязательная проверка."})
        if "court_queries" in system:
            return json.dumps({"court_queries": ["судебная практика по теме"], "notes": "Поиск практики."})
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
        with patch("agent.OpenAICompatibleModel", return_value=model), patch("agent.TavilySearch", return_value=search), patch("pravo_portal.PravoPortalSearch", return_value=search), patch("pravo_ips.PravoIpsBank") as ips_mock:
            with tempfile.TemporaryDirectory() as directory:
                _, run_path = run("Вопрос", 1, 6, Path(directory))
                journal = json.loads(run_path.read_text(encoding="utf-8"))
        self.assertEqual(search.calls[:3], ["Вопрос", "второй", "проверка поправок"])
        self.assertEqual(len(search.calls), 6)
        self.assertEqual(len(journal["research_rounds"]), 2)
        self.assertTrue(journal["legal_update_check"]["performed"])
        self.assertEqual(journal["legal_update_check"]["queries"][0], "проверка поправок")
        self.assertTrue(journal["court_practice_check"]["performed"])
        self.assertEqual(journal["court_practice_check"]["queries"][0], "судебная практика по теме")


class ArticleFactcheckTests(unittest.TestCase):
    def test_article_confirmed_by_source(self):
        source = Source("Норма", "https://pravo.gov.ru/norm", "Текст нормы: ст. 395 ГК РФ о процентах.", "q")
        report = VALID_REPORT.replace("Условная норма [1].", "Условная норма по ст. 395 ГК РФ [1].")
        issues = validate_report(report, [source])
        self.assertFalse(any("статьи" in issue for issue in issues))

    def test_article_not_in_sources_is_flagged(self):
        report = VALID_REPORT.replace("Условная норма [1].", "Условная норма по ст. 395 ГК РФ [1].")
        issues = validate_report(report, [SOURCE])
        self.assertTrue(any("статьи" in issue for issue in issues))

    def test_similar_article_number_does_not_confirm(self):
        source = Source("Норма", "https://pravo.gov.ru/norm", "Текст нормы: ст. 1395 ГК РФ.", "q")
        report = VALID_REPORT.replace("Условная норма [1].", "Условная норма по ст. 395 ГК РФ [1].")
        issues = validate_report(report, [source])
        self.assertTrue(any("статьи" in issue for issue in issues))


class ConsistentModel:
    def complete(self, system, user):
        return json.dumps({"consistent": True, "issues": []})


class ReflectingModel:
    """Самопроверка находит противоречие, исправление возвращает валидный отчёт."""

    def complete(self, system, user):
        if "consistent" in system:
            return json.dumps({"consistent": False, "issues": ["Разделы противоречат друг другу."]})
        if "Замечания:" in user:
            return VALID_REPORT
        return VALID_REPORT


class BrokenReflectionModel:
    """Самопроверка возвращает не-JSON: этап должен деградировать без падения."""

    def complete(self, system, user):
        if "consistent" in system:
            return "это не JSON"
        return VALID_REPORT


class SelfReflectionTests(unittest.TestCase):
    def test_consistent_report_passes(self):
        report, journal = reflect_on_report(ConsistentModel(), "Вопрос", VALID_REPORT, [SOURCE])
        self.assertEqual(report, VALID_REPORT)
        self.assertTrue(journal["performed"])
        self.assertTrue(journal["consistent"])
        self.assertFalse(journal["repaired"])

    def test_issues_trigger_repair(self):
        report, journal = reflect_on_report(ReflectingModel(), "Вопрос", VALID_REPORT, [SOURCE])
        self.assertTrue(journal["repaired"])
        self.assertEqual(journal["issues"], ["Разделы противоречат друг другу."])
        self.assertEqual(validate_report(report, [SOURCE]), [])

    def test_broken_reflection_raises_for_caller_to_catch(self):
        with self.assertRaises(AgentError):
            reflect_on_report(BrokenReflectionModel(), "Вопрос", VALID_REPORT, [SOURCE])


class CourtFailingModel(IterativeModel):
    def complete(self, system, user):
        if "court_queries" in system:
            return "это не JSON"
        return super().complete(system, user)


class CourtStageDegradationTests(unittest.TestCase):
    def test_court_stage_failure_does_not_break_run(self):
        model = CourtFailingModel()
        search = IterativeSearch()
        with patch("agent.OpenAICompatibleModel", return_value=model), patch("agent.TavilySearch", return_value=search), patch("pravo_portal.PravoPortalSearch", return_value=search), patch("pravo_ips.PravoIpsBank") as ips_mock:
            with tempfile.TemporaryDirectory() as directory:
                _, run_path = run("Вопрос", 1, 6, Path(directory))
                journal = json.loads(run_path.read_text(encoding="utf-8"))
        self.assertFalse(journal["court_practice_check"]["performed"])
        self.assertTrue(journal["legal_update_check"]["performed"])
        self.assertTrue(journal["report_validated"])


if __name__ == "__main__":
    unittest.main()


class ProvenanceSecurityTests(unittest.TestCase):
    def test_official_portal_requires_exact_host(self):
        from agent import classify_source_authority
        self.assertEqual(classify_source_authority("https://publication.pravo.gov.ru/Document/View/123"), "official_legal_portal")
        self.assertEqual(classify_source_authority("https://publication.pravo.gov.ru.evil.example/"), "unclassified")

    def test_consultant_is_not_official_portal(self):
        from agent import classify_source_authority
        self.assertEqual(classify_source_authority("https://www.consultant.ru/document/"), "secondary_legal_reference")



class ServiceUrlSafetyTests(unittest.TestCase):
    def test_reject_empty_username(self):
        with self.assertRaises(AgentError):
            validate_service_url("https://@api.example.com/v1")

    def test_reject_empty_password(self):
        with self.assertRaises(AgentError):
            validate_service_url("https://user:@api.example.com/v1")

    def test_allow_https_endpoint(self):
        validate_service_url("https://api.example.com/v1")


class UnreviewedCorpusSafetyTests(unittest.TestCase):
    def test_unreviewed_corpus_cannot_be_enabled(self):
        with patch.dict(os.environ, {"ENABLE_UNREVIEWED_STATUTE_CORPUS": "true"}):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(AgentError, "корпус"):
                    run("Тест", 1, 2, Path(directory), demo=False)
