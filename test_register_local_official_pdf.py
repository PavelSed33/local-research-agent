import tempfile
from pathlib import Path
import unittest

from scripts.register_local_official_pdf import register


class LocalOfficialPdfTests(unittest.TestCase):
    def test_registers_hash_without_accepting_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "official.pdf"
            path.write_bytes(b"%PDF-1.4\nlocal fixture")
            result = register(path, act_id="esign63",
                              official_url="https://publication.pravo.gov.ru/document/0001200000000001")
            self.assertEqual(result["status"], "local_pdf_unverified")
            self.assertEqual(len(result["sha256"]), 64)
            self.assertFalse(result["accepted"])
            self.assertEqual(result["corpus_imported"], 0)

    def test_rejects_unapproved_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "official.pdf"
            path.write_bytes(b"%PDF-1.4\nfixture")
            with self.assertRaises(ValueError):
                register(path, act_id="esign63", official_url="https://evil.example/document/123")

    def test_rejects_official_homepage_as_document_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "official.pdf"
            path.write_bytes(b"%PDF-1.4\nfixture")
            with self.assertRaises(ValueError):
                register(path, act_id="esign63", official_url="https://pravo.gov.ru/")

    def test_rejects_non_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fake.pdf"
            path.write_bytes(b"<html>not a PDF</html>")
            with self.assertRaises(ValueError):
                register(path, act_id="esign63",
                         official_url="https://publication.pravo.gov.ru/document/0001200000000001")


if __name__ == "__main__":
    unittest.main()
