import json
import unittest

from scoring import compute_score, handle_scoring, ScoringError


def risk(severity: str, category: str = "категория") -> dict:
    return {"category": category, "severity": severity, "quote": "цитата", "comment": "комментарий"}


class ComputeScoreTests(unittest.TestCase):
    def test_clean_contract_scores_100(self):
        result = compute_score([], [])
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["verdict_color"], "green")
        self.assertEqual(result["deductions"], [])

    def test_single_high_risk(self):
        result = compute_score([risk("high")], [])
        self.assertEqual(result["score"], 80)
        self.assertEqual(result["verdict_color"], "green")

    def test_medium_and_low_risks(self):
        result = compute_score([risk("medium"), risk("low")], [])
        self.assertEqual(result["score"], 85)

    def test_missing_clauses_deduct(self):
        result = compute_score([], ["порядок приёмки", "форс-мажор"])
        self.assertEqual(result["score"], 70)
        self.assertEqual(result["verdict_color"], "yellow")

    def test_risk_deduction_is_capped(self):
        result = compute_score([risk("high")] * 10, [])  # 200 > кэпа 70
        self.assertEqual(result["risk_deduction"], 70)
        self.assertTrue(result["risk_deduction_capped"])
        self.assertEqual(result["score"], 30)
        self.assertEqual(result["verdict_color"], "red")

    def test_missing_deduction_is_capped(self):
        result = compute_score([], ["a", "b", "c", "d", "e"])  # 75 > кэпа 45
        self.assertEqual(result["missing_deduction"], 45)
        self.assertEqual(result["score"], 55)

    def test_score_never_below_zero(self):
        result = compute_score([risk("high")] * 10, ["a"] * 10)
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["verdict_color"], "red")

    def test_yellow_verdict_range(self):
        result = compute_score([risk("high"), risk("medium")], ["условие"])  # 100-30-15=55
        self.assertEqual(result["score"], 55)
        self.assertEqual(result["verdict_color"], "yellow")

    def test_unknown_severity_counts_as_low(self):
        result = compute_score([risk("strange")], [])
        self.assertEqual(result["score"], 95)

    def test_weights_are_exposed(self):
        result = compute_score([], [])
        self.assertEqual(result["weights"]["base"], 100)
        self.assertEqual(result["weights"]["severity"]["high"], 20)


class HandleScoringTests(unittest.TestCase):
    def test_demo_scoring(self):
        result = handle_scoring({"document": "Договор аренды с условиями оплаты и сроками.", "demo": True})
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["score"], 65)  # low-риск −5 и два отсутствующих условия −30
        self.assertIn("weights", result)
        self.assertTrue(result["demo"])

    def test_short_document_is_rejected(self):
        with self.assertRaises(ScoringError):
            handle_scoring({"document": "коротко", "demo": True})

    def test_missing_document_is_rejected(self):
        with self.assertRaises(ValueError):
            handle_scoring({"demo": True})


if __name__ == "__main__":
    unittest.main()
