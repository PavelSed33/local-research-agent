import io
import json
import os
import tempfile
import unittest
import zipfile
import urllib.error
from pathlib import Path
from unittest.mock import patch

import agent
from pravo_portal import PravoPortalSearch, FederatedLegalSearch
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
            def search(self, query, limit, **kwargs):
                return [Source(query, f"https://example.test/{query}", f"Материал {query}", query)]

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(agent, "OpenAICompatibleModel", return_value=Model()), patch.object(
                agent, "TavilySearch", return_value=Search()
            ), patch("pravo_portal.PravoPortalSearch", return_value=Search()), patch(
                "pravo_ips.PravoIpsBank", return_value=Search()
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
        self.assertEqual(find_unconfirmed_articles(report, sources), ["15 (ГК РФ)"])

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



class OfficialPravoPortalIntegrationTests(unittest.TestCase):
    def test_search_fetches_official_document_text_and_dates(self):
        search_response = json.dumps([{
            "EoNumber": "123456789",
            "DocumentName": "Федеральный закон",
            "DocumentNumber": "123-ФЗ",
            "SignDate": "2025-01-10",
            "PubDate": "2025-01-11",
            "EffectiveDate": "2025-02-01",
        }]).encode("utf-8")
        text_response = json.dumps({"Text": "<p>Текст официального акта.</p>"}).encode("utf-8")
        with patch("urllib.request.urlopen", side_effect=[
            io.BytesIO(search_response), io.BytesIO(text_response)
        ]) as mocked:
            client = PravoPortalSearch()
            sources = client.search("Федеральный закон", 3)
        self.assertEqual(len(sources), 1)
        self.assertIn("publication.pravo.gov.ru", sources[0].url)
        self.assertIn("123-ФЗ", sources[0].text)
        self.assertIn("2025-02-01", sources[0].text)
        self.assertIn("Текст официального акта.", sources[0].text)
        self.assertEqual(mocked.call_count, 2)

    def test_portal_refuses_non_official_or_non_https_base_url(self):
        with self.assertRaisesRegex(agent.AgentError, "официальный HTTPS-домен"):
            PravoPortalSearch("http://example.com")

    def test_federated_search_keeps_tavily_results_when_portal_is_down(self):
        class BrokenPortal:
            def search(self, query, limit):
                raise agent.AgentError("портал временно недоступен")
        class WorkingSearch:
            def search(self, query, limit):
                return [Source("Найденный источник", "https://example.test/law", "Текст", query)]
        combined = FederatedLegalSearch(BrokenPortal(), WorkingSearch())
        results = combined.search("закон", 2)
        self.assertEqual(len(results), 1)
        self.assertTrue(any("портал временно недоступен" in item for item in combined.last_errors))

    def test_portal_response_size_is_bounded(self):
        class LargeResponse(io.BytesIO):
            pass
        with patch("urllib.request.urlopen", return_value=LargeResponse(b"x" * (2 * 1024 * 1024 + 1))):
            client = PravoPortalSearch()
            with self.assertRaisesRegex(agent.AgentError, "больше 2 МиБ"):
                client._get("/api/Documents", {"Name": "тест"})


class OfficialIntegratedLegalBankTests(unittest.TestCase):
    def test_history_selects_snapshot_not_later_than_requested_date(self):
        from pravo_ips import PravoIpsBank

        search_page = (
            '<html><a href="?docbody=&nd=102000001&rdk=1">Федеральный закон № 1-ФЗ</a></html>'
        )
        history_page = (
            '<html>Редакция от 01.01.2024'
            '<a href="?docbody=&nd=102000001&rdk=1">редакция 2020</a>'
            '<a href="?docbody=&nd=102000001&rdk=2">редакция 2024</a></html>'
        )
        old_page = '<html>Редакция от 01.01.2020 Текст старой редакции.</html>'
        new_page = '<html>Редакция от 01.01.2024 Текст новой редакции.</html>'
        with patch("urllib.request.urlopen", side_effect=[
            io.BytesIO(search_page.encode()),
            io.BytesIO(history_page.encode()),
            io.BytesIO(old_page.encode()),
            io.BytesIO(new_page.encode()),
        ]):
            sources = PravoIpsBank().search("тестовый закон", 1, as_of="2022-06-01")
        self.assertEqual(len(sources), 1)
        self.assertIn("Редакция-снимок: 2020-01-01", sources[0].text)
        self.assertIn("Текст старой редакции", sources[0].text)
        self.assertIn("не является официальным опубликованием", sources[0].text)
        self.assertIn("не равна", sources[0].text)

    def test_bank_refuses_invalid_document_ids_and_dates(self):
        from pravo_ips import PravoIpsBank

        bank = PravoIpsBank()
        with self.assertRaisesRegex(agent.AgentError, "идентификатор"):
            bank.get_history("../102000001")
        with self.assertRaisesRegex(agent.AgentError, "формате"):
            bank.get_history("102000001", as_of="01.01.2024")

    def test_date_parser_rejects_impossible_edition_date(self):
        from pravo_ips import _parse_date, _document_id

        self.assertIsNone(_parse_date("Редакция от 31.02.2024"))
        self.assertEqual(_parse_date("Редакция от 01.02.2024"), "2024-02-01")
        self.assertIsNone(_document_id("//evil.example/proxy/ips/?nd=102000001"))
        self.assertIsNone(_document_id("http://pravo.gov.ru/proxy/ips/?nd=102000001"))
        self.assertEqual(_document_id("?docbody=&nd=102000001"), "102000001")



    def test_history_returns_full_snapshot_text_and_runs_revision_audit(self):
        from pravo_ips import PravoIpsBank

        history_page = (
            '<html><a href="?docbody=&nd=102000001&rdk=1">редакция 2020</a>'
            '<a href="?docbody=&nd=102000001&rdk=2">редакция 2024</a></html>'
        )
        old_page = '<html>Редакция от 01.01.2020\\nСтатья 1. Старый текст.\\nСтатья 2. Без изменений.</html>'
        new_page = '<html>Редакция от 01.01.2024\\nСтатья 1. Новый текст.\\nСтатья 2. Без изменений.</html>'
        with patch("urllib.request.urlopen", side_effect=[
            io.BytesIO(history_page.encode()),
            io.BytesIO(old_page.encode()),
            io.BytesIO(new_page.encode()),
        ]):
            result = PravoIpsBank().get_history("102000001")

        self.assertEqual(len(result["snapshots"]), 2)
        self.assertIn("Старый текст", result["snapshots"][0]["text"])
        self.assertIn("Новый текст", result["snapshots"][1]["text"])
        audit = result["revision_chain_audit"]
        self.assertFalse(audit["verified"])
        self.assertFalse(audit["coverage_verified"])
        self.assertEqual(audit["change_count"], 1)
        self.assertEqual(audit["changed_provisions"][0]["change_kind"], "text_changed")



class ProvisionEffectivePeriodTests(unittest.TestCase):
    def _event(self, effective_from, number, quote):
        return {
            "provision_id": "ФЗ-123:ст.5:ч.2",
            "effective_from": effective_from,
            "act_title": "Федеральный закон",
            "act_number": number,
            "official_publication_id": "0001202401010001",
            "official_url": "https://publication.pravo.gov.ru/document/0001202401010001",
            "effective_date_quote": quote,
        }

    def test_builds_non_overlapping_periods_from_effective_dates(self):
        from legal_history import build_provision_timeline

        result = build_provision_timeline(
            "ФЗ-123:ст.5:ч.2",
            [
                self._event("2020-01-01", "123-ФЗ", "Вступает в силу с 01.01.2020"),
                self._event("2024-03-01", "456-ФЗ", "Вступает в силу с 01.03.2024"),
            ],
            timeline_complete=True,
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["status"], "verified_timeline")
        self.assertEqual(result["periods"][0]["valid_from"], "2020-01-01")
        self.assertEqual(result["periods"][0]["valid_to"], "2024-02-29")
        self.assertEqual(result["periods"][1]["valid_from"], "2024-03-01")
        self.assertIsNone(result["periods"][1]["valid_to"])

    def test_incomplete_timeline_is_never_verified(self):
        from legal_history import build_provision_timeline

        result = build_provision_timeline(
            "ФЗ-123:ст.5:ч.2",
            [self._event("2024-03-01", "456-ФЗ", "Вступает в силу с 01.03.2024")],
            timeline_complete=False,
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["status"], "candidate_timeline_incomplete")

    def test_rejects_wrong_scope_quote_and_nonofficial_source(self):
        from legal_history import build_provision_timeline

        event = self._event("2024-03-01", "456-ФЗ", "Опубликован 01.03.2024")
        event["provision_id"] = "другая норма"
        event["official_url"] = "https://example.com/act"
        result = build_provision_timeline("ФЗ-123:ст.5:ч.2", [event], timeline_complete=True)
        self.assertFalse(result["verified"])
        self.assertEqual(result["periods"], [])
        self.assertEqual(result["status"], "insufficient_or_invalid_evidence")

    def test_rejects_conflicting_events_on_same_date(self):
        from legal_history import build_provision_timeline

        result = build_provision_timeline(
            "ФЗ-123:ст.5:ч.2",
            [
                self._event("2024-03-01", "456-ФЗ", "Вступает в силу с 01.03.2024"),
                self._event("2024-03-01", "789-ФЗ", "Вступает в силу с 01.03.2024"),
            ],
            timeline_complete=True,
        )
        self.assertEqual(result["status"], "conflicting_same_day_events")
        self.assertFalse(result["verified"])

    def test_agent_extracts_only_events_with_verbatim_official_quotes(self):
        import json
        from agent import extract_provision_history

        source_text = (
            "Федеральный закон № 456-ФЗ. Статья 5, часть 2. "
            "Вступает в силу с 01.03.2024. Официальная публикация 0001202401010001."
        )
        source = Source(
            "Официальная публикация", 
            "https://publication.pravo.gov.ru/document/0001202401010001",
            source_text, "проверка",
        )
        payload = {
            "provision_id": "ФЗ-456:ст.5:ч.2",
            "provision_source_url": source.url,
            "provision_quote": "Статья 5, часть 2",
            "events": [{
                "effective_from": "2024-03-01",
                "act_title": "Федеральный закон",
                "act_number": "456-ФЗ",
                "official_publication_id": "0001202401010001",
                "source_url": source.url,
                "effective_date_quote": "Вступает в силу с 01.03.2024",
            }],
        }

        class Model:
            def complete(self, system, user):
                return json.dumps(payload, ensure_ascii=False)

        result = extract_provision_history(Model(), "проверить ФЗ-456 ст.5 ч.2", [source])
        self.assertEqual(result["status"], "candidate_timeline_incomplete")
        self.assertFalse(result["verified"])
        self.assertEqual(result["periods"][0]["valid_from"], "2024-03-01")

    def test_agent_rejects_hallucinated_effective_date_quote(self):
        import json
        from agent import extract_provision_history

        source = Source(
            "Официальная публикация",
            "https://publication.pravo.gov.ru/document/0001202401010001",
            "Федеральный закон № 456-ФЗ. Статья 5, часть 2. Опубликован 01.03.2024. "
            "Официальная публикация 0001202401010001.",
            "проверка",
        )
        payload = {
            "provision_id": "ФЗ-456:ст.5:ч.2",
            "provision_source_url": source.url,
            "provision_quote": "Статья 5, часть 2",
            "events": [{
                "effective_from": "2024-03-01",
                "act_title": "Федеральный закон",
                "act_number": "456-ФЗ",
                "official_publication_id": "0001202401010001",
                "source_url": source.url,
                "effective_date_quote": "Вступает в силу с 01.03.2024",
            }],
        }

        class Model:
            def complete(self, system, user):
                return json.dumps(payload, ensure_ascii=False)

        result = extract_provision_history(Model(), "проверить ФЗ-456 ст.5 ч.2", [source])
        self.assertEqual(result["status"], "no_effective_date_events")
        self.assertFalse(result["verified"])
        self.assertEqual(result["periods"], [])
        self.assertTrue(result["events_rejected"])




class RevisionChainCoverageTests(unittest.TestCase):
    def test_detects_changed_articles_between_dated_snapshots(self):
        from legal_history import audit_revision_chain

        result = audit_revision_chain(
            [
                {"edition_date": "2020-01-01", "text": "Статья 5. Старая редакция\nТекст нормы."},
                {"edition_date": "2024-01-01", "text": "Статья 5. Новая редакция\nНовый текст нормы."},
            ],
            [{"effective_from": "2022-06-01"}],
        )
        self.assertEqual(result["change_count"], 1)
        self.assertEqual(result["changed_provisions"][0]["change_kind"], "text_changed")
        self.assertFalse(result["verified"])
        self.assertFalse(result["coverage_verified"])

    def test_reports_missing_or_undated_snapshots(self):
        from legal_history import audit_revision_chain

        result = audit_revision_chain(
            [{"edition_date": None, "text": "Статья 1. Текст"}],
            [],
        )
        self.assertTrue(result["gaps"])
        self.assertFalse(result["verified"])

    def test_flags_events_outside_available_snapshot_range(self):
        from legal_history import audit_revision_chain

        result = audit_revision_chain(
            [
                {"edition_date": "2020-01-01", "text": "Статья 1. А"},
                {"edition_date": "2022-01-01", "text": "Статья 1. Б"},
            ],
            [{"effective_from": "2024-03-01"}],
        )
        self.assertTrue(any("вне диапазона" in gap for gap in result["gaps"]))
        self.assertEqual(result["status"], "revision_chain_gaps_detected")


if __name__ == "__main__":
    unittest.main()
