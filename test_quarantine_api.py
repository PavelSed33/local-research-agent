"""Security regressions for the optional non-authoritative corpus endpoint."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from api import RequestValidationError, handle_corpus_search


class QuarantineApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name) / "gk"
        folder.mkdir()
        (folder / "manifest.json").write_text(json.dumps({
            "status": "quarantine_unverified",
            "legal_review_approved": False,
            "source_claim_verified": False,
            "warning": "Непроверенная редакция",
        }), encoding="utf-8")
        (folder / "articles.jsonl").write_text(json.dumps({
            "id": "GK-395", "part": "I", "article": "395",
            "heading": "Ответственность", "text": "Тестовый текст статьи",
        }, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {"LEGAL_CORPUS_DIR": self.tmp.name}, clear=True):
            with self.assertRaises(RequestValidationError):
                handle_corpus_search({"code": "gk", "query": "395"})

    def test_enabled_search_is_explicitly_unverified(self):
        with patch.dict(os.environ, {
            "ENABLE_QUARANTINE_SEARCH": "true", "LEGAL_CORPUS_DIR": self.tmp.name
        }):
            result = handle_corpus_search({"code": "gk", "query": "395"})
        self.assertFalse(result["verified"])
        self.assertFalse(result["results"][0]["verified"])
        self.assertEqual(result["results"][0]["article"], "395")
        self.assertIn("не прошли", result["disclaimer"])

    def test_reject_invalid_limits_and_unknown_codes(self):
        with patch.dict(os.environ, {
            "ENABLE_QUARANTINE_SEARCH": "true", "LEGAL_CORPUS_DIR": self.tmp.name
        }):
            for limit in (True, 0, 51, "10"):
                with self.subTest(limit=limit), self.assertRaises(ValueError):
                    handle_corpus_search({"code": "gk", "query": "395", "limit": limit})
            with self.assertRaises(ValueError):
                handle_corpus_search({"code": "../gk", "query": "395"})


if __name__ == "__main__":
    unittest.main()
