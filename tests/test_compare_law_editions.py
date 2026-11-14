import importlib.util
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location("legal_diff",Path(__file__).resolve().parents[1]/"scripts/compare_law_editions.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class LawEditionDiffTests(unittest.TestCase):
 def test_detects_changes_and_new_article(self):
  a="Статья 1. Заголовок\nСтарый текст\nСтатья 2. Другая\nБез изменений\n"
  b="Статья 1. Заголовок\nНовый текст\nСтатья 2. Другая\nБез изменений\nСтатья 3. Новая\nТекст\n"
  r=m.compare(a,b)
  self.assertEqual(r["changed"],["1"]);self.assertEqual(r["added"],["3"])
  self.assertFalse(r["approved_for_legal_search"])
 def test_whitespace_normalized(self):
  r=m.compare("Статья 1. А\nТекст", "Статья 1. А   Текст")
  self.assertEqual(r["changed"],[])
 def test_removed_article(self):
  r=m.compare("Статья 1. А\nт\nСтатья 2. Б\nт", "Статья 1. А\nт")
  self.assertEqual(r["removed"],["2"])
 def test_duplicate_numbers_flagged(self):
  r=m.compare("", "Статья 1. А\nт\nСтатья 1. Б\nт")
  self.assertEqual(r["duplicate_article_numbers_new"],["1"])
if __name__=="__main__":unittest.main()
