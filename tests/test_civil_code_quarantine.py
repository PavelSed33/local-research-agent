import importlib.util
import unittest
from pathlib import Path
spec = importlib.util.spec_from_file_location("civil_code_index", Path(__file__).resolve().parents[1] / "scripts/index_civil_code_quarantine.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
class CivilCodeIndexTests(unittest.TestCase):
    def test_extracts_headings_and_blocks_legal_search(self):
        items = mod.build_index("Статья 1. Первая\nТекст\nСтатья 1.1. Вторая\nДругой текст")
        self.assertEqual([i["article"] for i in items], ["1", "1.1"])
        self.assertTrue(all(not i["approved_for_legal_search"] for i in items))
    def test_no_articles(self):
        self.assertEqual(mod.build_index("без заголовков"), [])
if __name__ == "__main__":
    unittest.main()
