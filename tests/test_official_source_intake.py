"""Regression tests for fail-closed official source intake."""
import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_official_source.py"
spec = importlib.util.spec_from_file_location("check_official_source", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OfficialSourceIntakeTests(unittest.TestCase):
    def proposal(self, **changes):
        data = {
            "url": "https://publication.pravo.gov.ru/Document/View/0001202611130001",
            "document_id": "0001202611130001",
            "captured_on": "2026-11-13",
            "source_kind": "official_publication",
            "rights_reviewed": True,
        }
        data.update(changes)
        return data

    def test_approved_metadata_does_not_import_text(self):
        result = module.validate_proposal(self.proposal())
        self.assertEqual(result["status"], "metadata_eligible_only")
        self.assertFalse(result["text_imported"])
        self.assertFalse(result["legal_edition_verified"])
        self.assertFalse(result["commercial_use_allowed"])

    def test_rejects_unreviewed_rights(self):
        with self.assertRaises(ValueError):
            module.validate_proposal(self.proposal(rights_reviewed=False))

    def test_rejects_nonofficial_host(self):
        with self.assertRaises(ValueError):
            module.validate_proposal(self.proposal(url="https://example.org/law"))

    def test_rejects_host_suffix_trick(self):
        with self.assertRaises(ValueError):
            module.validate_proposal(self.proposal(url="https://publication.pravo.gov.ru.example.org/law"))

    def test_rejects_query_and_fragment(self):
        for url in ("https://publication.pravo.gov.ru/law?q=1",
                    "https://publication.pravo.gov.ru/law#fragment"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                module.validate_proposal(self.proposal(url=url))

    def test_rejects_userinfo_and_port(self):
        for url in ("https://user@publication.pravo.gov.ru/law",
                    "https://publication.pravo.gov.ru:443/law"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                module.validate_proposal(self.proposal(url=url))

    def test_rejects_invalid_date(self):
        with self.assertRaises(ValueError):
            module.validate_proposal(self.proposal(captured_on="2026-13-99"))

    def test_rejects_future_capture_date(self):
        with self.assertRaises(ValueError):
            module.validate_proposal(self.proposal(captured_on="2099-01-01"))

    def test_rejects_backslash_and_control_characters(self):
        for url in (
            r"https://publication.pravo.gov.ru\@evil.example/law",
            r"https://publication.pravo.gov.ru/law\redirect",
            "https://publication.pravo.gov.ru/law\\nheader",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                module.validate_proposal(self.proposal(url=url))

    def test_rejects_extra_fields(self):
        with self.assertRaises(ValueError):
            module.validate_proposal(self.proposal(text="unverified statute content"))


if __name__ == "__main__":
    unittest.main()
