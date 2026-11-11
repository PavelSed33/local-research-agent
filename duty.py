#!/usr/bin/env python3
"""Детерминированный расчёт госпошлины: ст. 333.19 и 333.21 НК РФ.

Все вычисления выполняются кодом (Decimal), без LLM и внешних запросов:
числа не зависят от модели и воспроизводимы. Шкалы — встроенный справочник
по редакциям НК РФ: с 08.09.2024 (ФЗ от 08.08.2024 № 259-ФЗ) и ранее.
Шкала выбирается по дате подачи заявления (filing_date).

CLI:  python duty.py 500000 --court arbitration
API:  POST /duty (см. api.py)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from agent import LEGAL_DISCLAIMER
from penalty import money, parse_amount, parse_date_ru

REFORM_DATE = date(2024, 9, 8)  # заявления, поданные после этой даты, — по новым шкалам
WHOLE_RUBLE = Decimal("1")

# Шкала имущественного иска: (верхняя граница цены иска или None, фикс. часть, % сверх нижней границы)
# Арбитражные суды, ст. 333.21 НК РФ (ред. 259-ФЗ).
ARBITRATION_SCALE_NEW: list[tuple[Decimal | None, Decimal, Decimal]] = [
    (Decimal("100000"), Decimal("10000"), Decimal("0")),
    (Decimal("1000000"), Decimal("10000"), Decimal("5")),
    (Decimal("10000000"), Decimal("55000"), Decimal("3")),
    (Decimal("50000000"), Decimal("325000"), Decimal("1")),
    (None, Decimal("725000"), Decimal("0.5")),
]
ARBITRATION_CAP_NEW = Decimal("10000000")

# Суды общей юрисдикции и мировые судьи, ст. 333.19 НК РФ (ред. 259-ФЗ).
GENERAL_SCALE_NEW: list[tuple[Decimal | None, Decimal, Decimal]] = [
    (Decimal("100000"), Decimal("4000"), Decimal("0")),
    (Decimal("300000"), Decimal("4000"), Decimal("3")),
    (Decimal("500000"), Decimal("10000"), Decimal("2.5")),
    (Decimal("1000000"), Decimal("15000"), Decimal("2")),
    (Decimal("3000000"), Decimal("25000"), Decimal("1")),
    (Decimal("8000000"), Decimal("45000"), Decimal("0.7")),
    (Decimal("24000000"), Decimal("80000"), Decimal("0.35")),
    (Decimal("50000000"), Decimal("136000"), Decimal("0.3")),
    (Decimal("100000000"), Decimal("214000"), Decimal("0.2")),
    (None, Decimal("314000"), Decimal("0.15")),
]
GENERAL_CAP_NEW = Decimal("900000")

# Редакции до 08.09.2024 (для заявлений, поданных раньше реформы).
ARBITRATION_SCALE_OLD: list[tuple[Decimal | None, Decimal, Decimal]] = [
    (Decimal("100000"), Decimal("0"), Decimal("4")),  # минимум 2 000 руб.
    (Decimal("200000"), Decimal("4000"), Decimal("3")),
    (Decimal("1000000"), Decimal("7000"), Decimal("2")),
    (Decimal("2000000"), Decimal("23000"), Decimal("1")),
    (None, Decimal("33000"), Decimal("0.5")),
]
ARBITRATION_MIN_OLD = Decimal("2000")
ARBITRATION_CAP_OLD = Decimal("200000")

GENERAL_SCALE_OLD: list[tuple[Decimal | None, Decimal, Decimal]] = [
    (Decimal("20000"), Decimal("0"), Decimal("4")),  # минимум 400 руб.
    (Decimal("100000"), Decimal("800"), Decimal("3")),
    (Decimal("200000"), Decimal("3200"), Decimal("2")),
    (Decimal("1000000"), Decimal("5200"), Decimal("1")),
    (None, Decimal("13200"), Decimal("0.5")),
]
GENERAL_MIN_OLD = Decimal("400")
GENERAL_CAP_OLD = Decimal("60000")

# Фиксированные пошлины (ред. 259-ФЗ): (физлицо, организация).
FIXED_NEW: dict[str, dict[str, dict[str, Decimal]]] = {
    "general": {
        "non_property": {"individual": Decimal("3000"), "organization": Decimal("20000")},
        "appeal": {"individual": Decimal("3000"), "organization": Decimal("15000")},
        "cassation": {"individual": Decimal("5000"), "organization": Decimal("20000")},
    },
    "arbitration": {
        "non_property": {"individual": Decimal("15000"), "organization": Decimal("50000")},
        "appeal": {"individual": Decimal("10000"), "organization": Decimal("30000")},
        "cassation": {"individual": Decimal("20000"), "organization": Decimal("50000")},
    },
}
# Редакции до 08.09.2024.
FIXED_OLD: dict[str, dict[str, dict[str, Decimal]]] = {
    "general": {
        "non_property": {"individual": Decimal("300"), "organization": Decimal("6000")},
        "appeal": {"individual": Decimal("150"), "organization": Decimal("3000")},
        "cassation": {"individual": Decimal("150"), "organization": Decimal("3000")},
    },
    "arbitration": {
        "non_property": {"individual": Decimal("300"), "organization": Decimal("6000")},
        "appeal": {"individual": Decimal("150"), "organization": Decimal("3000")},
        "cassation": {"individual": Decimal("150"), "organization": Decimal("3000")},
    },
}

COURT_ORDER_MIN_ARBITRATION_NEW = Decimal("8000")

MODES = ("claim", "court_order", "non_property", "appeal", "cassation")
COURTS = ("arbitration", "general")
APPLICANTS = ("individual", "organization")


class DutyError(ValueError):
    """Понятная ошибка входных параметров расчёта госпошлины."""


def rubles(amount: Decimal) -> Decimal:
    """Пошлина округляется до целых рублей (общая практика; НК это не регламентирует)."""
    return amount.quantize(WHOLE_RUBLE, rounding=ROUND_HALF_UP)


def scale_bounds(scale: list[tuple[Decimal | None, Decimal, Decimal]], amount: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal | None]:
    """Возвращает (нижняя граница, фикс. часть, процент, верхняя граница) диапазона суммы."""
    lower = Decimal("0")
    for upper, fixed, percent in scale:
        if upper is None or amount <= upper:
            return lower, fixed, percent, upper
        lower = upper
    raise DutyError("Не удалось подобрать диапазон шкалы.")  # недостижимо при верной шкале


def calc_claim_duty(court: str, amount: Decimal, new_scale: bool) -> dict:
    """Имущественный иск, подлежащий оценке: ст. 333.21 или 333.19 НК РФ."""
    if court == "arbitration":
        scale, cap = (ARBITRATION_SCALE_NEW, ARBITRATION_CAP_NEW) if new_scale else (ARBITRATION_SCALE_OLD, ARBITRATION_CAP_OLD)
        minimum = Decimal("0") if new_scale else ARBITRATION_MIN_OLD
        article = "подп. 1 п. 1 ст. 333.21 НК РФ"
    else:
        scale, cap = (GENERAL_SCALE_NEW, GENERAL_CAP_NEW) if new_scale else (GENERAL_SCALE_OLD, GENERAL_CAP_OLD)
        minimum = Decimal("0") if new_scale else GENERAL_MIN_OLD
        article = "подп. 1 п. 1 ст. 333.19 НК РФ"
    lower, fixed, percent, _ = scale_bounds(scale, amount)
    duty = fixed + (amount - lower) * percent / 100
    if minimum:
        duty = max(duty, minimum)
    capped = False
    if duty > cap:
        duty, capped = cap, True
    formula = (
        f"{money(fixed)} + {percent.normalize()}% × ({money(amount)} − {money(lower)})"
        if percent
        else f"фиксированная сумма {money(fixed)}"
    )
    return {
        "duty": money(rubles(duty)),
        "duty_exact": money(duty),
        "_value": duty,
        "bracket": {"lower": money(lower), "fixed": money(fixed), "percent": f"{percent.normalize()}%"},
        "cap_applied": capped,
        "formula": formula,
        "article": article,
    }


def handle_duty(payload: dict) -> dict:
    """Обработчик POST /duty: детерминированный расчёт без LLM."""
    if not isinstance(payload, dict):
        raise DutyError("Тело запроса должно быть JSON-объектом.")
    mode = str(payload.get("mode", "claim")).strip() or "claim"
    if mode not in MODES:
        raise DutyError(f"Поле mode должно быть одним из: {', '.join(MODES)}.")
    court = str(payload.get("court", "")).strip()
    if court not in COURTS:
        raise DutyError('Поле court обязательно: "arbitration" (арбитраж, ст. 333.21) или "general" (суды общей юрисдикции, ст. 333.19).')
    filing_raw = payload.get("filing_date")
    filing = parse_date_ru(filing_raw) if filing_raw else date.today()
    new_scale = filing > REFORM_DATE

    warnings: list[str] = []
    result: dict
    if mode in ("claim", "court_order"):
        if "amount" not in payload:
            raise DutyError("Для режимов claim и court_order нужно поле amount (цена иска).")
        amount = parse_amount(payload["amount"])
        claim = calc_claim_duty(court, amount, new_scale)
        claim_value = claim.pop("_value")
        if mode == "claim":
            result = {
                "mode": mode,
                "amount": money(amount),
                "duty": claim["duty"],
                "duty_exact": claim["duty_exact"],
                "breakdown": [claim],
                "formula": claim["formula"],
                "article": claim["article"],
            }
        else:
            order = claim_value / 2
            note = "50% от пошлины по имущественному иску"
            if court == "arbitration" and new_scale and order < COURT_ORDER_MIN_ARBITRATION_NEW:
                order = COURT_ORDER_MIN_ARBITRATION_NEW
                note += f", но не менее {money(COURT_ORDER_MIN_ARBITRATION_NEW)}"
            result = {
                "mode": mode,
                "amount": money(amount),
                "duty": money(rubles(order)),
                "duty_exact": money(order),
                "breakdown": [claim],
                "formula": note,
                "article": ("подп. 3 п. 1 ст. 333.21 НК РФ" if court == "arbitration" else "подп. 2 п. 1 ст. 333.19 НК РФ"),
            }
    else:
        applicant = str(payload.get("applicant", "")).strip()
        if applicant not in APPLICANTS:
            raise DutyError(f'Для режима {mode} нужно поле applicant: "individual" или "organization".')
        table = FIXED_NEW if new_scale else FIXED_OLD
        duty = table[court][mode][applicant]
        article_map = {
            "general": {
                "non_property": "подп. 3 п. 1 ст. 333.19 НК РФ",
                "appeal": "подп. 19 п. 1 ст. 333.19 НК РФ",
                "cassation": "подп. 20 п. 1 ст. 333.19 НК РФ",
            },
            "arbitration": {
                "non_property": "подп. 4 п. 1 ст. 333.21 НК РФ",
                "appeal": "подп. 19 п. 1 ст. 333.21 НК РФ",
                "cassation": "подп. 20 п. 1 ст. 333.21 НК РФ",
            },
        }
        result = {
            "mode": mode,
            "amount": None,
            "duty": money(duty),
            "duty_exact": money(duty),
            "breakdown": [],
            "formula": f"фиксированная сумма ({'физическое лицо' if applicant == 'individual' else 'организация'})",
            "article": article_map[court][mode],
        }

    if not new_scale:
        warnings.append(
            "Применена шкала, действовавшая до 08.09.2024 (заявления, поданные до реформы ФЗ № 259-ФЗ)."
        )
    result.update(
        {
            "status": "draft",
            "court": court,
            "scale": "редакция с 08.09.2024 (ФЗ № 259-ФЗ)" if new_scale else "редакция до 08.09.2024",
            "warnings": warnings,
            "checked_on": datetime.now(timezone.utc).date().isoformat(),
            "disclaimer": LEGAL_DISCLAIMER,
            "note": "Расчёт детерминированный, выполнен кодом без LLM; сумма округлена до целых рублей. "
            "Льготы (ст. 333.35, 333.36 НК РФ) не учитываются — проверьте, относитесь ли вы к льготным категориям. "
            "Перед оплатой сверьте сумму с действующей редакцией НК РФ и реквизитами конкретного суда.",
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Расчёт госпошлины: ст. 333.19 и 333.21 НК РФ")
    parser.add_argument("amount", nargs="?", default=None, help="Цена иска, например \"500 000\" (для claim/court_order)")
    parser.add_argument("--court", choices=COURTS, required=True, help="arbitration — арбитраж, general — суды общей юрисдикции")
    parser.add_argument("--mode", choices=MODES, default="claim", help="Тип обращения (по умолчанию claim)")
    parser.add_argument("--applicant", choices=APPLICANTS, default=None, help="Истец для фиксированных пошлин")
    parser.add_argument("--filing-date", default=None, help="Дата подачи заявления, ДД.ММ.ГГГГ (по умолчанию — сегодня)")
    args = parser.parse_args()
    payload: dict = {
        "mode": args.mode,
        "court": args.court,
        "filing_date": args.filing_date,
    }
    if args.amount is not None:
        payload["amount"] = args.amount
    if args.applicant is not None:
        payload["applicant"] = args.applicant
    try:
        result = handle_duty(payload)
    except DutyError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
