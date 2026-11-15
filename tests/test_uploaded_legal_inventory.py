import unittest
from scripts.validate_uploaded_legal_inventory import validate


class InventoryValidationTests(unittest.TestCase):
    def setUp(self):
        self.registry = {"acts": [{"id": "a", "title": "Act", "number": "1", "signed_on": "2020-01-01"}]}
        self.inventory = {"candidate_registry_count": 1,
                          "registry_acts": [dict(self.registry["acts"][0], full_text_imported=False, edition_verified=False)],
                          "uploaded_evidence": [{"act": "Act", "file": "a.pdf", "availability": "audit_record_only", "production_approved": False}],
                          "duplicate_candidates": [],
                          "historical_index": {"production_approved": False, "edition_verified": False}}

    def test_valid(self):
        self.assertEqual(validate(self.inventory, self.registry)[0], [])

    def test_rejects_fabricated_import(self):
        self.inventory["registry_acts"][0]["full_text_imported"] = True
        self.assertTrue(validate(self.inventory, self.registry)[0])

    def test_rejects_approval(self):
        self.inventory["uploaded_evidence"][0]["production_approved"] = True
        self.assertTrue(validate(self.inventory, self.registry)[0])

    def test_rejects_missing_registry_act(self):
        self.inventory["registry_acts"] = []
        self.assertTrue(validate(self.inventory, self.registry)[0])

    def test_rejects_untracked_duplicate(self):
        self.inventory["duplicate_candidates"] = [["a.pdf", "missing.pdf"]]
        self.assertTrue(validate(self.inventory, self.registry)[0])


if __name__ == "__main__":
    unittest.main()
