import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agent import LEGAL_DISCLAIMER
from api import create_server


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
