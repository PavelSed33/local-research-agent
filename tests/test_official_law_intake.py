import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
spec = importlib.util.spec_from_file_location("official_intake", Path(__file__).resolve().parents[1] / "scripts/official_law_intake.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class OfficialIntakeTests(unittest.TestCase):
    def test_canonical_url(self):
        self.assertEqual(module.validate_url("https://publication.pravo.gov.ru/document/0001202210060013"), "0001202210060013")
    def test_rejects_untrusted_hosts_and_credentials(self):
        for url in ("http://publication.pravo.gov.ru/document/0001202210060013",
                    "https://publication.pravo.gov.ru.evil.test/document/0001202210060013",
                    "https://user@publication.pravo.gov.ru/document/0001202210060013",
                    "https://publication.pravo.gov.ru/document/0001202210060013?download=1",
                    "https://publication.pravo.gov.ru/search"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                module.validate_url(url)
    def test_rejects_private_dns(self):
        with patch.object(module.socket, "getaddrinfo", return_value=[(None,None,None,None,("127.0.0.1",443))]):
            with self.assertRaises(ValueError):
                module.validate_public_ips(module.HOST)
    def test_redirect_is_rejected(self):
        with self.assertRaises(ValueError):
            module.NoRedirect().redirect_request(None,None,302,"",{}, "https://example.com")
if __name__ == "__main__":
    unittest.main()
