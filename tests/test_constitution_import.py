import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_constitution_import", ROOT / "scripts/verify_constitution_import.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ConstitutionAdmissionTests(unittest.TestCase):
    def test_unreviewed_document_cannot_enter_legal_search(self):
        with tempfile.TemporaryDirectory() as d:
            pdf, txt = Path(d)/"source.pdf", Path(d)/"ocr.txt"
            pdf.write_bytes(b"%PDF-1.7\nsynthetic")
            txt.write_text("Synthetic text", encoding="utf-8")
            result = module.verify(pdf, txt)
            self.assertFalse(result["approved_for_legal_search"])
            self.assertFalse(result["checks"]["pdf_sha256"])
            self.assertFalse(result["checks"]["independent_edition_review"])
    def test_missing_pdf_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                module.verify(Path(d)/"missing.pdf", Path(d)/"missing.txt")

if __name__ == "__main__":
    unittest.main()
