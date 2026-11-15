import unittest
from scripts.discover_official_publications import publication_candidates, discover


class OfficialPublicationDiscoveryTests(unittest.TestCase):
    def test_exact_act_number_date_and_official_host(self):
        html = """<a href="/document/0001200607270001">Федеральный закон от 27.07.2006 № 152-ФЗ</a>
<a href="/document/0001200607270002">Федеральный закон от 27.07.2006 № 1152-ФЗ</a>
<a href="https://evil.test/document/0001200607270003">Федеральный закон от 27.07.2006 № 152-ФЗ</a>
<a href="/document/0001200607270004">Федеральный закон от 28.07.2006 № 152-ФЗ</a>"""
        result = publication_candidates(html, number="152-ФЗ", signed_on="2006-07-27")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["publication_number"], "0001200607270001")
        self.assertFalse(result[0]["verified"])

    def test_no_automatic_import_even_with_candidate(self):
        html = '<a href="/document/0001200607270001">Федеральный закон от 27.07.2006 № 152-ФЗ</a>'
        registry = {"acts": [{"id": "pd152", "title": "Персональные данные",
                              "number": "152-ФЗ", "signed_on": "2006-07-27"}]}
        report = discover(registry, fetch=lambda url: html, days=0)
        self.assertEqual(report["candidates_found"], 1)
        self.assertFalse(report["legal_acceptance"])
        self.assertEqual(report["full_text_imported"], 0)
        self.assertFalse(report["results"][0]["verified"])


if __name__ == "__main__":
    unittest.main()
