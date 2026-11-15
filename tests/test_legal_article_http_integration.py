"""Integration-level tests of the actual HTTP route dispatch, without external providers."""
import unittest
from unittest.mock import Mock
from api import LegalApiHandler


class ArticleRouteIntegrationTests(unittest.TestCase):
    def invoke(self, payload=None, authorized=True):
        handler = Mock()
        handler.path = "/legal/article"
        handler._authorized.return_value = authorized
        handler._read_payload.return_value = payload or {"article_id": "gk1:article:1", "as_of": "2026-11-15"}
        LegalApiHandler.do_POST(handler)
        return handler

    def test_empty_production_corpus_never_returns_legal_text(self):
        handler = self.invoke()
        handler._send_json.assert_called_once()
        status, body = handler._send_json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "unverified")
        self.assertIsNone(body["text"])

    def test_invalid_calendar_date_is_bad_request(self):
        handler = self.invoke({"article_id": "gk1:article:1", "as_of": "2026-02-30"})
        status, body = handler._send_json.call_args.args
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_unauthorized_request_does_not_read_corpus(self):
        handler = self.invoke(authorized=False)
        self.assertEqual(handler._send_json.call_args.args[0], 401)
        handler._read_payload.assert_not_called()

    def test_client_supplied_article_text_is_ignored(self):
        handler = self.invoke({"article_id": "gk1:article:1", "as_of": "2026-11-15",
                               "text": "malicious replacement", "verified": True,
                               "production_approved": True})
        status, body = handler._send_json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "unverified")
        self.assertIsNone(body["text"])


if __name__ == "__main__":
    unittest.main()
