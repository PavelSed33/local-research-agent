import json
import unittest

from agent import AgentError
from review import handle_review, review_contract, validate_review

DOCUMENT = """ДОГОВОР АРЕНДЫ № 12

г. Москва                                                    01.02.2026

ООО «Арендодатель» и ООО «Арендатор» заключили настоящий договор о нижеследующем.

1. ПРЕДМЕТ ДОГОВОРА
1.1. Арендодатель передаёт Арендатору помещение площадью 50 кв. м по адресу: г. Москва, ул. Примерная, д. 1.

5. ОТВЕТСТВЕННОСТЬ СТОРОН
5.1. Арендодатель вправе в одностороннем порядке изменить размер арендной платы в любое время без согласования с Арендатором.
"""

VALID_RESULT = {
    "summary": "Договор содержит одностороннее право арендодателя менять плату.",
    "risks": [
        {
            "category": "односторонние права",
            "severity": "high",
            "quote": "Арендодатель вправе в одностороннем порядке изменить размер арендной платы в любое время без согласования с Арендатором.",
            "comment": "Арендатор не защищён от роста платы.",
            "recommendation": "Ограничить изменение платы не чаще раза в год и по индексу.",
        }
    ],
    "missing_clauses": ["порядок возврата помещения"],
}


class ReviewValidationTests(unittest.TestCase):
    def test_valid_result_passes(self):
        self.assertEqual(validate_review(VALID_RESULT, DOCUMENT), [])

    def test_quote_not_in_document_is_flagged(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["risks"][0]["quote"] = "Такой фразы в договоре точно не существует."
        issues = validate_review(result, DOCUMENT)
        self.assertTrue(any("цитата не найдена" in issue for issue in issues))

    def test_invalid_severity_is_flagged(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["risks"][0]["severity"] = "critical"
        issues = validate_review(result, DOCUMENT)
        self.assertTrue(any("severity" in issue for issue in issues))

    def test_short_quote_is_flagged(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["risks"][0]["quote"] = "г. Москва"
        issues = validate_review(result, DOCUMENT)
        self.assertTrue(any("короче" in issue for issue in issues))

    def test_quote_with_newlines_matches_document(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["risks"][0]["quote"] = "ДОГОВОР АРЕНДЫ № 12\n\nг. Москва"
        self.assertEqual(validate_review(result, DOCUMENT), [])


class RepairingReviewModel:
    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        result = json.loads(json.dumps(VALID_RESULT))
        if self.calls == 1:
            result["risks"][0]["quote"] = "Выдуманная формулировка, которой нет в тексте."
        return json.dumps(result, ensure_ascii=False)


class AlwaysBrokenReviewModel:
    def complete(self, system, user):
        result = json.loads(json.dumps(VALID_RESULT))
        result["risks"][0]["quote"] = "Выдуманная формулировка, которой нет в тексте."
        return json.dumps(result, ensure_ascii=False)


class ReviewRepairTests(unittest.TestCase):
    def test_repairs_review_once(self):
        model = RepairingReviewModel()
        result, repaired = review_contract(model, DOCUMENT)
        self.assertTrue(repaired)
        self.assertEqual(model.calls, 2)
        self.assertEqual(validate_review(result, DOCUMENT), [])

    def test_fails_after_unsuccessful_repair(self):
        with self.assertRaises(AgentError):
            review_contract(AlwaysBrokenReviewModel(), DOCUMENT)


class HandleReviewTests(unittest.TestCase):
    def test_demo_review_returns_structured_result(self):
        result = handle_review({"document": DOCUMENT, "demo": True})
        self.assertTrue(result["demo"])
        self.assertEqual(len(result["risks"]), 1)
        self.assertTrue(result["checked_on"])
        self.assertIn("не индивидуальная юридическая консультация", result["disclaimer"])
        self.assertFalse(result["review_repaired"])

    def test_missing_document_is_rejected(self):
        with self.assertRaises(ValueError):
            handle_review({"document": "", "demo": True})

    def test_short_document_is_rejected(self):
        with self.assertRaises(ValueError):
            handle_review({"document": "слишком коротко", "demo": True})


if __name__ == "__main__":
    unittest.main()
