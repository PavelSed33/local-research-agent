"""Tests for statute corpus module."""
import unittest
import json
import os
import tempfile
import shutil

from statute_corpus import StatuteCorpus, get_statute_text, search_statutes


class StatuteCorpusTests(unittest.TestCase):
    """Test statute corpus functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.base_dir = 'fixtures/statutes'
        self.corpus = StatuteCorpus(base_dir=self.base_dir)

    def test_list_statutes(self):
        """Test listing all statutes."""
        statutes = self.corpus.list_statutes()
        self.assertGreater(len(statutes), 0)
        self.assertIn('gk_part1', statutes)
        self.assertIn('gk_part2', statutes)
        self.assertIn('tk_rf', statutes)

    def test_get_statute(self):
        """Test getting a specific statute."""
        statute = self.corpus.get_statute('gk_part1')
        self.assertIsNotNone(statute)
        self.assertEqual(statute['statute_id'], 'gk_part1')
        self.assertIn('articles', statute)

    def test_get_article(self):
        """Test getting a specific article."""
        article = self.corpus.get_article('gk_part1', '432')
        self.assertIsNotNone(article)
        self.assertEqual(article['number'], '432')
        self.assertIn('договор', article['text'].lower())

    def test_get_statute_text(self):
        """Test convenience function get_statute_text."""
        text = get_statute_text('gk_part1', '432')
        self.assertIsNotNone(text)
        self.assertIn('договор', text.lower())

    def test_search_statutes(self):
        """Test searching in statutes."""
        results = search_statutes('договор', limit=5)
        self.assertGreater(len(results), 0)
        self.assertIn('statute_id', results[0])
        self.assertIn('article', results[0])

    def test_search_no_results(self):
        """Test search with no results."""
        results = search_statutes('несуществующийтермин12345', limit=5)
        self.assertEqual(len(results), 0)

    def test_search_relevance(self):
        """Test search relevance sorting."""
        results = search_statutes('договор', statute_ids=['gk_part1', 'gk_part2'], limit=10)
        if len(results) > 1:
            relevances = [r['relevance'] for r in results]
            self.assertEqual(relevances, sorted(relevances, reverse=True))

    def test_metadata_integrity(self):
        """Test that metadata is consistent."""
        metadata = self.corpus.metadata
        self.assertIn('statutes', metadata)
        self.assertGreater(len(metadata['statutes']), 0)

        for statute_id in self.corpus.list_statutes():
            self.assertIn(statute_id, metadata['statutes'])


class StatuteCorpusIntegrationTests(unittest.TestCase):
    """Integration tests with real data."""

    def test_real_article_content(self):
        """Test that real article content is loaded."""
        corpus = StatuteCorpus()

        # Test ГК РФ Ст. 432
        article = corpus.get_article('gk_part1', '432')
        self.assertIsNotNone(article)
        self.assertIn('существенным условиям', article['text'])

        # Test ТК РФ Ст. 136
        article = corpus.get_article('tk_rf', '136')
        self.assertIsNotNone(article)
        self.assertIn('заработной платы', article['text'])

    def test_all_statutes_have_content(self):
        """Test that all statutes have content.json."""
        corpus = StatuteCorpus()

        for statute_id in corpus.list_statutes():
            statute = corpus.get_statute(statute_id)
            self.assertIsNotNone(statute, f"Statute {statute_id} not found")
            self.assertIn('articles', statute, f"Statute {statute_id} has no articles")


if __name__ == '__main__':
    unittest.main()
