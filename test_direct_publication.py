import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import AgentError
from pravo_portal import PravoPortalSearch
from publication_audit import archive_act


class DirectPublicationTests(unittest.TestCase):
    identity = '0001202012300044'

    def record(self):
        return {'EoNumber': self.identity, 'DocumentNumber': '519-ФЗ',
                'SignDate': '2020-12-30', 'Name': 'Федеральный закон'}

    def test_direct_lookup_skips_name_search_and_checks_metadata(self):
        portal = PravoPortalSearch()
        with patch.object(portal, '_get', side_effect=[self.record(), {'Text': 'Текст акта'}]) as get:
            sources = portal.search_act('2020-12-30', '519-ФЗ', publication_id=self.identity)
        self.assertEqual(len(sources), 1)
        self.assertEqual(get.call_args_list[0].args, ('/api/Document', {'eoNumber': self.identity}))
        self.assertEqual(get.call_count, 2)

    def test_wrong_id_date_or_number_never_downloads_text(self):
        for key, value in [('EoNumber', '0001202012300045'),
                           ('SignDate', '2021-12-30'), ('DocumentNumber', '518-ФЗ')]:
            with self.subTest(key=key):
                record = self.record()
                record[key] = value
                portal = PravoPortalSearch()
                with patch.object(portal, '_get', return_value=record) as get:
                    with self.assertRaises(AgentError):
                        portal.search_act('2020-12-30', '519-ФЗ', publication_id=self.identity)
                    self.assertEqual(get.call_count, 1)

    def test_invalid_publication_id_is_rejected_before_request(self):
        with patch.object(PravoPortalSearch, '_get') as get:
            with self.assertRaises(AgentError):
                PravoPortalSearch().search_act('2020-12-30', '519-ФЗ', publication_id='../../other')
            get.assert_not_called()

    def test_archive_preserves_requested_publication_id(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            PravoPortalSearch, '_get', side_effect=[self.record(), {'Text': 'Текст акта'}, {'Text': 'Текст акта'}]
        ):
            path = archive_act('2020-12-30', '519-ФЗ', Path(directory), publication_id=self.identity)
            result = json.loads(path.read_text())
        self.assertEqual(result['requested_publication_id'], self.identity)
        self.assertEqual(result['status'], 'text_retrieved_unverified')
