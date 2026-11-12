import io
from email.message import Message
import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agent import LEGAL_DISCLAIMER
from api import LegalApiHandler, RequestValidationError, SessionStore, create_server


def http_request(port, method, path, payload=None, token=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class ApiTestCase(unittest.TestCase):
    api_token = ""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.server = create_server("127.0.0.1", 0, Path(self.directory.name), self.api_token)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.directory.cleanup)

    def request(self, method, path, payload=None, token=None):
        return http_request(self.port, method, path, payload, token)


class ResponseSecurityHeadersTests(ApiTestCase):
    def test_sensitive_api_response_is_not_cacheable(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/ask",
            data=json.dumps({"question": "Тест", "demo": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")


class HttpConnectionSafetyTests(ApiTestCase):
    def test_invalid_json_closes_keepalive_connection(self):
        body = b'not-json'
        request = (
            b"POST /ask HTTP/1.1\\r\\n"
            b"Host: 127.0.0.1\\r\\n"
            b"Connection: keep-alive\\r\\n"
            b"Content-Type: application/json\\r\\n"
            + f"Content-Length: {len(body)}\\r\\n".encode("ascii")
            + b"\\r\\n" + body
        )
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as connection:
            connection.settimeout(5)
            connection.sendall(request)
            response = b""
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                response += chunk
        self.assertIn(b" 400 ", response)
        self.assertIn(b"Connection: close", response)


class HttpBodyValidationTests(unittest.TestCase):
    def test_truncated_body_is_rejected(self):
        handler = object.__new__(LegalApiHandler)
        handler.headers = Message()
        handler.headers["Content-Length"] = "20"
        handler.rfile = io.BytesIO(b'{"question":"hi"}')
        with self.assertRaisesRegex(RequestValidationError, "не полностью"):
            handler._read_payload()


class HttpFramingSafetyTests(unittest.TestCase):
    def make_handler(self, headers):
        handler = object.__new__(LegalApiHandler)
        handler.headers = Message()
        for key, value in headers:
            handler.headers[key] = value
        handler.rfile = io.BytesIO(b'{"question":"hi"}')
        return handler

    def test_duplicate_content_length_is_rejected(self):
        handler = self.make_handler([
            ("Content-Length", "17"), ("Content-Length", "17")
        ])
        with self.assertRaisesRegex(RequestValidationError, "один заголовок"):
            handler._read_payload()

    def test_transfer_encoding_is_rejected(self):
        handler = self.make_handler([
            ("Transfer-Encoding", "chunked"), ("Content-Length", "17")
        ])
        with self.assertRaisesRegex(RequestValidationError, "Transfer-Encoding"):
            handler._read_payload()


class SessionCapacityTests(unittest.TestCase):
    def test_session_cache_evicts_oldest_at_capacity(self):
        store = SessionStore()
        for index in range(1001):
            store.remember(f"session-{index}", "question", "answer")
        self.assertLessEqual(len(store._sessions), 1000)
        self.assertNotIn("session-0", store._sessions)
        self.assertIn("session-1000", store._sessions)


class ApiImportSafetyTests(unittest.TestCase):
    def test_api_does_not_expose_unaudited_statute_routes(self):
        import api
        self.assertFalse(hasattr(api, "statutes_get"))
        self.assertFalse(hasattr(api, "statutes_list"))


class ServerBindingSecurityTests(unittest.TestCase):
    def test_external_binding_requires_token(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "API_TOKEN"):
                create_server("0.0.0.0", 0, Path(directory), "")

    def test_localhost_without_token_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server("127.0.0.1", 0, Path(directory), "")
            server.server_close()


class HealthEndpointTests(ApiTestCase):
    def test_health_returns_status(self):
        status, payload = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["demo_available"])
        self.assertIn("llm_configured", payload)
        self.assertIn("tavily_configured", payload)
        self.assertIn("metrics", payload)

    def test_unknown_path_returns_404(self):
        status, payload = self.request("GET", "/unknown")
        self.assertEqual(status, 404)
        self.assertIn("error", payload)

    def test_openapi_spec_is_served(self):
        status, payload = self.request("GET", "/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(payload["openapi"], "3.0.3")
        for path in ("/health", "/ask", "/review", "/extract", "/diff", "/penalty", "/check", "/timeline", "/draft", "/triage", "/duty", "/export", "/protocol", "/scoring"):
            self.assertIn(path, payload["paths"])


class MetricsTests(ApiTestCase):
    def test_requests_are_counted_in_health(self):
        self.request("POST", "/duty", {"mode": "claim", "court": "general", "amount": 750000})
        self.request("POST", "/duty", {"mode": "claim", "court": "general", "amount": 750000})
        self.request("GET", "/health")  # первый вызов фиксируется в метриках
        status, payload = self.request("GET", "/health")
        self.assertEqual(status, 200)
        requests = payload["metrics"]["requests"]
        self.assertEqual(requests["POST /duty"]["count"], 2)
        self.assertEqual(requests["POST /duty"]["errors"], 0)
        self.assertGreaterEqual(requests["POST /duty"]["avg_ms"], 0)
        self.assertIn("GET /health", requests)

    def test_errors_are_counted(self):
        self.request("POST", "/duty", {"mode": "claim"})  # 400: нет court
        status, payload = self.request("GET", "/health")
        self.assertEqual(status, 200)
        entry = payload["metrics"]["requests"]["POST /duty"]
        self.assertEqual(entry["count"], 1)
        self.assertEqual(entry["errors"], 1)


class AskEndpointTests(ApiTestCase):
    def test_demo_question_returns_structured_answer(self):
        status, payload = self.request(
            "POST", "/ask", {"question": "Тестовый правовой вопрос", "demo": True}
        )
        self.assertEqual(status, 200)
        self.assertIn("# Тестовый правовой вопрос", payload["answer"])
        self.assertEqual(payload["disclaimer"], LEGAL_DISCLAIMER)
        self.assertEqual(len(payload["sources"]), 2)
        self.assertEqual(payload["sources"][0]["url"], "https://example.org/demo-agent")
        self.assertTrue(payload["checked_on"])
        self.assertTrue(payload["demo"])

    def test_response_does_not_leak_server_file_paths(self):
        status, payload = self.request(
            "POST", "/ask", {"question": "Тест путей", "demo": True}
        )
        self.assertEqual(status, 200)
        self.assertNotIn("report_file", payload)
        self.assertNotIn("journal_file", payload)
        self.assertNotIn(self.directory.name, json.dumps(payload, ensure_ascii=False))

    def test_oversized_question_is_rejected_before_research(self):
        status, payload = self.request(
            "POST", "/ask", {"question": "А" * 8001, "demo": True}
        )
        self.assertEqual(status, 400)
        self.assertIn("8000", payload["error"])

    def test_as_of_is_preserved_in_demo_response(self):
        status, payload = self.request(
            "POST", "/ask", {"question": "Норма на дату", "demo": True, "as_of": "2023-03-01"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["as_of"], "2023-03-01")

    def test_invalid_as_of_returns_400(self):
        status, payload = self.request(
            "POST", "/ask", {"question": "Норма", "demo": True, "as_of": "2023-02-30"}
        )
        self.assertEqual(status, 400)
        self.assertIn("as_of", payload["error"])

    def test_demo_string_is_not_accepted(self):
        status, payload = self.request(
            "POST", "/ask", {"question": "Вопрос", "demo": "false"}
        )
        self.assertEqual(status, 400)
        self.assertIn("demo", payload["error"])

    def test_missing_question_returns_400(self):
        status, payload = self.request("POST", "/ask", {"demo": True})
        self.assertEqual(status, 400)
        self.assertIn("question", payload["error"])

    def test_out_of_range_queries_returns_400(self):
        status, payload = self.request(
            "POST", "/ask", {"question": "Вопрос", "queries": 9, "demo": True}
        )
        self.assertEqual(status, 400)
        self.assertIn("queries", payload["error"])

    def test_invalid_json_returns_400(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/ask",
            data="не json".encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 400)


REVIEW_DOCUMENT = (
    "ДОГОВОР АРЕНДЫ № 12. ООО «Арендодатель» и ООО «Арендатор» заключили договор. "
    "Арендодатель вправе в одностороннем порядке изменить размер арендной платы."
)


class ReviewEndpointTests(ApiTestCase):
    def test_review_demo_returns_risks(self):
        status, payload = self.request(
            "POST", "/review", {"document": REVIEW_DOCUMENT, "demo": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(len(payload["risks"]), 1)
        self.assertEqual(payload["disclaimer"], LEGAL_DISCLAIMER)
        self.assertTrue(payload["checked_on"])
        self.assertTrue(payload["demo"])

    def test_review_without_document_returns_400(self):
        status, payload = self.request("POST", "/review", {"demo": True})
        self.assertEqual(status, 400)
        self.assertIn("document", payload["error"])


EXTRACT_DOCUMENT = (
    "ДОГОВОР АРЕНДЫ № 12 от 01.02.2026. ООО «Арендодатель», ИНН 7707083893, "
    "ОГРН 1027700132195, и ООО «Арендатор» заключили договор. "
    "Арендная плата составляет 100 000 руб. в месяц."
)


class ExtractEndpointTests(ApiTestCase):
    def test_extract_demo_returns_entities(self):
        status, payload = self.request(
            "POST", "/extract", {"document": EXTRACT_DOCUMENT, "demo": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["parties"][0]["inn"], "7707083893")
        self.assertEqual(payload["amounts"][0]["value"], "100 000 руб.")
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["disclaimer"], LEGAL_DISCLAIMER)
        self.assertTrue(payload["demo"])

    def test_extract_without_document_returns_400(self):
        status, payload = self.request("POST", "/extract", {"demo": True})
        self.assertEqual(status, 400)
        self.assertIn("document", payload["error"])


class DiffEndpointTests(ApiTestCase):
    old = "ДОГОВОР АРЕНДЫ № 12. Арендная плата составляет 100 000 руб. в месяц. Споры — в Арбитражном суде."
    new = "ДОГОВОР АРЕНДЫ № 12. Арендная плата составляет 120 000 руб. в месяц. Споры — в Арбитражном суде."

    def test_diff_returns_changes(self):
        status, payload = self.request(
            "POST", "/diff", {"document_a": self.old, "document_b": self.new}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["summary"]["changed"], 1)
        self.assertIn("версия A", payload["diff"])

    def test_diff_without_side_returns_400(self):
        status, payload = self.request("POST", "/diff", {"document_a": self.old})
        self.assertEqual(status, 400)
        self.assertIn("document_b", payload["error"])


class PenaltyEndpointTests(ApiTestCase):
    def test_penalty_key_rate(self):
        status, payload = self.request(
            "POST", "/penalty",
            {"mode": "key_rate", "amount": "365000", "start_date": "01.08.2026", "end_date": "10.08.2026"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["total"], "1 400 руб. 00 коп.")
        self.assertEqual(payload["period"]["days"], 10)

    def test_penalty_contractual(self):
        status, payload = self.request(
            "POST", "/penalty",
            {"mode": "contractual", "amount": "100 000 руб.", "rate_per_day": "0,1",
             "start_date": "01.01.2026", "end_date": "10.01.2026"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], "1 000 руб. 00 коп.")

    def test_penalty_without_mode_returns_400(self):
        status, payload = self.request("POST", "/penalty", {"amount": "1000", "start_date": "01.01.2026"})
        self.assertEqual(status, 400)
        self.assertIn("mode", payload["error"])


class CheckEndpointTests(ApiTestCase):
    def test_check_demo(self):
        status, payload = self.request("POST", "/check", {"inn": "7707083893", "demo": True})
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["company"]["inn"], "7707083893")
        self.assertIn("risk_level", payload)

    def test_check_without_inn_returns_400(self):
        status, payload = self.request("POST", "/check", {"demo": True})
        self.assertEqual(status, 400)
        self.assertIn("inn", payload["error"])

    def test_check_bad_inn_returns_400(self):
        status, payload = self.request("POST", "/check", {"inn": "123", "demo": True})
        self.assertEqual(status, 400)


class TimelineEndpointTests(ApiTestCase):
    materials = [
        {"name": "письмо-1.txt", "text": "15.01.2026 направили счёт № 45 на 200 000 руб. по договору аренды."},
        {"name": "письмо-2.txt", "text": "Подтверждаем получение претензии от 01.02.2026, платёж обещаем."},
    ]

    def test_timeline_demo(self):
        status, payload = self.request("POST", "/timeline", {"materials": self.materials, "demo": True})
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "draft")
        self.assertGreaterEqual(payload["events_count"], 1)
        self.assertIn("events", payload)

    def test_timeline_without_materials_returns_400(self):
        status, payload = self.request("POST", "/timeline", {"demo": True})
        self.assertEqual(status, 400)
        self.assertIn("materials", payload["error"])


class DraftEndpointTests(ApiTestCase):
    facts = (
        "По договору аренды № 12 от 01.02.2026 арендатор не оплатил аренду за сентябрь "
        "и октябрь 2026 года на сумму 200 000 руб. Нужна претензия с оплатой в 10 дней."
    )

    def test_draft_demo(self):
        status, payload = self.request(
            "POST", "/draft", {"type": "claim", "facts": self.facts, "demo": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["doc_type"], "claim")
        self.assertTrue(payload["document"])

    def test_draft_unknown_type_returns_400(self):
        status, payload = self.request(
            "POST", "/draft", {"type": "иск", "facts": self.facts, "demo": True}
        )
        self.assertEqual(status, 400)
        self.assertIn("type", payload["error"])

    def test_draft_without_facts_returns_400(self):
        status, payload = self.request("POST", "/draft", {"type": "claim", "demo": True})
        self.assertEqual(status, 400)
        self.assertIn("facts", payload["error"])


class TriageEndpointTests(ApiTestCase):
    def test_triage_demo_contract(self):
        status, payload = self.request(
            "POST", "/triage", {"text": "Коллеги, проверьте, пожалуйста, проект договора аренды.", "demo": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["category"], "contract")
        self.assertEqual(payload["recommended_endpoint"], "/review")

    def test_triage_demo_dispute(self):
        status, payload = self.request(
            "POST", "/triage", {"text": "Срочно! Контрагент не оплатил счёт, готовим претензию и иск.", "demo": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["category"], "dispute")
        self.assertEqual(payload["urgency"], "high")

    def test_triage_without_text_returns_400(self):
        status, payload = self.request("POST", "/triage", {"demo": True})
        self.assertEqual(status, 400)
        self.assertIn("text", payload["error"])


class DutyEndpointTests(ApiTestCase):
    def test_duty_claim_arbitration(self):
        status, payload = self.request(
            "POST", "/duty", {"mode": "claim", "court": "arbitration", "amount": "500 000"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["duty"], "30 000 руб. 00 коп.")
        self.assertIn("333.21", payload["article"])

    def test_duty_court_order_general(self):
        status, payload = self.request(
            "POST", "/duty", {"mode": "court_order", "court": "general", "amount": 750000}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["duty"], "10 000 руб. 00 коп.")

    def test_duty_non_property(self):
        status, payload = self.request(
            "POST", "/duty", {"mode": "non_property", "court": "general", "applicant": "individual"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["duty"], "3 000 руб. 00 коп.")

    def test_duty_old_scale_by_filing_date(self):
        status, payload = self.request(
            "POST", "/duty",
            {"mode": "claim", "court": "arbitration", "amount": 150000, "filing_date": "01.09.2024"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["duty"], "5 500 руб. 00 коп.")
        self.assertTrue(payload["warnings"])

    def test_duty_without_court_returns_400(self):
        status, payload = self.request("POST", "/duty", {"mode": "claim", "amount": 1000})
        self.assertEqual(status, 400)
        self.assertIn("court", payload["error"])


class ExportEndpointTests(ApiTestCase):
    def test_export_returns_docx(self):
        status, payload = self.request(
            "POST", "/export", {"title": "Претензия", "document": "# ПРЕТЕНЗИЯ\nТекст документа."}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["filename"], "Претензия.docx")
        self.assertGreater(payload["size_bytes"], 0)

    def test_export_without_document_returns_400(self):
        status, payload = self.request("POST", "/export", {"title": "Пусто"})
        self.assertEqual(status, 400)
        self.assertIn("document", payload["error"])


class ProtocolEndpointTests(ApiTestCase):
    doc_a = (
        "ДОГОВОР АРЕНДЫ № 12\n"
        "1. Арендная плата составляет 100 000 руб. в месяц.\n"
        "2. Арендатор вправе расторгнуть договор в любой момент.\n"
        "3. Споры разрешаются в Арбитражном суде города Москвы.\n"
    )
    doc_b = (
        "ДОГОВОР АРЕНДЫ № 12\n"
        "1. Арендная плата составляет 120 000 руб. в месяц.\n"
        "3. Споры разрешаются в Арбитражном суде города Москвы.\n"
    )

    def test_protocol_demo(self):
        status, payload = self.request(
            "POST", "/protocol", {"document_a": self.doc_a, "document_b": self.doc_b, "demo": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "draft")
        self.assertIn("ПРОТОКОЛ РАЗНОГЛАСИЙ", payload["protocol_markdown"])
        self.assertTrue(payload["rows"])

    def test_protocol_identical_returns_400(self):
        status, payload = self.request(
            "POST", "/protocol", {"document_a": self.doc_a, "document_b": self.doc_a, "demo": True}
        )
        self.assertEqual(status, 400)
        self.assertIn("совпадают", payload["error"])


class ScoringEndpointTests(ApiTestCase):
    def test_scoring_demo(self):
        status, payload = self.request(
            "POST", "/scoring", {"document": "Договор аренды с условиями оплаты и сроками.", "demo": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["score"], 95)
        self.assertEqual(payload["verdict_color"], "green")
        self.assertIn("weights", payload)

    def test_scoring_without_document_returns_400(self):
        status, payload = self.request("POST", "/scoring", {"demo": True})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)


class SessionTests(ApiTestCase):
    def test_new_session_receives_unpredictable_id(self):
        status, payload = self.request("POST", "/ask", {"question": "Первый вопрос", "demo": True})
        self.assertEqual(status, 200)
        self.assertRegex(payload["session_id"], r"^[a-f0-9]{48}$")

    def test_second_request_in_session_succeeds(self):
        status, first = self.request("POST", "/ask", {"question": "Первый вопрос", "demo": True})
        self.assertEqual(status, 200)
        session_id = first["session_id"]
        status, payload = self.request("POST", "/ask", {
            "question": "А что с неустойкой?", "session_id": session_id, "demo": True
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["session_id"], session_id)

    def test_unknown_session_id_is_rejected(self):
        status, payload = self.request("POST", "/ask", {
            "question": "Чужое дело", "session_id": "case-42", "demo": True
        })
        self.assertEqual(status, 400)
        self.assertIn("session_id", payload["error"])

    def test_invalid_session_id_returns_400(self):
        status, payload = self.request("POST", "/ask", {
            "question": "Вопрос", "session_id": "плохой id!", "demo": True
        })
        self.assertEqual(status, 400)
        self.assertIn("session_id", payload["error"])

    def test_expired_session_cannot_be_reused(self):
        _, first = self.request("POST", "/ask", {"question": "Первый вопрос", "demo": True})
        session_id = first["session_id"]
        self.server.sessions.ttl_seconds = -1
        status, payload = self.request("POST", "/ask", {
            "question": "Продолжение", "session_id": session_id, "demo": True
        })
        self.assertEqual(status, 400)
        self.assertIn("session_id", payload["error"])

    def test_two_new_sessions_are_distinct(self):
        _, first = self.request("POST", "/ask", {"question": "Дело А", "demo": True})
        _, second = self.request("POST", "/ask", {"question": "Дело Б", "demo": True})
        self.assertNotEqual(first["session_id"], second["session_id"])


class SessionStoreTests(unittest.TestCase):
    def test_context_returns_previous_entries(self):
        store = SessionStore()
        store.remember("case-1", "Вопрос об аренде", "Ответ про аренду.")
        self.assertIn("Вопрос об аренде", store.context("case-1"))

    def test_expired_session_is_purged(self):
        store = SessionStore(ttl_seconds=-1)
        store.remember("case-2", "Вопрос", "Ответ")
        self.assertEqual(store.context("case-2"), "")

    def test_entries_are_limited(self):
        store = SessionStore()
        for index in range(15):
            store.remember("case-3", f"Вопрос {index}", f"Ответ {index}")
        self.assertNotIn("Вопрос 0", store.context("case-3"))
        self.assertIn("Вопрос 14", store.context("case-3"))


class TokenAuthTests(ApiTestCase):
    api_token = "test-secret-token"

    def test_ask_requires_token(self):
        status, payload = self.request(
            "POST", "/ask", {"question": "Вопрос", "demo": True}
        )
        self.assertEqual(status, 401)
        self.assertIn("error", payload)

    def test_ask_with_token_succeeds(self):
        status, payload = self.request(
            "POST", "/ask", {"question": "Вопрос", "demo": True}, token="test-secret-token"
        )
        self.assertEqual(status, 200)
        self.assertIn("answer", payload)


if __name__ == "__main__":
    unittest.main()
