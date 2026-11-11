import unittest
from datetime import date
from unittest.mock import patch

from agent import AgentError
from check import (
    CheckError,
    build_risk_flags,
    fetch_egrul,
    handle_check,
    parse_egrul_rows,
    validate_inn,
)

VALID_INN = "7707083893"  # проходит контрольную сумму
EGRUL_ROWS = [
    {
        "n": "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ «АРЕНДОДАТЕЛЬ»",
        "c": "ООО «АРЕНДОДАТЕЛЬ»",
        "i": VALID_INN,
        "o": "1027700132195",
        "p": "770701001",
        "r": "15.01.2010",
        "e": "",
        "g": "ГЕНЕРАЛЬНЫЙ ДИРЕКТОР: Иванов Иван Иванович",
        "rn": "Г.Москва",
    }
]


class ValidateInnTests(unittest.TestCase):
    def test_valid_inn_passes(self):
        self.assertEqual(validate_inn("7707083893"), VALID_INN)

    def test_strips_spaces(self):
        self.assertEqual(validate_inn(" 7707083893 "), VALID_INN)

    def test_bad_inn_rejected(self):
        with self.assertRaises(CheckError):
            validate_inn("123")
        with self.assertRaises(CheckError):
            validate_inn("770708389a")
        with self.assertRaises(CheckError):
            validate_inn("12345678901")  # 11 цифр


class ParseRowsTests(unittest.TestCase):
    def test_exact_inn_match(self):
        company = parse_egrul_rows(EGRUL_ROWS, VALID_INN)
        self.assertEqual(company["ogrn"], "1027700132195")
        self.assertEqual(company["registered_on"], "15.01.2010")

    def test_no_match_returns_none(self):
        self.assertIsNone(parse_egrul_rows(EGRUL_ROWS, "500100732259"))

    def test_empty_rows_returns_none(self):
        self.assertIsNone(parse_egrul_rows([], VALID_INN))


class RiskFlagsTests(unittest.TestCase):
    def test_not_found_is_high(self):
        flags = build_risk_flags(None, VALID_INN, date(2026, 11, 6))
        self.assertEqual(flags[0]["code"], "not_found")
        self.assertEqual(flags[0]["severity"], "high")

    def test_terminated_is_high(self):
        company = dict(parse_egrul_rows(EGRUL_ROWS, VALID_INN))
        company["terminated_on"] = "01.02.2026"
        flags = build_risk_flags(company, VALID_INN, date(2026, 11, 6))
        self.assertTrue(any(f["code"] == "terminated" and f["severity"] == "high" for f in flags))

    def test_fresh_company_is_medium(self):
        company = dict(parse_egrul_rows(EGRUL_ROWS, VALID_INN))
        company["registered_on"] = "01.09.2026"
        flags = build_risk_flags(company, VALID_INN, date(2026, 11, 6))
        self.assertTrue(any(f["code"] == "fresh_registration" and f["severity"] == "medium" for f in flags))

    def test_mature_company_has_no_flags(self):
        company = parse_egrul_rows(EGRUL_ROWS, VALID_INN)
        self.assertEqual(build_risk_flags(company, VALID_INN, date(2026, 11, 6)), [])

    def test_kpp_and_head_parsed(self):
        company = parse_egrul_rows(EGRUL_ROWS, VALID_INN)
        self.assertEqual(company["kpp"], "770701001")
        self.assertIn("Иванов", company["head"])
        self.assertEqual(company["region"], "Г.Москва")


class FetchEgrulTests(unittest.TestCase):
    def test_fetch_success(self):
        responses = iter([{"t": "TOKEN123"}, {"rows": EGRUL_ROWS}])
        with patch("check._post_form", side_effect=lambda url, fields: next(responses)), \
             patch("check._get_json", side_effect=lambda url: next(responses)):
            result = fetch_egrul(VALID_INN)
        self.assertEqual(result["rows"], EGRUL_ROWS)

    def test_fetch_retries_then_fails(self):
        with patch("check._post_form", return_value={"t": "TOKEN123"}), \
             patch("check._get_json", side_effect=AgentError("таймаут")), \
             patch("check.time.sleep"):
            with self.assertRaises(AgentError):
                fetch_egrul(VALID_INN, retries=2)

    def test_no_token_fails_fast(self):
        with patch("check._post_form", return_value={}):
            with self.assertRaises(AgentError):
                fetch_egrul(VALID_INN)


class HandleCheckTests(unittest.TestCase):
    def test_demo_check(self):
        result = handle_check({"inn": VALID_INN, "demo": True})
        self.assertTrue(result["demo"])
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["company"]["inn"], VALID_INN)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["checks"]["egrul"], "demo")
        self.assertIn("не индивидуальная юридическая консультация", result["disclaimer"])

    def test_broken_checksum_warns(self):
        result = handle_check({"inn": "7707083891", "demo": True})
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("7707083891", result["warnings"][0])

    def test_missing_inn_rejected(self):
        with self.assertRaises(CheckError):
            handle_check({})

    def test_real_check_with_mock(self):
        with patch("check.fetch_egrul", return_value={"rows": EGRUL_ROWS}), \
             patch("check.search_arbitration_cases", return_value={"performed": False, "reason": "", "cases_found": 0, "results": []}):
            result = handle_check({"inn": VALID_INN})
        self.assertFalse(result["demo"])
        self.assertEqual(result["checks"]["egrul"], "performed")
        self.assertEqual(result["risk_level"], "low")
        self.assertEqual(result["risk_flags"], [])

    def test_real_check_not_found(self):
        with patch("check.fetch_egrul", return_value={"rows": []}):
            result = handle_check({"inn": VALID_INN})
        self.assertIsNone(result["company"])
        self.assertEqual(result["risk_level"], "high")
        self.assertEqual(result["risk_flags"][0]["code"], "not_found")


if __name__ == "__main__":
    unittest.main()
