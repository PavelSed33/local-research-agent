import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import restore_primary_archive as archive
from scripts.synthetic_archive import synthetic_archive


class RestorePrimaryArchiveTests(unittest.TestCase):
    def setUp(self):
        self.capture = synthetic_archive(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.output = Path(temp.name) / "restored"
        self.output.mkdir()
        self.pdfs = sorted(self.capture.glob("*.pdf"))

    def response(self, url, timeout):
        self.assertTrue(url.startswith(archive.CAPTURE_URL))
        return io.BytesIO((self.capture / url.removeprefix(archive.CAPTURE_URL)).read_bytes())

    def test_completes_partial_archive_and_reuses_verified_files_offline(self):
        first = self.pdfs[0]
        (self.output / first.name).write_bytes(first.read_bytes())
        with patch.object(archive, "urlopen", side_effect=self.response) as fetch:
            result = archive.restore(self.output)
            self.assertEqual(result["restored"], len(self.pdfs) - 1)
            self.assertEqual(result["already_present"], 1)
            self.assertEqual(fetch.call_count, len(self.pdfs) - 1)
            fetch.reset_mock()
            again = archive.restore(self.output)
            fetch.assert_not_called()
        self.assertEqual(again["already_present"], len(self.pdfs))
        for original in self.pdfs:
            self.assertEqual(original.read_bytes(), (self.output / original.name).read_bytes())

    def test_bad_download_does_not_publish_partial_batch(self):
        def damaged(url, timeout):
            if url.endswith(self.pdfs[0].name):
                return io.BytesIO(b"%PDF mismatched capture")
            return self.response(url, timeout)

        with patch.object(archive, "urlopen", side_effect=damaged), self.assertRaises(ValueError):
            archive.restore(self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_existing_corrupted_file_is_preserved(self):
        target = self.output / self.pdfs[-1].name
        target.write_bytes(b"preexisting bytes")
        with patch.object(archive, "urlopen") as fetch, self.assertRaises(ValueError):
            archive.restore(self.output)
        fetch.assert_not_called()
        self.assertEqual(target.read_bytes(), b"preexisting bytes")
