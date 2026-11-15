"""Source monitor must never promote an unverified URL to accepted legal text."""
import unittest
from unittest.mock import patch
from scripts.monitor_official_sources import allowed_url, check


class OfficialSourceMonitorTests(unittest.TestCase):
    def test_only_exact_official_https_hosts(self):
        self.assertTrue(allowed_url("https://publication.pravo.gov.ru/Document/View/123"))
        for url in ("http://publication.pravo.gov.ru/doc",
                    "https://publication.pravo.gov.ru.evil.test/doc",
                    "https://evil.test@publication.pravo.gov.ru/doc",
                    "https://publication.pravo.gov.ru:444/doc",
                    "https://127.0.0.1/doc", "file:///etc/passwd",
                    "https://publication.pravo.gov.ru/doc#fragment"):
            with self.subTest(url=url):
                self.assertFalse(allowed_url(url))

    def test_missing_and_untrusted_sources_remain_unverified(self):
        registry = {"acts": [{"id": "a", "title": "A", "source_document_url": None},
                             {"id": "b", "title": "B", "source_document_url": "https://evil.test/"}]}
        with patch("scripts.monitor_official_sources.build_opener",
                   side_effect=AssertionError("network should not be called")):
            report = check(registry, online=False)
        self.assertEqual(report["needs_url"], 1)
        self.assertEqual(report["acts"][1]["status"], "rejected_url")
        self.assertFalse(report["legal_acceptance"])
        self.assertEqual(report["full_text_imported"], 0)
        self.assertTrue(all(not act["verified"] for act in report["acts"]))

    def test_online_monitor_never_downloads_body_or_follows_redirects(self):
        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        class Opener:
            def open(self, request, timeout):
                self_request_method = request.get_method()
                assert self_request_method == "HEAD"
                return Response()
        with patch("scripts.monitor_official_sources.build_opener", return_value=Opener()):
            report = check({"acts": [{"id": "a", "title": "A",
                                     "source_document_url": "https://pravo.gov.ru/doc"}]}, online=True)
        self.assertEqual(report["acts"][0]["status"], "reachable_unverified")
        self.assertFalse(report["acts"][0]["verified"])
        self.assertFalse(report["legal_acceptance"])


if __name__ == "__main__":
    unittest.main()
