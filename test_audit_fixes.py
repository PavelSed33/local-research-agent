import io
import os
import tempfile
import unittest
import zipfile
import urllib.error
from pathlib import Path
from unittest.mock import patch

import agent
from agent import Source, current_check_date, deduplicate_queries, extract_json, find_unconfirmed_articles, reflect_on_report, classify_source_authority
from api import create_server, handle_ask, SessionStore, SESSION_MAX_COUNT, LegalApiHandler
from redline import diff_documents, MAX_DIFF_DOCUMENT_CHARS
from registry import build_registry, MAX_REGISTRY_FILE_BYTES
from doctext import MAX_DOCX_BYTES, MAX_DOCX_XML_BYTES, MAX_INPUT_FILE_BYTES, ensure_document_transfer_allowed, extract_docx_text, parse_demo_flag, read_document_path
from review import handle_review, validate_review
from extract import handle_extract, validate_extraction


class DocxResourceLimitTests(unittest.TestCase):
    def test_rejects_oversized_archive_input(self):
        with self.assertRaisesRegex(ValueError, "превышает допустимый размер"):
            extract_docx_text(b"x" * (MAX_DOCX_BYTES + 1))

    def test_rejects_oversized_document_xml_before_reading(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", b"x" * (MAX_DOCX_XML_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "Основной XML документа"):
            extract_docx_text(stream.getvalue())


class ContractPrivacyTests(unittest.TestCase):
    def test_remote_llm_requires_explicit_consent(self):
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://api.openai.com/v1"}, clear=True):
            with self.assertRaisesRegex(ValueError, "Отправка текста договора"):
                ensure_document_transfer_allowed()

    def test_loopback_llm_does_not_require_cloud_consent(self):
        with patch.dict(os.environ, {"LLM_BASE_URL": "http://127.0.0.1:11434/v1"}, clear=True):
            ensure_document_transfer_allowed()

    def test_remote_llm_is_allowed_after_explicit_consent(self):
        with patch.dict(os.environ, {
            "LLM_BASE_URL": "https://llm.example.test/v1",
            "ALLOW_CLOUD_DOCUMENTS": "true",
        }, clear=True):
            ensure_document_transfer_allowed()


class ApiExposureSecurityTests(unittest.TestCase):
    def test_external_bind_requires_token(self):
        with self.assertRaisesRegex(ValueError, "API_TOKEN обязателен"):
            create_server("0.0.0.0", 0, Path("."), "")

    def test_loopback_bind_can_remain_local_without_token(self):
        server = create_server("127.0.0.1", 0, Path("."))
        try:
            self.assertEqual(server.api_token, "")
        finally:
            server.server_close()


class ResearchEvidenceRetentionTests(unittest.TestCase):
    def test_update_and_court_sources_survive_full_core_source_limit(self):
        class Model:
            def complete(self, system, user):
                if "legal_update_queries" in system:
                    return '{"legal_update_queries":["update-check"],"notes":"check"}'
                if "court_queries" in system:
                    return '{"court_queries":["court-check"],"notes":"case law"}'
                if "additional_queries" in system:
                    return '{"sufficient":true,"additional_queries":[],"reason":"enough"}'
                if '"queries"' in system or "ключом queries" in system:
                    return '{"queries":["primary"]}'
                if "consistent" in system:
                    return '{"consistent":true,"issues":[]}'
                return (
                    "## Краткий ответ\nВывод [1].\n\n"
                    "## Правовое основание\nОснование [1].\n\n"
                    "## Актуальность и изменения\nПроверено [1].\n\n"
                    "## Как применить к ситуации\nПрактический шаг.\n\n"
                    "## Ограничения\nТребуется проверка."
                )

        class Search:
            def search(self, query, limit):
                return [Source(query, f"https://example.test/{query}", f"Материал {query}", query)]

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(agent, "OpenAICompatibleModel", return_value=Model()), patch.object(
                agent, "TavilySearch", return_value=Search()
            ):
                _, journal_path = agent.run(
                    "тестовый вопрос", 1, 1, Path(directory)
                )
            import json
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        urls = {item["url"] for item in journal["sources"]}
        self.assertIn("https://example.test/тестовый вопрос", urls)
        self.assertIn("https://example.test/update-check", urls)
        self.assertIn("https://example.test/court-check", urls)



class SecondAuditRegressionTests(unittest.TestCase):
    def test_session_store_has_a_hard_capacity(self):
        store = SessionStore()
        for index in range(SESSION_MAX_COUNT + 5):
            store.remember(f"session-{index}", "q", "a")
        self.assertLessEqual(len(store._sessions), SESSION_MAX_COUNT)
        self.assertNotIn("session-0", store._sessions)

    def test_demo_requires_a_json_boolean(self):
        with self.assertRaisesRegex(ValueError, "demo.*логическим"):
            handle_ask({"question": "тест", "demo": "false"}, Path("."))

    def test_diff_rejects_oversized_text_before_matching(self):
        with self.assertRaisesRegex(ValueError, "не должна превышать"):
            diff_documents("x" * (MAX_DIFF_DOCUMENT_CHARS + 1), "короткий текст")

    def test_registry_skips_oversized_file_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            large = folder / "large.txt"
            with large.open("wb") as handle:
                handle.truncate(MAX_REGISTRY_FILE_BYTES + 1)
            rows, extractions, skipped = build_registry(folder, demo=True)
        self.assertEqual(rows, [])
        self.assertEqual(extractions, [])
        self.assertTrue(any("больше допустимых" in note for note in skipped))


class StrictDocumentInputTests(unittest.TestCase):
    def test_review_rejects_string_demo_flag(self):
        with self.assertRaisesRegex(ValueError, "demo.*логическим"):
            handle_review({"document": "Текст договора достаточно длинный.", "demo": "false"})

    def test_extract_rejects_integer_demo_flag(self):
        with self.assertRaisesRegex(ValueError, "demo.*логическим"):
            handle_extract({"document": "Текст договора достаточно длинный.", "demo": 1})

    def test_demo_flag_defaults_to_false(self):
        self.assertIs(parse_demo_flag({}), False)
        self.assertIs(parse_demo_flag({"demo": True}), True)

    def test_cli_file_size_is_checked_before_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.txt"
            with path.open("wb") as handle:
                handle.truncate(MAX_INPUT_FILE_BYTES + 1)
            with self.assertRaisesRegex(ValueError, "превышает допустимый размер"):
                read_document_path(path)


class UserChangeRegressionTests(unittest.TestCase):
    def test_extract_json_uses_first_complete_object_not_greedy_match(self):
        self.assertEqual(extract_json('prefix {"first": 1} suffix {"second": 2}'), {"first": 1})

    def test_query_deduplication_ignores_case_and_whitespace(self):
        self.assertEqual(
            deduplicate_queries([" Закон 15 ", "закон 15", "ЗАКОН 16"]),
            ["Закон 15", "ЗАКОН 16"],
        )

    def test_check_date_is_iso_formatted(self):
        from datetime import date
        self.assertEqual(date.fromisoformat(current_check_date()).isoformat(), current_check_date())

    def test_review_requires_nonempty_recommendation(self):
        document = "Стороны согласовали условия договора и порядок оплаты."
        result = {
            "summary": "Краткое резюме.",
            "risks": [{
                "category": "цена и порядок расчётов",
                "severity": "medium",
                "quote": "порядок оплаты",
                "comment": "Условие недостаточно определённое.",
                "recommendation": "",
            }],
            "missing_clauses": [],
        }
        self.assertTrue(any("recommendation" in issue for issue in validate_review(result, document)))

    def test_extract_rejects_invalid_calendar_date_and_currency_format(self):
        document = "Договор заключён 31.02.2025. Сумма составляет 100 рублей."
        result = {
            "parties": [],
            "amounts": [{
                "label": "Цена",
                "value": "100 рублей",
                "currency": "rub",
                "quote": "Сумма составляет 100 рублей.",
            }],
            "dates": [{
                "label": "Дата заключения",
                "date": "31.02.2025",
                "quote": "Договор заключён 31.02.2025.",
            }],
            "penalties": [],
            "term": None,
            "jurisdiction": None,
        }
        issues = validate_extraction(result, document)
        self.assertTrue(any("currency" in issue for issue in issues))
        self.assertTrue(any("реальной календарной датой" in issue for issue in issues))

    def test_retry_post_json_after_transient_http_error(self):
        class Response(io.BytesIO):
            pass

        transient = urllib.error.HTTPError(
            "https://example.test", 503, "unavailable", {}, io.BytesIO(b"temporary")
        )
        with patch("urllib.request.urlopen", side_effect=[transient, Response(b'{"ok": true}')]), patch(
            "agent.time.sleep"
        ):
            self.assertEqual(agent.post_json("https://example.test", {}, {}), {"ok": True})

    def test_article_number_match_does_not_accept_decimal_prefix(self):
        report = "## Правовое основание\nСогласно статье 15 ГК РФ."
        sources = [Source("Источник", "https://example.test", "См. статью 15.1 закона.", "q")]
        self.assertEqual(find_unconfirmed_articles(report, sources), ["15"])

    def test_reflection_rejects_string_boolean(self):
        class Model:
            def complete(self, system, user):
                return '{"consistent": "false", "issues": []}'
        with self.assertRaisesRegex(agent.AgentError, "consistent неверного типа"):
            reflect_on_report(Model(), "тема", "отчёт", [])



class LegalCitationProvenanceTests(unittest.TestCase):
    def test_article_number_from_different_code_does_not_confirm_citation(self):
        report = "## Правовое основание\\nПрименяется статья 15 ГК РФ."
        sources = [Source("Трудовой кодекс РФ", "https://pravo.gov.ru/law", "Статья 15. Общие положения.", "q")]
        self.assertEqual(find_unconfirmed_articles(report, sources), ["15 (ГК РФ)"])

    def test_article_without_identified_statute_is_unconfirmed(self):
        report = "## Правовое основание\\nПрименяется статья 15."
        sources = [Source("Сборник норм", "https://example.test", "Статья 15. Текст.", "q")]
        self.assertEqual(
            find_unconfirmed_articles(report, sources),
            ["15 (не указан конкретный закон или кодекс)"],
        )

    def test_article_and_matching_statute_in_same_source_are_confirmed(self):
        report = "## Правовое основание\\nПрименяется статья 15 ГК РФ."
        sources = [Source("Гражданский кодекс Российской Федерации", "https://pravo.gov.ru/law", "Статья 15. Защита гражданских прав.", "q")]
        self.assertEqual(find_unconfirmed_articles(report, sources), [])

    def test_federal_law_number_must_match_source(self):
        report = "## Правовое основание\\nПрименяется статья 6 152-ФЗ."
        sources = [Source("Федеральный закон № 149-ФЗ", "https://pravo.gov.ru/law", "Статья 6. Информация.", "q")]
        self.assertEqual(find_unconfirmed_articles(report, sources), ["6 (152-ФЗ)"])

    def test_source_authority_is_classified_without_claiming_validity(self):
        self.assertEqual(classify_source_authority("https://pravo.gov.ru/document"), "official_legal_portal")
        self.assertEqual(classify_source_authority("https://kad.arbitr.ru/"), "official_court")
        self.assertEqual(classify_source_authority("https://sudact.ru/"), "case_law_aggregator")
        self.assertEqual(classify_source_authority("https://www.consultant.ru/"), "secondary_legal_reference")
        self.assertEqual(classify_source_authority("https://unknown.example/"), "unclassified")


if __name__ == "__main__":
    unittest.main()
