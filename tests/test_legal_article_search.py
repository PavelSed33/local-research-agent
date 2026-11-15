import json
import unittest
from pathlib import Path
from scripts.legal_article_search import search_verified_article


class GkArticleSearchTests(unittest.TestCase):
    def test_real_candidate_stays_unverified(self):
        candidate = json.loads(Path("fixtures/article_provenance_candidates_gk1.json").read_text(encoding="utf-8"))
        result = search_verified_article([candidate], article_id="gk1:article:1", as_of="2026-11-15")
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["text"])
        self.assertIsNone(result["source_url"])

    def test_verified_fixture_has_citation(self):
        fake = dict(article_id="test:1", text="Synthetic fixture only",
                    effective_from="2020-01-01", effective_until=None,
                    published_on="2019-12-31",
                    official_publication_url="https://publication.pravo.gov.ru/Document/View/test-fixture",
                    source_sha256="a" * 64, verified=True, reviewer_accepted=True,
                    amendment_history_checked=True)
        result = search_verified_article([fake], article_id="test:1", as_of="2026-11-15")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["source_url"], fake["official_publication_url"])

    def test_missing_review_blocks_citation(self):
        fake = dict(article_id="test:1", text="Synthetic fixture only",
                    effective_from="2020-01-01", effective_until=None,
                    published_on="2019-12-31",
                    official_publication_url="https://publication.pravo.gov.ru/Document/View/test-fixture",
                    source_sha256="a" * 64, verified=True, reviewer_accepted=False,
                    amendment_history_checked=True)
        result = search_verified_article([fake], article_id="test:1", as_of="2026-11-15")
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["text"])

    def test_empty_server_corpus_fails_closed(self):
        result = search_verified_article([], article_id="gk1:article:1", as_of="2026-11-15")
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["text"])

    def test_unverified_record_never_leaks_text(self):
        record = dict(article_id="gk1:article:1", text="unapproved text",
                      effective_from="2013-03-01", effective_until=None,
                      published_on="2012-12-31", official_publication_url=None,
                      source_sha256=None, verified=False, reviewer_accepted=False,
                      amendment_history_checked=False)
        result = search_verified_article([record], article_id="gk1:article:1", as_of="2026-11-15")
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["text"])
        self.assertIsNone(result["source_url"])


if __name__ == "__main__":
    unittest.main()
