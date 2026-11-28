"""Regression tests for the preventive copyright hygiene check."""
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import check_copyright_hygiene as hygiene
from scripts.check_copyright_hygiene import PROPRIETARY_MARKERS


class CopyrightHygieneTests(unittest.TestCase):
    def test_ownership_notice_is_flagged(self):
        self.assertIsNotNone(PROPRIETARY_MARKERS.search("© КонсультантПлюс"))
        self.assertIsNotNone(PROPRIETARY_MARKERS.search("Гарант — все права защищены"))

    def test_documentation_brand_mentions_are_not_flagged(self):
        self.assertIsNone(PROPRIETARY_MARKERS.search("Не загружайте материалы КонсультантПлюс без разрешения"))
        self.assertIsNone(PROPRIETARY_MARKERS.search("Правила авторских прав для Гарант"))

    def test_only_quarantine_manifest_may_be_tracked_in_retired_collections(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            manifest = root / "fixtures/statute_corpus/manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"groups":[]}', encoding="utf-8")
            subprocess.run(["git", "add", "fixtures"], cwd=root, check=True)
            with patch.object(hygiene, "ROOT", root), redirect_stdout(StringIO()):
                self.assertEqual(hygiene.main(), 0)
                paths = ["fixtures/statutes/gk_part2/content.json", "fixtures/statute_corpus/old/gk2.json"]
                for name in paths:
                    path = root / name
                    path.parent.mkdir(parents=True)
                    path.write_text("{}", encoding="utf-8")
                subprocess.run(["git", "add", "fixtures"], cwd=root, check=True)
                with redirect_stderr(StringIO()) as errors:
                    self.assertEqual(hygiene.main(), 1)
                for name in paths:
                    self.assertIn(name, errors.getvalue())


if __name__ == "__main__":
    unittest.main()
