"""No-network regression tests for legal portal redirect protections."""
import unittest

from agent import AgentError
from pravo_ips import _RejectRedirects as IPSRejectRedirects
from pravo_portal import _RejectRedirects as PortalRejectRedirects


class RedirectSafetyTests(unittest.TestCase):
    def test_legal_clients_never_follow_redirects(self):
        for handler in (IPSRejectRedirects(), PortalRejectRedirects()):
            with self.subTest(handler=type(handler).__name__):
                with self.assertRaises(AgentError):
                    handler.redirect_request(
                        None, None, 302, "Found", {},
                        "https://www.consultant.ru/",
                    )


if __name__ == "__main__":
    unittest.main()
