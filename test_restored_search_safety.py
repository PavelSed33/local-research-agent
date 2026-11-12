"""Safety regressions for the restored live-search engine.

Run on the recovery branch after merging main's source_admission.py.
"""
import os
import unittest
from unittest.mock import patch

from agent import AgentError, TavilySearch, Source


class RestoredSearchSafetyTests(unittest.TestCase):
    def test_commercial_domain_is_rejected_before_search(self):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            with self.assertRaises(AgentError):
                TavilySearch(domains=["www.consultant.ru"])

    def test_unknown_domain_is_rejected_before_search(self):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            with self.assertRaises(AgentError):
                TavilySearch(domains=["example.com"])

    def test_official_domain_is_accepted(self):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            client = TavilySearch(domains=["publication.pravo.gov.ru"])
        self.assertEqual(client.domains, ["publication.pravo.gov.ru"])

    def test_default_court_domains_pass_admission(self):
        from source_admission import classify_source
        for domain in ("kad.arbitr.ru", "vsrf.ru"):
            with self.subTest(domain=domain):
                self.assertEqual(classify_source(f"https://{domain}/").category, "official_court")

    def test_search_filters_unapproved_results(self):
        payload = {"results": [
            {"url": "https://publication.pravo.gov.ru/a", "title": "Official", "content": "Public metadata"},
            {"url": "https://www.consultant.ru/a", "title": "Commercial", "content": "Not allowed"},
            {"url": "https://publication.pravo.gov.ru.attacker.test/a", "title": "Fake", "content": "Not allowed"},
            {"url": "http://pravo.gov.ru/a", "title": "Insecure", "content": "Not allowed"},
        ]}
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            client = TavilySearch(domains=["publication.pravo.gov.ru"])
        with patch("agent.post_json", return_value=payload):
            result = client.search("law", 4)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, "Official")


if __name__ == "__main__":
    unittest.main()
