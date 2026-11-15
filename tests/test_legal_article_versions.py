import unittest
from scripts.legal_article_versions import LegalVersionError, select_article_version


class ArticleVersionTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(article_id="gk:10", text="verified historical text",
                        effective_from="2026-06-10", effective_until="2027-01-01",
                        published_on="2026-06-10",
                        official_publication_url="https://publication.pravo.gov.ru/Document/View/example",
                        source_sha256="a" * 64, verified=True)
        self.new = dict(self.old, text="verified future text",
                        effective_from="2027-01-01", effective_until=None,
                        published_on="2026-08-04", source_sha256="b" * 64)

    def test_before_change(self):
        self.assertIs(select_article_version([self.old, self.new], "2026-11-15", "gk:10"), self.old)

    def test_on_change_date(self):
        self.assertIs(select_article_version([self.old, self.new], "2027-01-01", "gk:10"), self.new)

    def test_no_early_future(self):
        with self.assertRaises(LegalVersionError):
            select_article_version([self.new], "2026-11-15", "gk:10")

    def test_missing_history_fails_closed(self):
        with self.assertRaises(LegalVersionError):
            select_article_version([self.old], "2025-01-01", "gk:10")

    def test_overlap_fails(self):
        with self.assertRaises(LegalVersionError):
            select_article_version([dict(self.old, effective_until=None), self.new], "2027-01-01", "gk:10")

    def test_unverified_fails(self):
        with self.assertRaises(LegalVersionError):
            select_article_version([dict(self.old, verified=False)], "2026-11-15", "gk:10")

    def test_publisher_url_fails(self):
        with self.assertRaises(LegalVersionError):
            select_article_version([dict(self.old, official_publication_url="https://consultant.ru/example")], "2026-11-15", "gk:10")

    def test_missing_digest_fails(self):
        with self.assertRaises(LegalVersionError):
            select_article_version([dict(self.old, source_sha256=None)], "2026-11-15", "gk:10")

    def test_wrong_article_fails(self):
        with self.assertRaises(LegalVersionError):
            select_article_version([self.old], "2026-11-15", "gk:11")


if __name__ == "__main__":
    unittest.main()
