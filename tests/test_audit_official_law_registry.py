import importlib.util
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location("audit_registry",Path(__file__).resolve().parents[1]/"scripts/audit_official_law_registry.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class RegistryAuditTests(unittest.TestCase):
 def test_pending_unverified_is_safe_but_not_approved(self):
  r=m.audit({"acts":[{"id":"gk4","approved_for_legal_search":False}]})
  self.assertTrue(r["safe"]);self.assertEqual(r["pending"],["gk4"])
 def test_approval_requires_evidence(self):
  r=m.audit({"acts":[{"id":"gk4","approved_for_legal_search":True}]})
  self.assertFalse(r["safe"])
 def test_rejects_duplicate(self):
  r=m.audit({"acts":[{"id":"a"},{"id":"a"}]})
  self.assertFalse(r["safe"])
 def test_rejects_external_source(self):
  r=m.audit({"acts":[{"id":"a","official_document_url":"https://example.com/x"}]})
  self.assertFalse(r["safe"])
if __name__=="__main__":unittest.main()
