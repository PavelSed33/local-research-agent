"""Regression tests: statutory corpus must never accept third-party legal database URLs."""
import unittest

from statute_corpus import _url


class StatutorySourceHostTests(unittest.TestCase):
    def test_official_publication_host_allowed(self):
        self.assertEqual(
            _url("https://publication.pravo.gov.ru/Document/View/0001202611130001"),
            "https://publication.pravo.gov.ru/Document/View/0001202611130001",
        )

    def test_consultant_hosts_denied(self):
        for host in ("consultant.ru", "www.consultant.ru", "sub.consultant.ru"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                _url("https://" + host + "/document/")

    def test_official_host_suffix_spoofing_denied(self):
        with self.assertRaises(ValueError):
            _url("https://publication.pravo.gov.ru.evil.example/document")

    def test_url_credentials_and_query_denied(self):
        for url in (
            "https://user@publication.pravo.gov.ru/document",
            "https://publication.pravo.gov.ru/document?q=1",
            "https://publication.pravo.gov.ru/document#fragment",
            "http://publication.pravo.gov.ru/document",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _url(url)


if __name__ == "__main__":
    unittest.main()
