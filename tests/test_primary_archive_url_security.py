"""Archive URL security regression tests; no licensed document fixtures needed."""
import unittest
from primary_sources import _url


class PrimaryArchiveUrlTests(unittest.TestCase):
    def test_official_https_source_allowed(self):
        self.assertIsNone(_url("https://www.vsrf.ru/documents/own/1234/"))

    def test_rejects_nonofficial_sources_and_url_tricks(self):
        invalid = (
            "https://www.consultant.ru/document/123",
            "https://vsrf.ru.evil.example/doc",
            "http://www.vsrf.ru/doc",
            "https://user@www.vsrf.ru/doc",
            "https://:pass@www.vsrf.ru/doc",
            "https://www.vsrf.ru:443/doc",
            "https://www.vsrf.ru/doc?next=evil",
            "https://www.vsrf.ru/doc#part",
            r"https://www.vsrf.ru\\@evil.example/doc",
            "https://www.vsrf.ru/doc\nInjected: true",
            "https://www.vsrf.ru",
        )
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                _url(url)


if __name__ == "__main__":
    unittest.main()
