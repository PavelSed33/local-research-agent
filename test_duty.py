import unittest
from decimal import Decimal

from duty import DutyError, calc_claim_duty, handle_duty, scale_bounds, ARBITRATION_SCALE_NEW, GENERAL_SCALE_NEW


class ClaimDutyTests(unittest.TestCase):
    def test_arbitration_first_bracket_is_fixed(self):
        result = calc_claim_duty("arbitration", Decimal("50000"), True)
        self.assertEqual(result["duty"], "10 000 руб. 00 коп.")

    def test_arbitration_second_bracket(self):
        # 10 000 + 5% × 400 000 = 30 000
        result = calc_claim_duty("arbitration", Decimal("500000"), True)
        self.assertEqual(result["duty"], "30 000 руб. 00 коп.")

    def test_arbitration_bracket_continuity_at_1m(self):
        # На границе 1 000 000: 10 000 + 5% × 900 000 = 55 000
        result = calc_claim_duty("arbitration", Decimal("1000000"), True)
        self.assertEqual(result["duty"], "55 000 руб. 00 коп.")

    def test_arbitration_cap_applied(self):
        # 2 млрд: 725 000 + 0,5% × 1 950 000 000 = 10 475 000 > кэпа 10 000 000
        result = calc_claim_duty("arbitration", Decimal("2000000000"), True)
        self.assertEqual(result["duty"], "10 000 000 руб. 00 коп.")
        self.assertTrue(result["cap_applied"])

    def test_general_example_from_practice(self):
        # 750 000 → 15 000 + 2% × 250 000 = 20 000
        result = calc_claim_duty("general", Decimal("750000"), True)
        self.assertEqual(result["duty"], "20 000 руб. 00 коп.")

    def test_general_bracket_continuity(self):
        # 8 000 000 → 45 000 + 0,7% × 5 000 000 = 80 000; далее 24 000 000 → 80 000 + 0,35% × 16 000 000 = 136 000
        self.assertEqual(calc_claim_duty("general", Decimal("8000000"), True)["duty"], "80 000 руб. 00 коп.")
        self.assertEqual(calc_claim_duty("general", Decimal("24000000"), True)["duty"], "136 000 руб. 00 коп.")

    def test_general_cap_applied(self):
        result = calc_claim_duty("general", Decimal("5000000000"), True)
        self.assertEqual(result["duty"], "900 000 руб. 00 коп.")
        self.assertTrue(result["cap_applied"])

    def test_old_arbitration_scale(self):
        # Старая редакция: 150 000 → 4 000 + 3% × 50 000 = 5 500
        result = calc_claim_duty("arbitration", Decimal("150000"), False)
        self.assertEqual(result["duty"], "5 500 руб. 00 коп.")

    def test_old_arbitration_minimum(self):
        # Старая редакция: 10 000 × 4% = 400 < минимума 2 000
        result = calc_claim_duty("arbitration", Decimal("10000"), False)
        self.assertEqual(result["duty"], "2 000 руб. 00 коп.")

    def test_old_general_scale(self):
        # Старая редакция: 150 000 → 3 200 + 2% × 50 000 = 4 200
        result = calc_claim_duty("general", Decimal("150000"), False)
        self.assertEqual(result["duty"], "4 200 руб. 00 коп.")

    def test_scale_bounds_last_open_bracket(self):
        lower, fixed, percent, upper = scale_bounds(ARBITRATION_SCALE_NEW, Decimal("60000000"))
        self.assertIsNone(upper)
        self.assertEqual(lower, Decimal("50000000"))


class HandleDutyTests(unittest.TestCase):
    def test_claim_arbitration(self):
        result = handle_duty({"mode": "claim", "court": "arbitration", "amount": "500 000"})
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["duty"], "30 000 руб. 00 коп.")
        self.assertIn("333.21", result["article"])
        self.assertEqual(result["scale"], "редакция с 08.09.2024 (ФЗ № 259-ФЗ)")

    def test_claim_general(self):
        result = handle_duty({"mode": "claim", "court": "general", "amount": 750000})
        self.assertEqual(result["duty"], "20 000 руб. 00 коп.")
        self.assertIn("333.19", result["article"])

    def test_court_order_is_half(self):
        result = handle_duty({"mode": "court_order", "court": "general", "amount": 750000})
        self.assertEqual(result["duty"], "10 000 руб. 00 коп.")

    def test_court_order_arbitration_minimum(self):
        # 50% от 10 000 = 5 000, но не менее 8 000
        result = handle_duty({"mode": "court_order", "court": "arbitration", "amount": 90000})
        self.assertEqual(result["duty"], "8 000 руб. 00 коп.")
        self.assertIn("не менее", result["formula"])

    def test_non_property_fixed(self):
        result = handle_duty({"mode": "non_property", "court": "general", "applicant": "individual"})
        self.assertEqual(result["duty"], "3 000 руб. 00 коп.")
        result = handle_duty({"mode": "non_property", "court": "arbitration", "applicant": "organization"})
        self.assertEqual(result["duty"], "50 000 руб. 00 коп.")

    def test_appeal_and_cassation(self):
        self.assertEqual(
            handle_duty({"mode": "appeal", "court": "arbitration", "applicant": "organization"})["duty"],
            "30 000 руб. 00 коп.",
        )
        self.assertEqual(
            handle_duty({"mode": "cassation", "court": "general", "applicant": "individual"})["duty"],
            "5 000 руб. 00 коп.",
        )

    def test_filing_date_before_reform_uses_old_scale(self):
        result = handle_duty({
            "mode": "claim", "court": "arbitration", "amount": 150000, "filing_date": "01.09.2024",
        })
        self.assertEqual(result["duty"], "5 500 руб. 00 коп.")
        self.assertEqual(result["scale"], "редакция до 08.09.2024")
        self.assertTrue(result["warnings"])

    def test_reform_boundary_date(self):
        # 08.09.2024 — ещё старая шкала (новая применяется к заявлениям, поданным ПОСЛЕ 08.09.2024)
        result = handle_duty({"mode": "claim", "court": "arbitration", "amount": 150000, "filing_date": "08.09.2024"})
        self.assertEqual(result["scale"], "редакция до 08.09.2024")
        result = handle_duty({"mode": "claim", "court": "arbitration", "amount": 150000, "filing_date": "09.09.2024"})
        self.assertEqual(result["scale"], "редакция с 08.09.2024 (ФЗ № 259-ФЗ)")

    def test_missing_court_is_rejected(self):
        with self.assertRaises(DutyError):
            handle_duty({"mode": "claim", "amount": 1000})

    def test_missing_amount_is_rejected(self):
        with self.assertRaises(DutyError):
            handle_duty({"mode": "claim", "court": "general"})

    def test_missing_applicant_is_rejected(self):
        with self.assertRaises(DutyError):
            handle_duty({"mode": "appeal", "court": "general"})

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(DutyError):
            handle_duty({"mode": "надзор", "court": "general", "applicant": "individual"})


if __name__ == "__main__":
    unittest.main()
