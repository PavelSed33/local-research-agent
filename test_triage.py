import json
import unittest

from agent import AgentError
from triage import (
    DemoTriageModel,
    handle_triage,
    triage_request,
    validate_triage,
    TriageError,
)


def valid_result() -> dict:
    return {
        "category": "contract",
        "urgency": "medium",
        "risk": "low",
        "summary": "Просят проверить проект договора аренды.",
        "recommended_endpoint": "/review",
    }


class FakeModel:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.responses.pop(0)


class ValidateTests(unittest.TestCase):
    def test_valid_result_passes(self):
        self.assertEqual(validate_triage(valid_result()), [])

    def test_unknown_category_is_rejected(self):
        result = valid_result()
        result["category"] = "magic"
        self.assertTrue(any("category" in issue for issue in validate_triage(result)))

    def test_unknown_urgency_is_rejected(self):
        result = valid_result()
        result["urgency"] = "yesterday"
        self.assertTrue(any("urgency" in issue for issue in validate_triage(result)))

    def test_unknown_risk_is_rejected(self):
        result = valid_result()
        result["risk"] = "enormous"
        self.assertTrue(any("risk" in issue for issue in validate_triage(result)))

    def test_unknown_endpoint_is_rejected(self):
        result = valid_result()
        result["recommended_endpoint"] = "/hallucinate"
        self.assertTrue(any("recommended_endpoint" in issue for issue in validate_triage(result)))

    def test_endpoint_must_fit_category(self):
        result = valid_result()
        result["recommended_endpoint"] = "/check"
        self.assertTrue(any("не подходит" in issue for issue in validate_triage(result)))

    def test_short_summary_is_rejected(self):
        result = valid_result()
        result["summary"] = "ок"
        self.assertTrue(any("summary" in issue for issue in validate_triage(result)))


class TriageRequestTests(unittest.TestCase):
    def test_valid_first_answer_needs_no_repair(self):
        model = FakeModel([json.dumps(valid_result())])
        result, repaired = triage_request(model, "Проверьте договор, пожалуйста.")
        self.assertFalse(repaired)
        self.assertEqual(model.calls, 1)
        self.assertEqual(result["category"], "contract")

    def test_invalid_answer_is_repaired_once(self):
        broken = valid_result()
        broken["category"] = "magic"
        model = FakeModel([json.dumps(broken), json.dumps(valid_result())])
        result, repaired = triage_request(model, "Проверьте договор, пожалуйста.")
        self.assertTrue(repaired)
        self.assertEqual(model.calls, 2)
        self.assertEqual(result["category"], "contract")

    def test_second_invalid_answer_raises(self):
        broken = valid_result()
        broken["risk"] = "enormous"
        model = FakeModel([json.dumps(broken), json.dumps(broken)])
        with self.assertRaises(AgentError):
            triage_request(model, "Проверьте договор, пожалуйста.")


class DemoModelTests(unittest.TestCase):
    def test_dispute_keywords_route_to_timeline(self):
        result = json.loads(DemoTriageModel().complete("", "Классифицируй обращение:\n\nКонтрагент не оплатил счёт, готовим претензию и иск в суд."))
        self.assertEqual(result["category"], "dispute")
        self.assertEqual(result["recommended_endpoint"], "/timeline")
        self.assertEqual(result["risk"], "high")

    def test_compliance_keywords_route_to_check(self):
        result = json.loads(DemoTriageModel().complete("", "Классифицируй обращение:\n\nНужно проверить контрагента по ИНН перед сделкой."))
        self.assertEqual(result["category"], "compliance")
        self.assertEqual(result["recommended_endpoint"], "/check")

    def test_contract_keywords_route_to_review(self):
        result = json.loads(DemoTriageModel().complete("", "Классифицируй обращение:\n\nПрислали проект договора аренды, надо посмотреть."))
        self.assertEqual(result["category"], "contract")
        self.assertEqual(result["recommended_endpoint"], "/review")

    def test_neutral_request_falls_back_to_general(self):
        result = json.loads(DemoTriageModel().complete("", "Классифицируй обращение:\n\nПодскажите, как оформить командировку."))
        self.assertEqual(result["category"], "general")
        self.assertEqual(result["recommended_endpoint"], "/ask")


class HandleTriageTests(unittest.TestCase):
    def test_demo_contract_request(self):
        result = handle_triage({"text": "Коллеги, проверьте, пожалуйста, проект договора поставки.", "demo": True})
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["category"], "contract")
        self.assertEqual(result["recommended_endpoint"], "/review")
        self.assertTrue(result["demo"])
        self.assertIn("disclaimer", result)

    def test_demo_dispute_with_urgency(self):
        result = handle_triage({"text": "Срочно! Контрагент не платит, завтра суд, нужна претензия.", "demo": True})
        self.assertEqual(result["category"], "dispute")
        self.assertEqual(result["urgency"], "high")
        self.assertEqual(result["risk"], "high")

    def test_missing_text_is_rejected(self):
        with self.assertRaises(TriageError):
            handle_triage({"text": "коротко", "demo": True})

    def test_non_dict_payload_is_rejected(self):
        with self.assertRaises(TriageError):
            handle_triage("не словарь")


if __name__ == "__main__":
    unittest.main()
