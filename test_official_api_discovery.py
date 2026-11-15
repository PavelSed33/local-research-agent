import unittest
from urllib.error import URLError

from scripts.discover_official_api import api_candidates


class OfficialApiDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.registry = {"acts": [{"id": "gk1", "number": "51-ФЗ",
                                    "signed_on": "1994-11-30"}]}

    def test_exact_date_and_number_required(self):
        def fetch(url):
            self.assertIn("/api/Documents?", url)
            self.assertIn("Number=51-%D0%A4%D0%97", url)
            return {"items": [
                {"eoNumber": "0001200012010001", "number": "51-ФЗ",
                 "documentDate": "1994-11-30T00:00:00", "title": "ГК РФ"},
                {"eoNumber": "0001200012010002", "number": "52-ФЗ",
                 "documentDate": "1994-11-30", "title": "Wrong number"},
                {"eoNumber": "0001200012010003", "number": "51-ФЗ",
                 "documentDate": "1994-12-01", "title": "Wrong date"},
                {"eoNumber": "https://evil.example/", "number": "51-ФЗ",
                 "documentDate": "1994-11-30", "title": "Untrusted"}]}
        report = api_candidates(self.registry, fetch=fetch)
        self.assertEqual(report["candidates_found"], 1)
        self.assertFalse(report["legal_acceptance"])
        self.assertEqual(report["results"][0]["candidates"][0]["url"],
                         "https://publication.pravo.gov.ru/document/0001200012010001")

    def test_post_2011_acts_take_priority_over_older_statutes(self):
        registry = {"acts": [
            {"id": "old", "number": "51-ФЗ", "signed_on": "1994-11-30"},
            {"id": "recent", "number": "178", "signed_on": "2022-10-27"}]}
        report = api_candidates(registry, fetch=lambda url: {"items": []}, limit=1)
        self.assertEqual(report["results"][0]["id"], "recent")
        self.assertFalse(report["results"][0]["original_predates_electronic_publication"])

    def test_network_circuit_stops_after_three_failures(self):
        calls = []
        def fetch(url):
            calls.append(url)
            raise URLError("network down")
        registry = {"acts": self.registry["acts"] * 10}
        report = api_candidates(registry, fetch=fetch, limit=10)
        self.assertEqual(len(calls), 3)
        self.assertEqual(report["results"][-1]["status"], "network_circuit_open")
        self.assertEqual(report["full_text_imported"], 0)


if __name__ == "__main__":
    unittest.main()
