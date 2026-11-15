"""Article-level historical-date checks without unverified statute text.

These checks are intentionally metadata-only. They do not certify a source
or turn secondary chronology into an approved legal corpus.
"""
import unittest
from scripts.legal_article_versions import LegalVersionError, select_article_version


class GkArticle1HistoricalDates(unittest.TestCase):
    """Known amendment boundary from 302-FZ, pending official-text acceptance."""

    def setUp(self):
        self.unapproved_candidate = {
            "article_id": "gk1:article:1",
            "text": None,
            "effective_from": "2013-03-01",
            "effective_until": None,
            "published_on": "2012-12-31",
            "official_publication_url": None,
            "source_sha256": None,
            "verified": False,
        }

    def test_day_before_302_fz_requires_earlier_version(self):
        with self.assertRaises(LegalVersionError):
            select_article_version([self.unapproved_candidate], "2013-02-28", "gk1:article:1")

    def test_effective_date_does_not_bypass_source_review(self):
        with self.assertRaises(LegalVersionError):
            select_article_version([self.unapproved_candidate], "2013-03-01", "gk1:article:1")

    def test_2026_date_does_not_bypass_source_review(self):
        with self.assertRaises(LegalVersionError):
            select_article_version([self.unapproved_candidate], "2026-11-15", "gk1:article:1")

    def test_different_article_not_accepted(self):
        with self.assertRaises(LegalVersionError):
            select_article_version([self.unapproved_candidate], "2026-11-15", "gk1:article:2")


if __name__ == "__main__":
    unittest.main()
