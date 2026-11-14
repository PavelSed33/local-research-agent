import importlib.util
import unittest
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "scripts/compare_constitution_text.py"
spec = importlib.util.spec_from_file_location("compare_constitution_text", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class ConstitutionComparisonTests(unittest.TestCase):
    def test_equal_text_not_automatically_approved(self):
        sample = "Статья 1\nТекст статьи\nСтатья 2\nДругой текст"
        report = mod.compare(sample, sample)
        self.assertTrue(report["exact_normalized_match"])
        self.assertFalse(report["approved_for_legal_search"])

    def test_detects_missing_and_mismatched_articles(self):
        report = mod.compare("Статья 1\nОшибка", "Статья 1\nВерно\nСтатья 2\nТекст")
        self.assertEqual(report["missing_in_ocr"], ["2"])
        self.assertEqual(report["mismatched_articles"], ["1"])
        self.assertFalse(report["exact_normalized_match"])

    def test_rejects_duplicate_articles(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            mod.compare("Статья 1\nА\nСтатья 1\nБ", "Статья 1\nА")

    def test_empty_text_cannot_pass(self):
        self.assertFalse(mod.compare("", "")["exact_normalized_match"])

if __name__ == "__main__":
    unittest.main()
