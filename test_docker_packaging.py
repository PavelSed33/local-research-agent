"""Static regression checks for the distributable Docker image."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class DockerPackagingTests(unittest.TestCase):
    def test_image_copies_restored_python_modules(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY *.py ./", dockerfile)
        for name in ("source_admission.py", "pravo_portal.py", "pravo_ips.py", "legal_history.py"):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_unreviewed_legal_fixtures_not_distributed(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertNotIn("COPY fixtures/", dockerfile)
        self.assertIn("fixtures/", dockerignore)
        self.assertIn("test_*.py", dockerignore)


if __name__ == "__main__":
    unittest.main()
