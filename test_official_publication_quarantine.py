import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.quarantine_official_publications import quarantine


class QuarantineTests(unittest.TestCase):
    def test_untrusted_candidate_never_downloaded(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("scripts.quarantine_official_publications.build_opener",
                       side_effect=AssertionError("network should not be used")):
                report = quarantine({"results": [{"id": "a", "candidates": [
                    {"url": "https://evil.test/document/0001200607270001"}]}]},
                    Path(temp))
            self.assertEqual(report["downloaded"], 0)
            self.assertFalse(report["legal_acceptance"])
            self.assertEqual(report["corpus_imported"], 0)

    def test_official_document_is_quarantined_not_accepted(self):
        class Response:
            status = 200
            headers = {"Content-Type": "text/html"}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, n):
                return b"<!doctype html><html>unverified</html>"
        class Opener:
            def open(self, request, timeout):
                assert request.full_url == "https://publication.pravo.gov.ru/document/0001200607270001"
                return Response()
        with tempfile.TemporaryDirectory() as temp:
            report = quarantine({"results": [{"id": "a", "candidates": [
                {"url": "https://publication.pravo.gov.ru/document/0001200607270001"}]}]},
                Path(temp), opener=Opener())
            self.assertEqual(report["downloaded"], 1)
            self.assertFalse(report["items"][0]["accepted"])
            self.assertFalse(report["legal_acceptance"])
            self.assertEqual(report["corpus_imported"], 0)
            self.assertTrue((Path(temp) / report["items"][0]["filename"]).is_file())


if __name__ == "__main__":
    unittest.main()
