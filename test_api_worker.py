"""Offline regression tests for the isolated HTTP worker dispatch."""
import unittest
from unittest.mock import patch

from api_worker import execute


class WorkerDispatchTests(unittest.TestCase):
    def test_local_extract_dispatch(self):
        payload = {"document": "Договор. Цена 123 USD."}
        result = execute({"path": "/extract/local", "payload": payload})
        self.assertEqual(result["status"], 200)
        self.assertIn("matches", result["result"])

    def test_invalid_local_extract_returns_400(self):
        result = execute({"path": "/extract/local", "payload": {"unexpected": "x"}})
        self.assertEqual(result["status"], 400)
        self.assertEqual(result["code"], "invalid_request")

    def test_research_receives_parent_context(self):
        with patch("api.handle_ask", return_value={"answer": "test"}) as handler:
            result = execute({
                "path": "/ask",
                "payload": {"question": "test", "demo": True},
                "output_dir": "/tmp",
                "context": "prior question",
            })
        self.assertEqual(result["status"], 200)
        self.assertEqual(handler.call_args.kwargs["session_context_override"], "prior question")
        self.assertFalse(handler.call_args.kwargs["persist_results"])


if __name__ == "__main__":
    unittest.main()
