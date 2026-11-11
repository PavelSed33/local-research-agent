import unittest
from datetime import date
from decimal import Decimal

from penalty import (
    PenaltyError,
    calc_contractual,
    calc_key_rate,
    handle_penalty,
    money,
    parse_amount,
    parse_date_ru,
    parse_rate_per_day,
)


class ParseTests(unittest.TestCase):
    def test_parse_amount_plain(self):
        self.assertEqual(parse_amount("100 000"), Decimal("100000.00"))

    def test_parse_amount_with_currency(self):
        self.assertEqual(parse_amount("100 000 руб."), Decimal("100000.00"))

    def test_parse_amount_comma(self):
        self.assertEqual(parse_amount("1234,56"), Decimal("1234.56"))

    def test_parse_amount_number(self):
        self.assertEqual(parse_amount(5000), Decimal("5000.00"))

    def test_bad_amount_rejected(self):
        with self.assertRaises(PenaltyError):
            parse_amount("сто тыщ")

    def test_negative_amount_rejected(self):
        with self.assertRaises(PenaltyError):
            parse_amount("-100")

    def test_parse_date(self):
        self.assertEqual(parse_date_ru("05.06.2027"), date(2027, 6, 5))

    def test_bad_date_rejected(self):
        with self.assertRaises(PenaltyError):
            parse_date_ru("32.13.2026")
        with self.assertRaises(PenaltyError):
            parse_date_ru("2026-12-31")

    def test_parse_rate(self):
        self.assertEqual(parse_rate_per_day("0,1"), Decimal("0.1"))

    def test_bad_rate_rejected(self):
        with self.assertRaises(PenaltyError):
            parse_rate_per_day("0")
        with self.assertRaises(PenaltyError):
            parse_rate_per_day("10")


class MoneyFormatTests(unittest.TestCase):
    def test_money_format(self):
        self.assertEqual(money(Decimal("123456.78")), "123 456 руб. 78 коп.")


class ContractualTests(unittest.TestCase):
    def test_simple_contractual(self):
        result = calc_contractual(Decimal("100000.00"), Decimal("0.1"), date(2026, 1, 1), date(2026, 1, 10))
        self.assertEqual(result["period"]["days"], 10)
        self.assertEqual(result["total"], "1 000 руб. 00 коп.")

    def test_single_day(self):
        result = calc_contractual(Decimal("50000.00"), Decimal("0.05"), date(2026, 3, 1), date(2026, 3, 1))
        self.assertEqual(result["period"]["days"], 1)
        self.assertEqual(result["total"], "25 руб. 00 коп.")

    def test_inverted_period_rejected(self):
        with self.assertRaises(PenaltyError):
            calc_contractual(Decimal("1000"), Decimal("0.1"), date(2026, 2, 1), date(2026, 1, 1))


class KeyRateTests(unittest.TestCase):
    def test_single_rate_period(self):
        # Ставка 14.00% действует с 27.07.2026.
        result = calc_key_rate(Decimal("365000.00"), date(2026, 8, 1), date(2026, 8, 10))
        self.assertEqual(result["period"]["days"], 10)
        # 365000 × 14% / 365 × 10 = 1400.00
        self.assertEqual(result["total"], "1 400 руб. 00 коп.")
        self.assertEqual(len(result["breakdown"]), 1)

    def test_splits_across_rate_change(self):
        # С 22.06.2026 — 14.25%, с 27.07.2026 — 14.00%.
        result = calc_key_rate(Decimal("365000.00"), date(2026, 7, 26), date(2026, 7, 27))
        self.assertEqual(len(result["breakdown"]), 2)
        first, second = result["breakdown"]
        self.assertEqual(first["days"], 1)  # 26.07 по 14.25% → 142.50
        self.assertEqual(first["penalty"], "142 руб. 50 коп.")
        self.assertEqual(second["days"], 1)  # 27.07 по 14.00% → 140.00
        self.assertEqual(second["penalty"], "140 руб. 00 коп.")
        self.assertEqual(result["total"], "282 руб. 50 коп.")

    def test_period_before_history_rejected(self):
        with self.assertRaises(PenaltyError):
            calc_key_rate(Decimal("1000"), date(2010, 1, 1), date(2010, 2, 1))

    def test_future_end_warns(self):
        result = calc_key_rate(Decimal("1000"), date(2026, 8, 1), date(2099, 1, 1))
        self.assertTrue(any("будущем" in warning for warning in result["warnings"]))


class HandlePenaltyTests(unittest.TestCase):
    def test_contractual_flow(self):
        result = handle_penalty(
            {
                "mode": "contractual",
                "amount": "100 000 руб.",
                "rate_per_day": "0,1",
                "start_date": "01.01.2026",
                "end_date": "10.01.2026",
            }
        )
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["total"], "1 000 руб. 00 коп.")
        self.assertEqual(result["warnings"], [])
        self.assertIn("не индивидуальная юридическая консультация", result["disclaimer"])
        self.assertTrue(result["checked_on"])

    def test_key_rate_flow(self):
        result = handle_penalty(
            {"mode": "key_rate", "amount": "365000", "start_date": "01.08.2026", "end_date": "10.08.2026"}
        )
        self.assertEqual(result["total"], "1 400 руб. 00 коп.")

    def test_missing_mode_rejected(self):
        with self.assertRaises(PenaltyError):
            handle_penalty({"amount": "1000", "start_date": "01.01.2026"})

    def test_missing_amount_rejected(self):
        with self.assertRaises(PenaltyError):
            handle_penalty({"mode": "key_rate", "start_date": "01.01.2026"})

    def test_missing_rate_for_contractual_rejected(self):
        with self.assertRaises(PenaltyError):
            handle_penalty({"mode": "contractual", "amount": "1000", "start_date": "01.01.2026"})

    def test_default_end_date_is_today(self):
        result = handle_penalty({"mode": "key_rate", "amount": "1000", "start_date": "01.11.2026"})
        self.assertEqual(result["period"]["to"], date.today().isoformat())


if __name__ == "__main__":
    unittest.main()
