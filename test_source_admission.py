"""Tests for strict legal-source admission."""
import unittest

from source_admission import SourceAdmissionError, admit_record, classify_source


class SourceAdmissionTests(unittest.TestCase):
    def test_official_publication_metadata_only(self):
        decision = classify_source("https://publication.pravo.gov.ru/Document/View/123")
        self.assertEqual(decision.category, "official_legal_publication")
        self.assertFalse(decision.approved_for_full_text)

    def test_commercial_and_lookalike_hosts_denied(self):
        for url in (
            "https://www.consultant.ru/document/123",
            "https://garant.ru/doc/123",
            "https://publication.pravo.gov.ru.evil.test/",
            "http://publication.pravo.gov.ru/",
            "https://user:pass@publication.pravo.gov.ru/",
            "https://publication.pravo.gov.ru:444/",
        ):
            with self.subTest(url=url), self.assertRaises(SourceAdmissionError):
                classify_source(url)

    def test_record_rejects_full_text_and_missing_provenance(self):
        valid = {
            "document_id": "test-123",
            "source_url": "https://publication.pravo.gov.ru/Document/View/123",
            "fetched_at": "2026-11-12T12:00:00Z",
        }
        result = admit_record(valid)
        self.assertEqual(result["license_review"], "pending")
        with self.assertRaises(SourceAdmissionError):
            admit_record({**valid, "full_text": "copied legal document"})
        with self.assertRaises(SourceAdmissionError):
            admit_record({**valid, "fetched_at": ""})


if __name__ == "__main__":
    unittest.main()
