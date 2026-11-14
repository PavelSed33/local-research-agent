"""Focused security regression tests for the reconciled core API."""
import tempfile
import unittest
from pathlib import Path

from api import SESSION_MAX_COUNT, SessionStore, create_server, handle_ask
from redline import MAX_DIFF_DOCUMENT_CHARS, diff_documents
from registry import MAX_REGISTRY_FILE_BYTES, build_registry


class CoreGuardrailTests(unittest.TestCase):
    def test_external_binding_requires_token(self):
        with self.assertRaisesRegex(ValueError, "API_TOKEN"):
            create_server("0.0.0.0", 0, Path("."), "")

    def test_loopback_without_token_is_allowed(self):
        server = create_server("127.0.0.1", 0, Path("."))
        try:
            self.assertFalse(server.api_token)
        finally:
            server.server_close()

    def test_session_capacity_evicts_oldest(self):
        store = SessionStore()
        for i in range(SESSION_MAX_COUNT + 3):
            store.remember(f"s{i}", "question", "answer")
        self.assertLessEqual(len(store._sessions), SESSION_MAX_COUNT)
        self.assertNotIn("s0", store._sessions)

    def test_nonboolean_demo_rejected(self):
        with self.assertRaisesRegex(ValueError, "demo"):
            handle_ask({"question": "demo must be boolean", "demo": "false"}, Path("."))

    def test_oversized_diff_rejected_before_matching(self):
        with self.assertRaisesRegex(ValueError, "не должна превышать"):
            diff_documents("x" * (MAX_DIFF_DOCUMENT_CHARS + 1), "other")

    def test_oversized_registry_input_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            big = root / "oversized.txt"
            with big.open("wb") as stream:
                stream.truncate(MAX_REGISTRY_FILE_BYTES + 1)
            rows, extractions, skipped = build_registry(root, demo=True)
            self.assertEqual(rows, [])
            self.assertEqual(extractions, [])
            self.assertTrue(any("больше допустимых" in entry for entry in skipped))


if __name__ == "__main__":
    unittest.main()
