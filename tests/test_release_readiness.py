"""Tests for the independent, fail-closed readiness evidence checklist."""
import importlib.util
from datetime import date, timedelta
from pathlib import Path
import unittest

FILE = Path(__file__).resolve().parents[1] / "scripts" / "release_readiness.py"
spec = importlib.util.spec_from_file_location("release_readiness", FILE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ReadinessTests(unittest.TestCase):
    def empty(self):
        return {name: None for name in mod.GATES}

    def passing(self):
        return {name: {
            "status": "pass",
            "verified_on": date.today().isoformat(),
            "proof_url": "https://github.com/example/repo/actions/runs/123",
        } for name in mod.GATES}

    def test_missing_evidence_fails_closed(self):
        result = mod.assess(self.empty())
        self.assertEqual(result["percentage"], 0)
        self.assertFalse(result["at_least_90_percent"])
        self.assertFalse(result["release_approved"])

    def test_incomplete_schema_rejected(self):
        with self.assertRaises(ValueError):
            mod.assess({"docker": {"status": "pass"}})

    def test_nine_of_eleven_is_not_ninety_percent(self):
        evidence = self.passing()
        evidence["ocr"] = None
        evidence["backups"] = None
        result = mod.assess(evidence)
        self.assertEqual(result["passed"], 9)
        self.assertFalse(result["at_least_90_percent"])

    def test_ten_of_eleven_reaches_threshold_but_not_release(self):
        evidence = self.passing()
        evidence["archive"] = None
        result = mod.assess(evidence)
        self.assertTrue(result["at_least_90_percent"])
        self.assertFalse(result["release_approved"])

    def test_malicious_proof_urls_do_not_count(self):
        for proof in (
            "https://user@github.com/example/proof",
            "https://github.com:443/example/proof",
            "https://github.com/example#fragment",
            "https://github.com/line\\nfeed",
            r"https://github.com\\@evil.example/proof",
            "https://localhost/proof",
            "http://github.com/example/proof",
        ):
            with self.subTest(proof=proof):
                evidence = self.passing()
                evidence["security"]["proof_url"] = proof
                self.assertFalse(mod.assess(evidence)["gates"]["security"]["passed"])

    def test_noncanonical_evidence_dates_do_not_count(self):
        for stamp in ("20261114", "2026-W46-6", "2026-11-14T00:00:00"):
            with self.subTest(stamp=stamp):
                evidence = self.passing()
                evidence["security"]["verified_on"] = stamp
                self.assertFalse(mod.assess(evidence)["gates"]["security"]["passed"])

    def test_stale_future_and_unlinked_evidence_not_counted(self):
        for replacement in (
            {"status": "pass", "verified_on": "2000-01-01", "proof_url": "https://example.org/proof"},
            {"status": "pass", "verified_on": (date.today() + timedelta(days=1)).isoformat(), "proof_url": "https://example.org/proof"},
            {"status": "pass", "verified_on": date.today().isoformat(), "proof_url": ""},
        ):
            with self.subTest(replacement=replacement):
                evidence = self.passing()
                evidence["security"] = replacement
                self.assertFalse(mod.assess(evidence)["gates"]["security"]["passed"])


if __name__ == "__main__":
    unittest.main()
