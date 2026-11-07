import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import AgentError, Source
from publication_audit import archive_act


class PublicationArchiveTests(unittest.TestCase):
    def test_full_endpoint_text_is_saved_instead_of_source_excerpt(self):
        text = 'Полный текст. ' * 2000
        source = Source('Акт', 'https://publication.pravo.gov.ru/api/Document?eoNumber=123', 'excerpt', 'query')
        with tempfile.TemporaryDirectory() as directory, patch(
            'publication_audit.PravoPortalSearch.search_act', return_value=[source]
        ), patch('publication_audit.PravoPortalSearch._get', return_value={'Text': text}):
            path = archive_act('2022-07-14', '266-ФЗ', Path(directory))
            result = json.loads(path.read_text())
            saved = (path.parent / result['publications'][0]['text_file']).read_bytes()
            self.assertEqual(saved.decode(), text.strip())
            self.assertEqual(result['publications'][0]['text_sha256'], hashlib.sha256(saved).hexdigest())
            self.assertFalse(result['verified'])
            self.assertEqual(result['status'], 'text_retrieved_unverified')

    def test_failed_retrieval_is_recorded_without_success_claim(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            'publication_audit.PravoPortalSearch.search_act', side_effect=AgentError('timeout')
        ):
            path = archive_act('2022-07-14', '266-ФЗ', Path(directory))
            result = json.loads(path.read_text())
            self.assertEqual(result['status'], 'retrieval_failed')
            self.assertEqual(result['error'], 'timeout')
            self.assertEqual(result['publications'], [])
