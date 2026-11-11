"""Regression tests for bounded IPS revision history retrieval."""
import unittest
from unittest.mock import patch

from pravo_ips import PravoIpsBank


class PravoIpsHistoryTests(unittest.TestCase):
    def test_current_text_is_compared_with_historical_revision(self):
        bank = PravoIpsBank()
        current = "Редакция от 01.01.2024 Статья 5. Новая редакция нормы."
        historical = "Редакция от 01.01.2020 Статья 5. Старая редакция нормы."

        def fake_page(document_id, revision_id=""):
            self.assertEqual(document_id, "123456")
            if not revision_id:
                return current, [
                    ("/proxy/ips/?docbody=&nd=123456&rdk=987", "Редакция от 01.01.2020")
                ]
            self.assertEqual(revision_id, "987")
            return historical, []

        with patch.object(bank, "_page", side_effect=fake_page):
            history = bank.get_history("123456", as_of="2024-02-01")

        self.assertEqual(len(history["snapshots"]), 2)
        self.assertEqual(history["selected_snapshot"]["edition_date"], "2024-01-01")
        self.assertEqual(history["selected_snapshot"]["revision_id"], "")
        audit = history["revision_chain_audit"]
        self.assertEqual(audit["change_count"], 1)
        self.assertEqual(audit["changed_provisions"][0]["provision_id"], "Статья 5")
        self.assertEqual(audit["changed_provisions"][0]["from_snapshot_date"], "2020-01-01")
        self.assertEqual(audit["changed_provisions"][0]["to_snapshot_date"], "2024-01-01")
        self.assertFalse(audit["coverage_verified"])
        self.assertFalse(history["verified"])

    def test_identical_current_and_revision_snapshot_is_not_double_counted(self):
        bank = PravoIpsBank()
        same_text = "Редакция от 01.01.2024 Статья 5. Текст нормы."

        def fake_page(document_id, revision_id=""):
            if not revision_id:
                return same_text, [
                    ("/proxy/ips/?docbody=&nd=123456&rdk=987", "Текущая редакция")
                ]
            return same_text, []

        with patch.object(bank, "_page", side_effect=fake_page):
            history = bank.get_history("123456")

        self.assertEqual(len(history["snapshots"]), 1)
        self.assertEqual(history["revision_chain_audit"]["change_count"], 0)
        self.assertFalse(history["revision_chain_audit"]["coverage_verified"])


if __name__ == "__main__":
    unittest.main()
