"""Regression tests for the preventive copyright hygiene check."""
import unittest

from scripts.check_copyright_hygiene import PROPRIETARY_MARKERS


class CopyrightHygieneTests(unittest.TestCase):
    def test_ownership_notice_is_flagged(self):
        self.assertIsNotNone(PROPRIETARY_MARKERS.search("© КонсультантПлюс"))
        self.assertIsNotNone(PROPRIETARY_MARKERS.search("Гарант — все права защищены"))

    def test_documentation_brand_mentions_are_not_flagged(self):
        self.assertIsNone(PROPRIETARY_MARKERS.search("Не загружайте материалы КонсультантПлюс без разрешения"))
        self.assertIsNone(PROPRIETARY_MARKERS.search("Правила авторских прав для Гарант"))


if __name__ == "__main__":
    unittest.main()
