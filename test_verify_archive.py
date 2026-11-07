import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import Source
from publication_audit import archive_act
from verify_archive import verify_archive


class ArchiveIntegrityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        source = Source('Акт', 'https://publication.pravo.gov.ru/api/Document?eoNumber=123', 'excerpt', 'q')
        with patch('publication_audit.PravoPortalSearch.search_act', return_value=[source]), patch(
            'publication_audit.PravoPortalSearch._get', return_value={'Text': 'Статья 1. Текст.'}
        ):
            self.manifest = archive_act('2022-07-14', '266-ФЗ', Path(directory.name))
        self.data = json.loads(self.manifest.read_text())

    def save(self):
        self.manifest.write_text(json.dumps(self.data))

    def test_valid_archive_has_integrity_but_no_legal_verification(self):
        result = verify_archive(self.manifest)
        self.assertTrue(result['integrity_ok'], result)
        self.assertFalse(result['verified'])

    def test_modified_and_missing_files_are_rejected(self):
        text = self.manifest.parent / '0.txt'
        text.write_text('Подмена')
        self.assertFalse(verify_archive(self.manifest)['integrity_ok'])
        text.unlink()
        self.assertFalse(verify_archive(self.manifest)['integrity_ok'])

    def test_matching_hash_does_not_hide_inconsistent_response(self):
        text = 'Подмена'
        (self.manifest.parent / '0.txt').write_text(text)
        self.data['publications'][0].update(text_sha256=hashlib.sha256(text.encode()).hexdigest(), text_chars=len(text))
        self.save()
        self.assertFalse(verify_archive(self.manifest)['integrity_ok'])

    def test_empty_success_is_rejected(self):
        self.data['publications'] = []
        self.save()
        self.assertFalse(verify_archive(self.manifest)['integrity_ok'])

    def test_external_file_and_mismatched_url_are_rejected(self):
        record = self.data['publications'][0]
        record['text_file'] = '../outside.txt'
        self.save()
        self.assertFalse(verify_archive(self.manifest)['integrity_ok'])
        record['text_file'] = '0.txt'
        record['source_url'] = 'https://example.org/api/Document?eoNumber=123'
        self.save()
        self.assertFalse(verify_archive(self.manifest)['integrity_ok'])

    def test_empty_retrieval_does_not_produce_success_manifest(self):
        with patch('publication_audit.PravoPortalSearch.search_act', return_value=[]):
            path = archive_act('2022-07-14', '266-ФЗ', self.manifest.parent)
        self.assertEqual(json.loads(path.read_text())['status'], 'retrieval_failed')
