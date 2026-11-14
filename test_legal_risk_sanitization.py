"""Ensure malformed model risks never become partially trusted legal output."""
import unittest
from unittest.mock import patch
from agent import AgentError
from legal_review import handle_legal_review

DOCUMENT = "Стороны согласовали передачу товара и оплату по договору. Обязательства сторон описаны."


class LegalRiskSanitizationTests(unittest.TestCase):
    def test_invalid_sanitized_risk_fails_closed(self):
        payload = {
            "document": DOCUMENT,
            "demo": True,
            "facts": {"both_businesses": True},
        }
        with patch("legal_review.safe_review_risk", return_value=None):
            with self.assertRaisesRegex(AgentError, "безопасно обработать риск"):
                handle_legal_review(payload)


if __name__ == "__main__":
    unittest.main()
