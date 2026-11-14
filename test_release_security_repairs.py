"""Regress critical model/API contract and evidence-security repairs."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import AgentError
from api import handle_ask
from review import handle_review, review_contract
from extract import extract_entities, validate_extraction


DOC = "Стороны согласовали оплату и передачу результата по акту. Заказчик оплачивает работы."


class NoApprovalTests(unittest.TestCase):
    def test_demo_review_is_unverified_and_quotes_document(self):
        result = handle_review({"document": DOC, "demo": True})
        self.assertEqual(result["status"], "draft")
        self.assertFalse(result["risks"][0]["verified"])
        self.assertIn(result["risks"][0]["quote"], DOC)
        self.assertEqual(result["risks"][0]["severity"], "low")

    def test_reject_unknown_category_or_empty_recommendation(self):
        class Poison:
            def complete(self, system, user):
                return ('{"summary":"draft","risks":[{"category":"unrecognized",'
                        '"severity":"high","quote":"Стороны согласовали оплату",'
                        '"comment":"issue","recommendation":""}],'
                        '"missing_clauses":[]}')
        with self.assertRaises(AgentError):
            review_contract(Poison(), DOC)

    def test_reject_nonexistent_calendar_date(self):
        data = {"parties": [], "amounts": [], "dates": [
            {"label": "signed", "date": "31.02.2025",
             "quote": "Стороны согласовали оплату и передачу результата"}],
            "penalties": [], "term": None, "jurisdiction": None}
        self.assertTrue(any("календарной датой" in issue for issue in validate_extraction(data, DOC)))

    def test_extraction_does_not_echo_private_identifier(self):
        bad = ("{"+'"parties":[{"name":"Test","inn":"PRIVATE-ID",'
               '"quote":"Стороны согласовали оплату и передачу результата"}],'
               '"amounts":[],"dates":[],"penalties":[],"term":null,"jurisdiction":null}')
        class Poison:
            def complete(self, system, user):
                return bad
        with self.assertRaises(AgentError) as caught:
            extract_entities(Poison(), DOC)
        self.assertNotIn("PRIVATE-ID", str(caught.exception))

    def test_ask_includes_unverified_evidence_without_materials(self):
        with tempfile.TemporaryDirectory() as directory:
            result = handle_ask({"question": "Тест", "demo": True}, Path(directory))
        self.assertEqual(result["evidence"]["status"], "demo")
        self.assertFalse(result["evidence"]["verified"])
        self.assertNotIn("source_materials", result)

    def test_source_materials_opt_in_are_hashed_and_unverified(self):
        with tempfile.TemporaryDirectory() as directory:
            result = handle_ask({"question": "Тест", "demo": True,
                                 "include_source_materials": True}, Path(directory))
        self.assertEqual(len(result["sources"]), len(result["source_materials"]))
        for index, source in enumerate(result["source_materials"], 1):
            self.assertEqual(source["id"], index)
            self.assertFalse(source["verified"])
            self.assertFalse(source["period_supported"])
            self.assertEqual(source["sha256"], hashlib.sha256(source["text"].encode()).hexdigest())
            self.assertNotIn("query", source)


if __name__ == "__main__":
    unittest.main()
