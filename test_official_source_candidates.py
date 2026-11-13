"""Ensure discovery metadata never masquerades as accepted statutory text."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent


class OfficialSourceCandidateTests(unittest.TestCase):
    def test_candidates_are_metadata_only(self):
        data = json.loads((ROOT / "fixtures/official_source_candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(len(data["acts"]), 15)
        self.assertEqual(len({act["id"] for act in data["acts"]}), 15)
        for act in data["acts"]:
            self.assertTrue(act["source_lookup"].startswith("https://publication.pravo.gov.ru/"))
            self.assertIsNone(act["source_document_url"])
            self.assertFalse(act["full_text_imported"])
            self.assertFalse(act["edition_verified"])
            self.assertFalse(act["legal_reviewed"])
            self.assertNotIn("text", act)
        self.assertFalse(data["candidate_corpus"]["accepted_for_legal_answers"])

    def test_candidates_match_existing_coverage_registry(self):
        candidates = json.loads((ROOT / "fixtures/official_source_candidates.json").read_text(encoding="utf-8"))
        coverage = json.loads((ROOT / "fixtures/legal_coverage.json").read_text(encoding="utf-8"))
        self.assertEqual({a["id"] for a in candidates["acts"]},
                         {a["id"] for a in coverage["acts"]})


if __name__ == "__main__":
    unittest.main()
