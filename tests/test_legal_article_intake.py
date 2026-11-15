import hashlib
import unittest
from scripts.legal_article_intake import accept_article_record
from scripts.legal_article_versions import LegalVersionError


class ArticleIntakeTests(unittest.TestCase):
    def setUp(self):
        self.source = b"TEST FIXTURE - NOT A REAL LAW"
        self.record = dict(article_id="test:1", text="fixture only", effective_from="2026-01-01",
                           effective_until=None, published_on="2025-12-30",
                           official_publication_url="https://publication.pravo.gov.ru/Document/View/example",
                           source_sha256=hashlib.sha256(self.source).hexdigest(), verified=True,
                           reviewer_accepted=True, amendment_history_checked=True,
                           production_approved=True,
                           article_text_sha256=hashlib.sha256(b"fixture only").hexdigest())

    def test_fixture_gate(self):
        self.assertEqual(accept_article_record(self.record, self.source, as_of="2026-11-15")["article_id"], "test:1")

    def test_rejects_missing_production_approval(self):
        self.record["production_approved"] = False
        with self.assertRaises(LegalVersionError):
            accept_article_record(self.record, self.source, as_of="2026-11-15")

    def test_rejects_tampered_article_text(self):
        self.record["text"] = "tampered"
        with self.assertRaises(LegalVersionError):
            accept_article_record(self.record, self.source, as_of="2026-11-15")

    def test_rejects_invalid_record(self):
        with self.assertRaises(LegalVersionError):
            accept_article_record(None, self.source, as_of="2026-11-15")

    def test_rejects_modified_source(self):
        with self.assertRaises(LegalVersionError):
            accept_article_record(self.record, self.source + b"x", as_of="2026-11-15")

    def test_rejects_unreviewed(self):
        self.record["reviewer_accepted"] = False
        with self.assertRaises(LegalVersionError):
            accept_article_record(self.record, self.source, as_of="2026-11-15")

    def test_rejects_missing_amendment_history(self):
        self.record["amendment_history_checked"] = False
        with self.assertRaises(LegalVersionError):
            accept_article_record(self.record, self.source, as_of="2026-11-15")

    def test_rejects_future_effective_date(self):
        with self.assertRaises(LegalVersionError):
            accept_article_record(self.record, self.source, as_of="2025-11-15")


if __name__ == "__main__":
    unittest.main()
