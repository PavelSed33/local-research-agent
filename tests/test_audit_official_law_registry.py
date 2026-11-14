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

 def test_rejects_fake_hash_even_with_flags(self):
  item={"id":"gk4","official_document_url":"https://publication.pravo.gov.ru/document/0001202210060013",
        "official_text_verified":True,"edition_verified":True,"rights_reviewed":True,
        "source_sha256":"not-a-sha","reviewed_by":"reviewer","last_verified_edition":"2026-11-14",
        "approved_for_legal_search":True}
  self.assertFalse(m.audit({"acts":[item]})["safe"])
 def test_rejects_approval_without_explicit_reviews(self):
  item={"id":"gk4","official_document_url":"https://publication.pravo.gov.ru/document/0001202210060013",
        "source_sha256":"a"*64,"reviewed_by":"reviewer","last_verified_edition":"2026-11-14",
        "approved_for_legal_search":True}
  self.assertFalse(m.audit({"acts":[item]})["safe"])
 def test_valid_review_metadata(self):
  item={"id":"gk4","official_document_url":"https://publication.pravo.gov.ru/document/0001202210060013",
        "official_text_verified":True,"edition_verified":True,"rights_reviewed":True,
        "source_sha256":"a"*64,"reviewed_by":"reviewer","last_verified_edition":"2026-11-14",
        "approved_for_legal_search":True}
  self.assertTrue(m.audit({"acts":[item]})["safe"])
 def test_rejects_empty_registry(self):
  self.assertFalse(m.audit({"acts":[]})["safe"])
 def test_rejects_bad_publication_path(self):
  self.assertFalse(m.audit({"acts":[{"id":"a","official_document_url":"https://publication.pravo.gov.ru/search"}]})["safe"])
if __name__=="__main__":unittest.main()
