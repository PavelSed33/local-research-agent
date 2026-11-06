import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agent import LEGAL_DISCLAIMER
from api import SessionStore, create_server


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


class HealthEndpointTests(ApiTestCase):
    def test_health_returns_status(self):
        status, payload = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["demo_available"])
        self.assertIn("llm_configured", payload)
        self.assertIn("tavily_configured", payload)

    def test_unknown_path_returns_404(self):
        status, payload = self.request("GET", "/unknown")
        self.assertEqual(status, 404)
        self.assertIn("error", payload)

    def test_openapi_spec_is_served(self):
        status, payload = self.request("GET", "/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(payload["openapi"], "3.0.3")
        for path in ("/health", "/ask", "/review", "/extract", "/diff"):
            self.assertIn(path, payload["paths"])


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


class SessionTests(ApiTestCase):
    def test_ask_with_session_id_returns_it(self):
        status, payload = self.request(
            "POST", "/ask", {"question": "Первый вопрос", "session_id": "дело-1".encode().hex()[:20], "demo": True}
        )
        self.assertEqual(status, 200)
        self.assertIsNotNone(payload["session_id"])

    def test_second_request_in_session_succeeds(self):
        session_id = "case-42"
        status, _ = self.request("POST", "/ask", {"question": "Первый вопрос", "session_id": session_id, "demo": True})
        self.assertEqual(status, 200)
        status, payload = self.request("POST", "/ask", {"question": "А что с неустойкой?", "session_id": session_id, "demo": True})
        self.assertEqual(status, 200)
        self.assertEqual(payload["session_id"], session_id)

    def test_invalid_session_id_returns_400(self):
        status, payload = self.request(
            "POST", "/ask", {"question": "Вопрос", "session_id": "плохой id!", "demo": True}
        )
        self.assertEqual(status, 400)
        self.assertIn("session_id", payload["error"])

    def test_ask_without_session_id_returns_null(self):
        status, payload = self.request("POST", "/ask", {"question": "Вопрос", "demo": True})
        self.assertEqual(status, 200)
        self.assertIsNone(payload["session_id"])


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
