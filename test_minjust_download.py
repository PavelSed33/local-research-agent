import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts.fetch_minjust_document import fetch, validate_url

class Response:
    def __init__(self, url, data=b"%PDF-1.4\\nmock"):
        self.url, self.data = url, data
        self.headers = {"Content-Type": "application/pdf", "Content-Length": str(len(data))}
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def geturl(self): return self.url
    def read(self, n): return self.data[:n]

class MinjustDownloadTests(unittest.TestCase):
    def test_url_allowlist(self):
        for url in ("http://pravo.minjust.ru/a.pdf", "https://evil.example/a.pdf",
                    "https://pravo.minjust.ru.evil.example/a.pdf",
                    "https://user@pravo.minjust.ru/a.pdf",
                    "https://pravo.minjust.ru:444/a.pdf"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        self.assertEqual(validate_url("https://pravo.minjust.ru/a.pdf"),
                         "https://pravo.minjust.ru/a.pdf")

    def test_quarantine_never_promotes(self):
        url = "https://pravo.minjust.ru/document.pdf"
        opener = Mock()
        opener.open.return_value = Response(url)
        with tempfile.TemporaryDirectory() as tmp:
            path = fetch("test-law", url, tmp, opener=opener)
            self.assertTrue(path.exists())
            metadata = path.with_suffix(".json").read_text(encoding="utf-8")
            self.assertIn('"full_text_imported": false', metadata)
            self.assertIn('"legal_reviewed": false', metadata)
            self.assertIn('"commercial_use_allowed": false', metadata)
            with self.assertRaises(FileExistsError):
                fetch("test-law", url, tmp, opener=opener)

    def test_html_rejected(self):
        url = "https://pravo.minjust.ru/a"
        response = Response(url, b"<html>")
        opener = Mock()
        opener.open.return_value = response
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                fetch("test-law", url, tmp, opener=opener)
            self.assertEqual(list(Path(tmp).iterdir()), [])

if __name__ == "__main__":
    unittest.main()
