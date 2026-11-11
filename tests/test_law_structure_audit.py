"""Offline tests for conservative structural legal audit."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("audit_law_structure",ROOT/"scripts/audit_law_structure.py")
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class StructureAuditTests(unittest.TestCase):
    def test_extract(self):
        result=mod.extract("Часть первая\nГлава 2. Общие положения\nСтатья 1. Начало\nСтатья 2. Продолжение")
        self.assertEqual(result["articles"],["1","2"])
        self.assertEqual(result["chapters"],["2"])
        self.assertEqual(result["parts"],["первая"])
    def test_missing_article_per_edition(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/"data.sqlite"
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE documents(title TEXT,body TEXT,verified INTEGER)")
                con.executemany("INSERT INTO documents VALUES(?,?,?)",[
                    ("Кодекс X","Глава 1.\nСтатья 1. Текст",0),
                    ("Кодекс X","Глава 1.\nСтатья 2. Текст",0)])
            ref=Path(temp)/"reference.json"
            ref.write_text(json.dumps([{"title":"Кодекс X","articles":["1","2"],"chapters":["1"]}]),encoding="utf-8")
            result=mod.audit(db,ref)["results"][0]
            self.assertEqual(result["status"],"gaps_detected")
            self.assertEqual(len(result["missing"]["articles"]),1)
if __name__=="__main__":unittest.main()
