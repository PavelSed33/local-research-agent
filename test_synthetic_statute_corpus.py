"""Synthetic, copyright-safe regression tests for the quarantined statute corpus.

All records are generated in a temporary directory. No real legal text is stored.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from statute_corpus import ROOT, corpus_report


class SyntheticStatuteCorpusTests(unittest.TestCase):
    def _report(self, *, file_name="synthetic.json", url="https://example.invalid/fake-act",
                corrupt_hash=False, escape=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = "Статья 1. Вымышленная норма исключительно для проверки программного кода."
            section = {
                "card_id": "LAW-02:15",
                "act_id": "gk1",
                "provision": "15",
                "title": "Вымышленный акт",
                "heading": "Статья 1",
                "text": text,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "url": url,
                "kind": "public_copy",
                "captured_on": "2026-11-08",
            }
            data = {"act_id": "gk1", "sections": [section]}
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            if not escape:
                (root / file_name).write_bytes(raw)
            manifest = {
                "schema_version": 1, "captured_on": "2026-11-08",
                "groups": [{"act_id": "gk1", "file": file_name,
                            "sha256": "0" * 64 if corrupt_hash else hashlib.sha256(raw).hexdigest()}],
                "verified": False, "commercial_use_allowed": False,
            }
            (root / "manifest.json").write_text(json.dumps(manifest))
            return corpus_report(corpus_dir=root, as_of="2026-11-08", include_text=True)

    def test_quarantined_production_corpus_serves_no_articles(self):
        report = corpus_report(corpus_dir=ROOT, as_of="2026-11-08", include_text=True)
        self.assertEqual(report["entries"], [])
        self.assertEqual(report["text_received_count"], 0)
        self.assertFalse(report["verified"])
        self.assertFalse(report["commercial_use_allowed"])

    def test_synthetic_untrusted_host_is_rejected(self):
        report = self._report()
        self.assertEqual(report["entries"], [])
        self.assertTrue(report["errors"])

    def test_synthetic_modified_hash_is_rejected(self):
        report = self._report(corrupt_hash=True)
        self.assertEqual(report["entries"], [])
        self.assertTrue(any("sha256" in error for error in report["errors"]))

    def test_synthetic_path_traversal_is_rejected(self):
        report = self._report(file_name="../outside.json", escape=True)
        self.assertEqual(report["entries"], [])
        self.assertTrue(report["errors"])

    def test_synthetic_lookalike_host_is_rejected(self):
        report = self._report(url="https://www.consultant.ru.evil.invalid/fake-act")
        self.assertEqual(report["entries"], [])
        self.assertTrue(report["errors"])


if __name__ == "__main__":
    unittest.main()
