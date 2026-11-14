"""Regression tests for conservative official-source intake policy."""
import unittest

from source_admission import SourceAdmissionError, admit_record, classify_source


class SourceAdmissionSafetyTests(unittest.TestCase):
    def test_official_metadata_only(self):
        record = admit_record({
            "document_id": "law-1",
            "source_url": "https://publication.pravo.gov.ru/Document/View/1",
            "fetched_at": "2026-11-14T00:00:00Z",
        })
        self.assertEqual(record["license_review"], "pending")
        self.assertEqual(record["storage_policy"], "metadata_and_short_excerpts")

    def test_commercial_and_unknown_sources_rejected(self):
        for url in (
            "https://www.consultant.ru/document/1",
            "https://garant.ru/document/1",
            "https://example.org/document/1",
            "http://publication.pravo.gov.ru/Document/View/1",
            "https://publication.pravo.gov.ru.evil.example/document",
        ):
            with self.subTest(url=url), self.assertRaises(SourceAdmissionError):
                classify_source(url)

    def test_full_document_fields_rejected(self):
        base = {
            "document_id": "law-1",
            "source_url": "https://publication.pravo.gov.ru/Document/View/1",
            "fetched_at": "2026-11-14T00:00:00Z",
        }
        for field in ("full_text", "raw_content", "pdf_base64", "content",
                      "text", "body", "html", "markdown", "pdf_bytes", "attachment"):
            with self.subTest(field=field), self.assertRaises(SourceAdmissionError):
                admit_record({**base, field: "unapproved content"})


if __name__ == "__main__":
    unittest.main()
