"""Synthetic regression tests; these DO NOT validate any legal edition."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.compare_official_editions import compare_batch


class CompareOfficialEditionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.corpus = self.root / "corpus"
        self.snapshots = self.root / "snapshots"
        self.snapshots.mkdir()
        for code in ("gk", "uk"):
            folder = self.corpus / code
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_text(
                json.dumps({"status": "quarantine_unverified"}), encoding="utf-8")
            (folder / "articles.jsonl").write_text(
                json.dumps({"article": "321.1" if code == "uk" else "395",
                            "text": "Статья: тестовый текст\nВторая строка."},
                           ensure_ascii=False) + "\n", encoding="utf-8")

    def _check(self, code, text, filename):
        raw = text.encode("utf-8")
        (self.snapshots / filename).write_bytes(raw)
        return {"code": code, "article": "321.1" if code == "uk" else "395",
                "official_url": "https://publication.pravo.gov.ru/Document/View/test",
                "edition_date": "2026-01-01", "snapshot_file": filename,
                "snapshot_sha256": hashlib.sha256(raw).hexdigest()}

    def test_exact_and_changed_text_batch_stays_unverified(self):
        checks = [
            self._check("gk", "Статья: тестовый текст\nВторая строка.", "gk.txt"),
            self._check("uk", "Статья: изменённый текст\nВторая строка.", "uk.txt")
        ]
        result = compare_batch(self.corpus, self.snapshots, {"checks": checks})
        self.assertEqual(result["compared"], 2)
        self.assertEqual(result["exact_text_matches"], 1)
        self.assertFalse(result["edition_verified"])
        self.assertFalse(result["official_provenance_authenticated"])
        self.assertFalse(result["results"][0]["edition_verified"])
        self.assertIn("изменённый", result["results"][1]["diff_preview"])

    def test_unicode_line_separator_is_not_a_jsonl_record_boundary(self):
        folder = self.corpus / "gk"
        original = "Первая строка\u2028Вторая строка."
        (folder / "articles.jsonl").write_text(
            json.dumps({"article": "395", "text": original}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        check = self._check("gk", original, "gk.txt")
        result = compare_batch(self.corpus, self.snapshots, {"checks": [check]})
        self.assertEqual(result["exact_text_matches"], 1)
        self.assertFalse(result["edition_verified"])

    def test_wrong_digest_rejected(self):
        check = self._check("gk", "Something", "gk.txt")
        check["snapshot_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            compare_batch(self.corpus, self.snapshots, {"checks": [check]})

    def test_non_government_url_rejected(self):
        check = self._check("gk", "Something", "gk.txt")
        check["official_url"] = "https://pravo.gov.ru.evil.test/act"
        with self.assertRaisesRegex(ValueError, "Official URL"):
            compare_batch(self.corpus, self.snapshots, {"checks": [check]})

    def test_unsafe_path_rejected(self):
        check = self._check("gk", "Something", "gk.txt")
        check["snapshot_file"] = "../escape.txt"
        with self.assertRaisesRegex(ValueError, "safe relative path"):
            compare_batch(self.corpus, self.snapshots, {"checks": [check]})

    def test_duplicate_check_rejected(self):
        check = self._check("gk", "Something", "gk.txt")
        with self.assertRaisesRegex(ValueError, "Duplicate article"):
            compare_batch(self.corpus, self.snapshots, {"checks": [check, check]})

    def test_invalid_date_rejected(self):
        check = self._check("gk", "Something", "gk.txt")
        check["edition_date"] = "2026-02-30"
        with self.assertRaisesRegex(ValueError, "edition_date"):
            compare_batch(self.corpus, self.snapshots, {"checks": [check]})


if __name__ == "__main__":
    unittest.main()
