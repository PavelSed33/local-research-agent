"""Synthetic tests: quarantine search must never imply legal verification."""
import json
from pathlib import Path
import tempfile
import unittest

from legal_quarantine_search import CorpusError, search


class QuarantineSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for code in ('gk','nk','tk','koap'):
            folder = self.root / code
            folder.mkdir()
            (folder / 'manifest.json').write_text(json.dumps({
                'status':'quarantine_unverified',
                'legal_review_approved':False,
                'source_claim_verified':False,
                'warning':'Unverified edition'}), encoding='utf-8')
            (folder / 'articles.jsonl').write_text(json.dumps({
                'id':code+'-1','part':'I','article':'12.8',
                'heading':'Тестовая статья','text':'Тестовое содержание'}, ensure_ascii=False)+'\\n', encoding='utf-8')

    def test_each_supported_code(self):
        for code in ('gk','nk','tk','koap'):
            with self.subTest(code=code):
                result=search(self.root,code,'статья 12.8')
                self.assertEqual(result['total_matches'],1)
                self.assertFalse(result['verified'])
                self.assertFalse(result['results'][0]['verified'])

    def test_unverified_manifest_required(self):
        folder=self.root/'tk'
        p=folder/'manifest.json'
        manifest=json.loads(p.read_text(encoding='utf-8'))
        manifest['legal_review_approved']=True
        p.write_text(json.dumps(manifest),encoding='utf-8')
        with self.assertRaises(CorpusError):
            search(self.root,'tk','12.8')

    def test_unknown_code_and_invalid_limit(self):
        with self.assertRaises(CorpusError):
            search(self.root,'unknown','12.8')
        with self.assertRaises(CorpusError):
            search(self.root,'gk','12.8',limit=True)


if __name__=='__main__':
    unittest.main()
