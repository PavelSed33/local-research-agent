"""Offline regression tests for official PDF acquisition."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from scripts.fetch_official_publication import fetch


class Response:
    def __init__(self, data, content_type="application/pdf"):
        self.buffer = io.BytesIO(data)
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(data))}
    def geturl(self):
        return "https://publication.pravo.gov.ru/file/pdf?eoNumber=0001202512280026"
    def read(self, size):
        return self.buffer.read(size)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class OfficialPublicationDownloadTests(unittest.TestCase):
    def test_valid_pdf_is_quarantined(self):
        with tempfile.TemporaryDirectory() as temp:
            pdf, metadata = fetch("14fz-ooo", "0001202512280026", temp,
                opener=Mock(open=Mock(return_value=Response(b"%PDF-1.4\nsynthetic test"))))
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
            record = json.loads(metadata.read_text(encoding="utf-8"))
            self.assertFalse(record["admitted_to_agent"])
            self.assertFalse(record["title_verified"])
            self.assertEqual(len(record["sha256"]), 64)
            with self.assertRaises(FileExistsError):
                fetch("14fz-ooo", "0001202512280026", temp,
                    opener=Mock(open=Mock(return_value=Response(b"%PDF-1.4\nsynthetic test"))))

    def test_html_and_invalid_identifiers_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                fetch("../escape", "0001202512280026", temp)
            with self.assertRaises(ValueError):
                fetch("14fz-ooo", "123", temp)
            with self.assertRaises(ValueError):
                fetch("14fz-ooo", "0001202512280026", temp,
                    opener=Mock(open=Mock(return_value=Response(b"<html>error</html>", "text/html"))))
            self.assertEqual(list(Path(temp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
