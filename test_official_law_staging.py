"""Security regressions for the official-law review staging path."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.stage_official_law import stage, validate_official_url

URL = "https://publication.pravo.gov.ru/document/0001202506300007"


class OfficialLawStagingTests(unittest.TestCase):
    def test_canonical_official_document_url(self):
        self.assertEqual(validate_official_url(URL), URL)
        for url in (
            "http://publication.pravo.gov.ru/document/0001202506300007",
            "https://publication.pravo.gov.ru.evil.invalid/document/0001202506300007",
            "https://publication.pravo.gov.ru/document/0001202506300007?download=1",
            "https://publication.pravo.gov.ru/document/../../etc/passwd",
            "https://publication.pravo.gov.ru/search",
            "https://user@publication.pravo.gov.ru/document/0001202506300007",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_official_url(url)

    def test_stage_keeps_document_unapproved_and_hashes_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "synthetic.txt"
            source.write_text("FICTIONAL TEST DOCUMENT ONLY", encoding="utf-8")
            target = stage("gk1", URL, source, Path(temp) / "review")
            record = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(record["source_url"], URL)
            self.assertEqual(record["act_id"], "gk1")
            self.assertFalse(record["admitted_to_agent"])
            self.assertFalse(record["edition_verified"])
            self.assertFalse(record["legal_reviewed"])
            self.assertEqual(len(record["source_sha256"]), 64)
            with self.assertRaises(FileExistsError):
                stage("gk1", URL, source, Path(temp) / "review")

    def test_invalid_identifier_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.txt"
            source.write_text("SYNTHETIC", encoding="utf-8")
            with self.assertRaises(ValueError):
                stage("../escape", URL, source, Path(temp) / "review")
            link = Path(temp) / "link.txt"
            link.symlink_to(source)
            with self.assertRaises(ValueError):
                stage("gk1", URL, link, Path(temp) / "review")


if __name__ == "__main__":
    unittest.main()
