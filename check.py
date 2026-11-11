#!/usr/bin/env python3
"""Проверка контрагента по ИНН.

Источники:
  - ЕГРЮЛ/ЕГРИП ФНС (egrul.nalog.ru) — бесплатный сервис, ключ не нужен:
    наименование, ОГРН, КПП, дата регистрации, адрес, прекращение деятельности;
  - опционально (если задан TAVILY_API_KEY) — выборочный поиск упоминаний
    на kad.arbitr.ru (арбитражные дела).

Массовость адреса и недостоверность сведений ФНС публикует в полной выписке
(PDF): без сторонних библиотек она не разбирается, поэтому эти признаки честно
помечаются как непроверенные. Санкционные списки открытого бесплатного API
не имеют и в проверку не входят — см. поле note.

CLI:  python check.py 7707083893 [--demo]
API:  POST /check (см. api.py)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any

from agent import LEGAL_DISCLAIMER, AgentError, load_dotenv
from extract import inn_checksum_ok

EGRUL_URL = "https://egrul.nalog.ru/"
EGRUL_RESULT_URL = "https://egrul.nalog.ru/search-result/"
HTTP_TIMEOUT = 30
RESULT_RETRIES = 4
RESULT_RETRY_DELAY = 1.5
FRESH_COMPANY_MONTHS = 12

INN_PATTERN = re.compile(r"^\d{10}(\d{2})?$")


class CheckError(ValueError):
    """Понятная ошибка входных параметров проверки."""


def validate_inn(inn: str) -> str:
    cleaned = re.sub(r"\s", "", str(inn))
    if not INN_PATTERN.match(cleaned):
        raise CheckError(f"ИНН «{inn}» должен содержать 10 или 12 цифр.")
    return cleaned


def _post_form(url: str, fields: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    return _open_json(request)


def _get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    return _open_json(request)


def _open_json(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise AgentError(f"Сервис ФНС вернул HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise AgentError(f"Не удалось подключиться к egrul.nalog.ru: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise AgentError("Сервис ФНС вернул некорректный ответ.") from exc


def fetch_egrul(inn: str, retries: int = RESULT_RETRIES) -> dict[str, Any]:
    """Запрашивает сведения ЕГРЮЛ/ЕГРИП по ИНН через сервис ФНС."""
    token_response = _post_form(EGRUL_URL, {"query": inn, "region": "", "PreventChromeAutocomplete": ""})
    token = str(token_response.get("t", "")).strip()
    if not token:
        raise AgentError("Сервис ФНС не выдал токен поиска (возможна временная блокировка, повторите позже).")
    last_error: AgentError | None = None
    for attempt in range(retries):
        if attempt:
            time.sleep(RESULT_RETRY_DELAY)
        try:
            result = _get_json(f"{EGRUL_RESULT_URL}{token}")
        except AgentError as exc:
            last_error = exc
            continue
        rows = result.get("rows")
        if isinstance(rows, list):
            return result
        last_error = AgentError("Сервис ФНС вернул неожиданный формат результата.")
    raise last_error or AgentError("Сервис ФНС не ответил в отведённое время.")


def parse_egrul_rows(rows: list[dict[str, Any]], inn: str) -> dict[str, Any] | None:
    """Выбирает строку с точным совпадением ИНН.

    Поля сервиса ФНС: n — полное наименование, c — краткое, o — ОГРН,
    i — ИНН, p — КПП, r — дата регистрации, e — дата прекращения (если есть),
    g — руководитель, rn — регион. Полного адреса в кратком ответе нет:
    он публикуется в выписке ЕГРЮЛ (PDF).
    """
    for row in rows:
        if re.sub(r"\s", "", str(row.get("i", ""))) == inn:
            return {
                "name": str(row.get("n", "")).strip(),
                "short_name": str(row.get("c", "")).strip(),
                "inn": inn,
                "ogrn": str(row.get("o", "")).strip(),
                "kpp": str(row.get("p", "")).strip(),
                "registered_on": str(row.get("r", "")).strip(),
                "terminated_on": str(row.get("e", "")).strip(),
                "head": str(row.get("g", "")).strip(),
                "region": str(row.get("rn", "")).strip(),
            }
    return None


def _parse_ru_date(value: str) -> date | None:
    match = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", value.strip())
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def build_risk_flags(company: dict[str, Any] | None, inn: str, today: date) -> list[dict[str, str]]:
    """Детерминированные признаки риска по данным ЕГРЮЛ."""
    flags: list[dict[str, str]] = []
    if company is None:
        flags.append(
            {
                "code": "not_found",
                "severity": "high",
                "comment": "ИНН не найден в ЕГРЮЛ/ЕГРИП: организация не зарегистрирована или прекратила деятельность.",
            }
        )
        return flags
    if company.get("terminated_on"):
        flags.append(
            {
                "code": "terminated",
                "severity": "high",
                "comment": f"Деятельность прекращена {company['terminated_on']}: сделки с таким контрагентом недопустимы.",
            }
        )
    registered = _parse_ru_date(company.get("registered_on", ""))
    if registered is not None:
        months = (today.year - registered.year) * 12 + today.month - registered.month
        if months < FRESH_COMPANY_MONTHS:
            flags.append(
                {
                    "code": "fresh_registration",
                    "severity": "medium",
                    "comment": f"Регистрация {company['registered_on']} — менее {FRESH_COMPANY_MONTHS} месяцев назад: повышенное внимание к предоплате.",
                }
            )
    return flags


def search_arbitration_cases(name: str, limit: int = 5) -> dict[str, Any]:
    """Выборочный поиск упоминаний на kad.arbitr.ru через Tavily (если есть ключ)."""
    if not os.getenv("TAVILY_API_KEY", "").strip():
        return {"performed": False, "reason": "TAVILY_API_KEY не задан", "cases_found": 0, "results": []}
    from agent import TavilySearch

    search = TavilySearch(domains=["kad.arbitr.ru"])
    found = search.search(f"{name} арбитражные дела", limit)
    return {
        "performed": True,
        "reason": "",
        "cases_found": len(found),
        "results": [{"title": item.title, "url": item.url} for item in found],
    }


def handle_check(payload: dict[str, Any]) -> dict[str, Any]:
    """Обработчик POST /check: проверка контрагента по ИНН."""
    if not isinstance(payload, dict):
        raise CheckError("Тело запроса должно быть JSON-объектом.")
    inn_raw = payload.get("inn")
    if inn_raw is None or not str(inn_raw).strip():
        raise CheckError("Поле inn обязательно (10 или 12 цифр).")
    inn = validate_inn(inn_raw)
    today = date.today()
    warnings: list[str] = []
    if not inn_checksum_ok(inn):
        warnings.append(f"ИНН {inn} не проходит контрольную сумму — вероятна опечатка, проверьте реквизиты.")

    demo = bool(payload.get("demo", False))
    if demo:
        company = {
            "name": "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ «ДЕМОНСТРАЦИОННЫЙ КОНТРАГЕНТ»",
            "short_name": "ООО «ДЕМО»",
            "inn": inn,
            "ogrn": "1027700132195",
            "kpp": "770701001",
            "registered_on": "15.01.2010",
            "terminated_on": "",
            "head": "ГЕНЕРАЛЬНЫЙ ДИРЕКТОР: Иванов Иван Иванович",
            "region": "Г.Москва",
        }
        kad = {"performed": False, "reason": "демонстрационный режим", "cases_found": 0, "results": []}
    else:
        rows = fetch_egrul(inn).get("rows", [])
        company = parse_egrul_rows(rows, inn)
        if company is None:
            kad = {"performed": False, "reason": "контрагент не найден в ЕГРЮЛ", "cases_found": 0, "results": []}
        else:
            kad = search_arbitration_cases(company["name"])

    risk_flags = build_risk_flags(company, inn, today)
    if demo:
        risk_flags = [
            {
                "code": "demo_mode",
                "severity": "low",
                "comment": "Демонстрационные данные: реальная проверка выполняется без флага demo.",
            }
        ]
    overall = "high" if any(f["severity"] == "high" for f in risk_flags) else (
        "medium" if any(f["severity"] == "medium" for f in risk_flags) else "low"
    )
    return {
        "status": "draft",
        "inn": inn,
        "company": company,
        "risk_level": overall,
        "risk_flags": risk_flags,
        "checks": {
            "egrul": "demo" if demo else "performed",
            "arbitration": kad,
            "sanctions": {"performed": False, "reason": "открытого бесплатного API санкционных списков нет"},
            "mass_registration": {"performed": False, "reason": "признак доступен в полной выписке ЕГРЮЛ (PDF), без сторонних библиотек не разбирается"},
        },
        "warnings": warnings,
        "note": "Проверка по открытым источникам не заменяет полноценную комплаенс-проверку: "
        "для сделок с высокой суммой дополнительно запросите выписку ЕГРЮЛ, бухгалтерскую отчётность "
        "и проверьте контрагента в картотеке арбитражных дел вручную.",
        "checked_on": datetime.now(timezone.utc).date().isoformat(),
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": demo,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка контрагента по ИНН (ЕГРЮЛ ФНС)")
    parser.add_argument("inn", help="ИНН контрагента (10 или 12 цифр)")
    parser.add_argument("--demo", action="store_true", help="Проверка без внешних запросов")
    args = parser.parse_args()
    load_dotenv()
    try:
        result = handle_check({"inn": args.inn, "demo": args.demo})
    except (AgentError, CheckError) as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
