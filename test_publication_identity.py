import unittest
from unittest.mock import patch

from agent import AgentError
from pravo_portal import PravoPortalSearch


class PublicationIdentityTests(unittest.TestCase):
    def record(self, **kwargs):
        return dict(DocumentName='Федеральный закон', DocumentNumber='266-ФЗ',
                    SignDate='2022-07-14T00:00:00', EoNumber='0001202207140001', **kwargs)

    def test_only_matching_act_is_downloaded(self):
        bank = PravoPortalSearch()
        wrong_year = self.record()
        wrong_year['SignDate'] = '2009-11-25'
        wrong_number = self.record()
        wrong_number['DocumentNumber'] = '265-ФЗ'
        wrong_number['DocumentName'] = 'О внесении изменений в 266-ФЗ'
        def get(path, params):
            if path == '/api/Documents':
                return [wrong_year, wrong_number, self.record()]
            self.assertEqual(params['eonumber'], '0001202207140001')
            return {'Text': 'Статья 1. Внести изменения в Федеральный закон.'}
        with patch.object(bank, '_get', side_effect=get) as request:
            sources = bank.search_act('2022-07-14', '266-ФЗ')
        self.assertEqual(len(sources), 1)
        self.assertEqual(request.call_count, 2)

    def test_missing_identity_or_text_is_an_explicit_gap(self):
        for key in ('SignDate', 'DocumentNumber', 'EoNumber', 'text'):
            with self.subTest(key=key):
                record = self.record()
                if key != 'text':
                    record.pop(key)
                bank = PravoPortalSearch()
                with patch.object(bank, '_get', side_effect=[[record], {}]):
                    with self.assertRaisesRegex(AgentError, 'Не получен текст'):
                        bank.search_act('2022-07-14', '266-ФЗ')

    def test_russian_date_and_number_spacing(self):
        record = self.record()
        record.update(SignDate='14.07.2022', DocumentNumber='№ 266-ФЗ')
        with patch.object(PravoPortalSearch, '_get', side_effect=[[record], {'Text': 'Текст акта'}]):
            self.assertEqual(len(PravoPortalSearch().search_act('2022-07-14', '266-ФЗ')), 1)

    def test_invalid_requested_identity_does_not_call_portal(self):
        with patch.object(PravoPortalSearch, '_get') as get:
            with self.assertRaises(AgentError):
                PravoPortalSearch().search_act('2022-02-30', '266-ФЗ')
            get.assert_not_called()
