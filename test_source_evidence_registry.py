import json
from pathlib import Path
import unittest
from agent import classify_source_authority, source_authority_label


class SourceEvidenceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent
        self.inventory = json.loads((self.root / 'fixtures/152fz_amendment_map.json').read_text())
        self.registry = json.loads((self.root / self.inventory['source_evidence_registry']).read_text())

    def test_corroboration_does_not_promote_original_text_retrieval(self):
        self.assertFalse(self.registry['verified'])
        self.assertFalse(self.registry['coverage_verified'])
        self.assertEqual(self.registry['original_amendment_bodies_retrieved'], 0)
        sources = {source['id']: source for source in self.registry['sources']}
        linked = 0
        for article in self.inventory['article_scoped_inventory']['articles']:
            for entry in article['candidate_amendments']:
                if 'government_corroboration' not in entry:
                    continue
                evidence = entry['government_corroboration']
                self.assertIn(evidence['source_id'], sources)
                self.assertFalse(evidence['original_act_body_retrieved'])
                self.assertFalse(entry['official_text_retrieved'])
                self.assertTrue(any(obs['amending_act_date'] == entry['act_date'] and
                                    obs['amending_act_number'] == entry['act_number']
                                    for obs in sources[evidence['source_id']]['observations']))
                linked += 1
        self.assertEqual(linked, 6)

    def test_explanation_date_remains_distinct_from_primary_transition_text(self):
        source = next(s for s in self.registry['sources'] if s['source_class'] == 'official_authority_explanation')
        self.assertEqual(source['observations'][0]['effective_date_corroborated'], '2025-09-01')
        self.assertFalse(source['original_publication_body'])

    def test_stale_consolidation_and_unretrieved_newspaper_are_explicit(self):
        source = next(s for s in self.registry['sources'] if s['source_class'] == 'government_hosted_consolidated_copy')
        self.assertFalse(source['current_as_of_checked_on'])
        source = next(s for s in self.registry['sources'] if s['source_class'] == 'original_publication_link_unretrieved')
        self.assertFalse(source['content_observed'])

    def test_government_copy_and_explanation_are_not_original_publications(self):
        self.assertEqual(classify_source_authority('https://mintrud.gov.ru/docs/laws/130'), 'government_legal_reproduction')
        self.assertEqual(classify_source_authority('https://epp.genproc.gov.ru/ru/proc_39/'), 'official_legal_explanation')
        self.assertIn('не исходная публикация', source_authority_label('https://mintrud.gov.ru/docs/laws/130'))

    def test_lookalike_hosts_do_not_get_government_status(self):
        for url in ('https://mintrud.gov.ru.example.org/law', 'https://epp.genproc.gov.ru.example.org/'):
            self.assertEqual(classify_source_authority(url), 'unclassified')
