import tempfile
from pathlib import Path
import unittest

from agent import evidence_summary
from api import handle_ask


class EvidenceStatusTests(unittest.TestCase):
    def test_found_evidence_never_promotes_to_legal_verification(self):
        result = evidence_summary({'performed': True, 'verified': True}, {}, False)
        self.assertFalse(result['verified'])
        self.assertEqual(result['amendments']['status'], 'evidence_found_unverified')
        self.assertFalse(result['amendments']['verified'])

    def test_failure_empty_result_and_not_attempted_are_distinct(self):
        result = evidence_summary({
            'attempted': True, 'errors': ['timeout'],
            'official_portal': {'attempted': True},
        }, {}, False)
        self.assertEqual(result['amendments']['status'], 'unavailable')
        self.assertEqual(result['official_publications']['status'], 'no_evidence')
        self.assertEqual(result['court_practice']['status'], 'not_attempted')

    def test_partial_retrieval_stays_unverified(self):
        result = evidence_summary({'performed': True, 'errors': ['one source failed']}, {}, False)
        self.assertEqual(result['amendments']['status'], 'evidence_found_unverified')
        self.assertEqual(result['amendments']['error_count'], 1)
        self.assertFalse(result['verified'])

    def test_demo_api_has_explicit_evidence_status(self):
        with tempfile.TemporaryDirectory() as directory:
            result = handle_ask({'question': 'Тест', 'demo': True}, Path(directory))
        self.assertEqual(result['evidence']['status'], 'demo')
        self.assertEqual(result['evidence']['amendments']['status'], 'demo')
        self.assertFalse(result['evidence']['verified'])
