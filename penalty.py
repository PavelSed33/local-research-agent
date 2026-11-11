#!/usr/bin/env python3
"""Детерминированный расчёт неустойки: договорной и по ст. 395 ГК РФ.

Все вычисления выполняются кодом (Decimal), без LLM и внешних запросов:
числа не зависят от модели и воспроизводимы. Ключевая ставка — встроенный
справочник KEY_RATE_HISTORY по данным информационных сообщений Банка России.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from agent import LEGAL_DISCLAIMER

MIN_DOCUMENT_LENGTH = 0  # модуль работает с параметрами, а не с текстом договора

# Ключевая ставка Банка России (%, годовых) по информационным сообщениям ЦБ.
# Проверено по cbr.ru и справочным таблицам на 06.11.2026.
KEY_RATE_HISTORY: list[tuple[date, Decimal]] = [
    (date(2016, 9, 19), Decimal("10.00")),
    (date(2017, 3, 27), Decimal("9.75")),
    (date(2017, 5, 2), Decimal("9.25")),
    (date(2017, 6, 19), Decimal("9.00")),
    (date(2017, 9, 18), Decimal("8.50")),
    (date(2017, 10, 30), Decimal("8.25")),
    (date(2017, 12, 18), Decimal("7.75")),
    (date(2018, 2, 12), Decimal("7.50")),
    (date(2018, 3, 26), Decimal("7.25")),
    (date(2018, 9, 17), Decimal("7.50")),
    (date(2018, 12, 17), Decimal("7.75")),
    (date(2019, 6, 17), Decimal("7.50")),
    (date(2019, 7, 29), Decimal("7.25")),
    (date(2019, 9, 9), Decimal("7.00")),
    (date(2019, 10, 28), Decimal("6.50")),
    (date(2019, 12, 16), Decimal("6.25")),
    (date(2020, 2, 10), Decimal("6.00")),
    (date(2020, 4, 27), Decimal("5.50")),
    (date(2020, 6, 22), Decimal("4.50")),
    (date(2020, 7, 27), Decimal("4.25")),
    (date(2021, 3, 22), Decimal("4.50")),
    (date(2021, 4, 26), Decimal("5.00")),
    (date(2021, 6, 15), Decimal("5.50")),
    (date(2021, 7, 26), Decimal("6.50")),
    (date(2021, 9, 13), Decimal("6.75")),
    (date(2021, 10, 25), Decimal("7.50")),
    (date(2021, 12, 20), Decimal("8.50")),
    (date(2022, 2, 14), Decimal("9.50")),
    (date(2022, 2, 28), Decimal("20.00")),
    (date(2022, 4, 11), Decimal("17.00")),
    (date(2022, 5, 4), Decimal("14.00")),
    (date(2022, 5, 27), Decimal("11.00")),
    (date(2022, 6, 14), Decimal("9.50")),
    (date(2022, 7, 25), Decimal("8.00")),
    (date(2022, 9, 19), Decimal("7.50")),
    (date(2023, 7, 24), Decimal("8.50")),
    (date(2023, 8, 15), Decimal("12.00")),
    (date(2023, 9, 18), Decimal("13.00")),
    (date(2023, 10, 30), Decimal("15.00")),
    (date(2023, 12, 18), Decimal("16.00")),
    (date(2024, 7, 29), Decimal("18.00")),
    (date(2024, 9, 16), Decimal("19.00")),
    (date(2024, 10, 28), Decimal("21.00")),
    (date(2025, 6, 9), Decimal("20.00")),
    (date(2025, 7, 28), Decimal("18.00")),
    (date(2025, 9, 15), Decimal("17.00")),
    (date(2025, 10, 27), Decimal("16.50")),
    (date(2025, 12, 22), Decimal("16.00")),
    (date(2026, 2, 16), Decimal("15.50")),
    (date(2026, 3, 23), Decimal("15.00")),
    (date(2026, 4, 27), Decimal("14.50")),
    (date(2026, 6, 22), Decimal("14.25")),
    (date(2026, 7, 27), Decimal("14.00")),
]

DAYS_IN_YEAR = Decimal(365)
CENT = Decimal("0.01")


class PenaltyError(ValueError):
    """Понятная ошибка входных параметров расчёта."""


def parse_date_ru(value: str) -> date:
    """Разбирает дату ДД.ММ.ГГГГ."""
    match = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*", str(value))
    if not match:
        raise PenaltyError(f"Дата «{value}» не в формате ДД.ММ.ГГГГ.")
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise PenaltyError(f"Некорректная дата «{value}».") from exc


def parse_amount(value) -> Decimal:
    """Принимает сумму числом или строкой вида «100 000 руб.» / «100000,50»."""
    if isinstance(value, (int, float)):
        amount = Decimal(str(value))
    else:
        cleaned = str(value).replace(" ", " ").strip()
        cleaned = re.sub(r"(?i)\s*(руб(?:лей|ля|ль)?\.?|р\.?|коп\.?)\s*$", "", cleaned).strip()
        cleaned = cleaned.replace(" ", "").replace(",", ".")
        if not re.fullmatch(r"\d+(\.\d{1,2})?", cleaned):
            raise PenaltyError(f"Сумма «{value}» не распознана.")
        amount = Decimal(cleaned)
    if amount <= 0:
        raise PenaltyError("Сумма должна быть положительной.")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_rate_per_day(value) -> Decimal:
    """Ставка договорной неустойки в процентах за день («0,1» → 0.001 доля)."""
    cleaned = str(value).replace(",", ".").replace("%", "").strip()
    try:
        rate = Decimal(cleaned)
    except ArithmeticError as exc:
        raise PenaltyError(f"Ставка «{value}» не распознана.") from exc
    if not 0 < rate <= 5:
        raise PenaltyError("Ставка в день должна быть в диапазоне (0; 5] процентов.")
    return rate


def money(amount: Decimal) -> str:
    """Форматирует сумму: 123 456,78 руб."""
    amount = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    rubles = int(amount)
    cents = int((amount - rubles) * 100)
    return f"{rubles:,} руб. {cents:02d} коп.".replace(",", " ")


def check_period(start: date, end: date) -> int:
    if start > end:
        raise PenaltyError("Дата начала просрочки позже даты окончания.")
    return (end - start).days + 1


def calc_contractual(amount: Decimal, rate_percent: Decimal, start: date, end: date) -> dict:
    """Договорная неустойка: сумма × ставка (% в день) × дни просрочки."""
    days = check_period(start, end)
    penalty = (amount * rate_percent / 100 * days).quantize(CENT, rounding=ROUND_HALF_UP)
    return {
        "mode": "contractual",
        "amount": money(amount),
        "rate_per_day": f"{rate_percent.normalize()}% в день",
        "period": {"from": start.isoformat(), "to": end.isoformat(), "days": days},
        "breakdown": [
            {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "days": days,
                "rate": f"{rate_percent.normalize()}% в день",
                "penalty": money(penalty),
            }
        ],
        "total": money(penalty),
        "formula": f"{money(amount)} × {rate_percent.normalize()}% × {days} дн.",
    }


def calc_key_rate(amount: Decimal, start: date, end: date) -> dict:
    """Проценты по ст. 395 ГК РФ: сумма × ключевая ставка / 365 × дни, по периодам ставок."""
    days_total = check_period(start, end)
    history = KEY_RATE_HISTORY
    if start < history[0][0]:
        raise PenaltyError(
            f"Период начинается раньше {history[0][0].strftime('%d.%m.%Y')} — "
            "справочник ставок покрывает период с 19.09.2016."
        )
    warnings: list[str] = []
    last_rate_date = history[-1][0]
    if end > date.today():
        warnings.append("Дата окончания в будущем: применена последняя известная ставка, расчёт ориентировочный.")
    elif end > last_rate_date:
        warnings.append(
            f"Часть периода после {last_rate_date.strftime('%d.%m.%Y')} рассчитана "
            "по последней известной ставке; проверьте, не менял ли её Банк России."
        )

    breakdown: list[dict] = []
    total = Decimal("0.00")
    # Периоды действия ставок внутри [start, end].
    for index, (rate_date, rate) in enumerate(history):
        period_start = max(start, rate_date)
        period_end = history[index + 1][0] - _one_day() if index + 1 < len(history) else end
        period_end = min(period_end, end)
        if period_start > period_end:
            continue
        days = (period_end - period_start).days + 1
        part = (amount * rate / 100 / DAYS_IN_YEAR * days).quantize(CENT, rounding=ROUND_HALF_UP)
        total += part
        breakdown.append(
            {
                "from": period_start.isoformat(),
                "to": period_end.isoformat(),
                "days": days,
                "rate": f"{rate.normalize()}% годовых (ключевая ставка ЦБ)",
                "penalty": money(part),
            }
        )
    if not breakdown:
        raise PenaltyError("Не удалось разбить период по ставкам ЦБ.")
    return {
        "mode": "key_rate",
        "amount": money(amount),
        "rate_per_day": None,
        "period": {"from": start.isoformat(), "to": end.isoformat(), "days": days_total},
        "breakdown": breakdown,
        "total": money(total),
        "formula": "сумма × ставка ЦБ / 365 × дни просрочки (по периодам действия ставок), ст. 395 ГК РФ",
        "warnings": warnings,
    }


def _one_day():
    from datetime import timedelta

    return timedelta(days=1)


def handle_penalty(payload: dict) -> dict:
    """Обработчик POST /penalty: детерминированный расчёт без LLM."""
    if not isinstance(payload, dict):
        raise PenaltyError("Тело запроса должно быть JSON-объектом.")
    mode = str(payload.get("mode", "")).strip()
    if mode not in ("contractual", "key_rate"):
        raise PenaltyError('Поле mode обязательно: "contractual" (договорная) или "key_rate" (ст. 395 ГК РФ).')
    if "amount" not in payload:
        raise PenaltyError("Поле amount обязательно (сумма долга).")
    amount = parse_amount(payload["amount"])
    if not payload.get("start_date"):
        raise PenaltyError("Поле start_date обязательно (первый день просрочки, ДД.ММ.ГГГГ).")
    start = parse_date_ru(payload["start_date"])
    end_raw = payload.get("end_date")
    end = parse_date_ru(end_raw) if end_raw else date.today()

    if mode == "contractual":
        if "rate_per_day" not in payload:
            raise PenaltyError('Для режима "contractual" нужно поле rate_per_day (например, "0,1").')
        result = calc_contractual(amount, parse_rate_per_day(payload["rate_per_day"]), start, end)
        result["warnings"] = []
    else:
        result = calc_key_rate(amount, start, end)

    result.update(
        {
            "status": "draft",
            "checked_on": datetime.now(timezone.utc).date().isoformat(),
            "disclaimer": LEGAL_DISCLAIMER,
            "note": "Расчёт детерминированный, выполнен кодом без LLM. "
            "Суд может снизить неустойку по ст. 333 ГК РФ; дни считаются со дня, "
            "следующего за сроком исполнения, по день фактической уплаты включительно.",
        }
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Расчёт неустойки: договорной или по ст. 395 ГК РФ")
    parser.add_argument("amount", help='Сумма долга, например "100 000"')
    parser.add_argument("start_date", help="Первый день просрочки, ДД.ММ.ГГГГ")
    parser.add_argument("end_date", nargs="?", default=None, help="День уплаты, ДД.ММ.ГГГГ (по умолчанию — сегодня)")
    parser.add_argument("--mode", choices=["contractual", "key_rate"], required=True, help="Режим расчёта")
    parser.add_argument("--rate", default=None, help='Ставка в день для режима contractual, например "0,1"')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = {
        "mode": args.mode,
        "amount": args.amount,
        "start_date": args.start_date,
        "end_date": args.end_date,
    }
    if args.rate is not None:
        payload["rate_per_day"] = args.rate
    try:
        result = handle_penalty(payload)
    except PenaltyError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
