"""Offline regression tests for legal corpus provenance and edition boundaries."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
audit=load("legal_audit","scripts/audit_external_law_corpus.py")
gate=load("legal_gate","scripts/check_external_corpus_manifest.py")

class CorpusSafetyTests(unittest.TestCase):
    def test_blocked_sources(self):
        base={"source_url":"https://publication.pravo.gov.ru/document/0001202411230024",
              "license":"official-source-review-pending","provenance":["publication.pravo.gov.ru"],
              "sha256":"a"*64,"approved_for_import":True}
        self.assertIsNone(gate.check(base))
        for source in ("https://example.org/RusLawOD/part1","https://irlspbru/ruslawod"):
            self.assertIn("blocked",gate.check(dict(base,source_url=source)))
        self.assertIsNotNone(gate.check(dict(base,approved_for_import=False)))
        self.assertIsNotNone(gate.check(dict(base,sha256="wrong")))

    def test_effective_date_half_open_and_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            db=folder/"legal.sqlite"
            with sqlite3.connect(db) as con:
                con.execute("""CREATE TABLE documents(
                    source_id TEXT,title TEXT,document_number TEXT,content_sha256 TEXT,
                    effective_from TEXT,effective_to TEXT,verified INTEGER,source_url TEXT)""")
                con.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",[
                    ("official","Code A","1","a","2020-01-01","2022-01-01",1,"https://publication.pravo.gov.ru"),
                    ("official","Code A","1","b","2022-01-01",None,1,"https://publication.pravo.gov.ru"),
                    ("unknown","Code B","2","c",None,None,0,None)])
            inv=folder/"inventory.json"
            inv.write_text(json.dumps([{"title":"Code A"},{"title":"Code B"},{"title":"Code C"}]),encoding="utf-8")
            r=audit.assess(db,inv,"2022-01-01")
            self.assertEqual([x["status"] for x in r["documents"]],
                             ["verified_for_date","present_unverified","missing"])
            self.assertEqual(r["documents"][0]["verified_candidates"],1)
            self.assertEqual(audit.assess(db,inv,"2019-01-01")["documents"][0]["status"],"present_unverified")

if __name__=="__main__":
    unittest.main()
