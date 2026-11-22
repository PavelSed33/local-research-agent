"""Regression tests for amendment marker comparisons without legal status promotion."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.check_amendment_markers import check_markers

class AmendmentMarkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name) / "uk"
        folder.mkdir()
        (folder / "manifest.json").write_text(json.dumps({
            "status": "quarantine_unverified"
        }), encoding="utf-8")
        (folder / "articles.jsonl").write_text(json.dumps({
            "article": "321.1", "text": "Новая формулировка: месте лишения свободы."
        }, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_markers_do_not_promote_verification(self):
        report=check_markers(self.temp.name, "uk", "321.1",
                             ["месте лишения свободы"],
                             ["учреждении уголовно-исполнительной системы"])
        self.assertTrue(report["checks_passed"])
        self.assertFalse(report["edition_verified"])
        self.assertFalse(report["source_claim_verified"])

    def test_missing_required_phrase_fails(self):
        report=check_markers(self.temp.name, "uk", "321.1", ["несуществующий фрагмент"])
        self.assertFalse(report["checks_passed"])

    def test_non_quarantine_rejected(self):
        folder=Path(self.temp.name)/"uk"
        (folder/"manifest.json").write_text(json.dumps({"status": "verified"}),encoding="utf-8")
        with self.assertRaises(ValueError):
            check_markers(self.temp.name, "uk", "321.1", ["месте лишения свободы"])

if __name__ == "__main__":
    unittest.main()
