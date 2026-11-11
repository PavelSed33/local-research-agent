#!/usr/bin/env python3
"""Маршрутизация входящих юридических запросов (triage).

Один вызов модели классифицирует обращение сотрудника: категория, срочность,
уровень риска и рекомендуемый эндпоинт агента. Код проверяет, что значения
— из допустимых перечислений: «галлюцинированная» категория не пройдёт.
Результат всегда имеет status=draft: распределение проверяет человек.

CLI:  python triage.py обращение.txt [--demo]
API:  POST /triage (см. api.py)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent import (
    LEGAL_DISCLAIMER,
    AgentError,
    LanguageModel,
    OpenAICompatibleModel,
    extract_json,
    load_dotenv,
)
from review import normalize_text

MIN_TEXT_CHARS = 10

CATEGORIES = ("contract", "dispute", "compliance", "general")
LEVELS = ("high", "medium", "low")
ENDPOINTS = ("/review", "/extract", "/check", "/penalty", "/timeline", "/draft", "/ask")

# Какой эндпоинт обычно подходит под категорию — подсказка в промпте и контроль.
CATEGORY_ENDPOINTS: dict[str, tuple[str, ...]] = {
    "contract": ("/review", "/extract", "/draft"),
    "dispute": ("/timeline", "/penalty", "/draft", "/ask"),
    "compliance": ("/check", "/ask"),
    "general": ("/ask",),
}


class TriageError(ValueError):
    """Понятная ошибка входных данных маршрутизации."""


def build_triage_prompt(text: str) -> tuple[str, str]:
    system = (
        "Ты юрист, распределяющий входящие запросы сотрудников компании. "
        "Верни только JSON: {\"category\": \"contract|dispute|compliance|general\", "
        "\"urgency\": \"high|medium|low\", \"risk\": \"high|medium|low\", "
        "\"summary\": \"суть запроса одним предложением\", "
        "\"recommended_endpoint\": \"один из: /review, /extract, /check, /penalty, /timeline, /draft, /ask\"}. "
        "contract — вопросы по договорам (проверка, реквизиты, проекты документов); "
        "dispute — споры, просрочки, претензии, суды; "
        "compliance — проверка контрагентов, санкции, регуляторные риски; "
        "general — всё остальное. "
        "urgency high — есть дедлайн до 3 дней, угроза суда или блокировки; "
        "risk high — возможны существенные финансовые или процессуальные потери. "
        "Не выдумывай факты, оценивай только по тексту обращения."
    )
    user = f"Классифицируй обращение:\n\n{text}"
    return system, user


def validate_triage(result: dict[str, Any]) -> list[str]:
    """Проверяет, что классификация состоит из допустимых значений."""
    issues: list[str] = []
    category = str(result.get("category", ""))
    if category not in CATEGORIES:
        issues.append(f"category должен быть одним из: {', '.join(CATEGORIES)}.")
    for field in ("urgency", "risk"):
        if str(result.get(field, "")) not in LEVELS:
            issues.append(f"{field} должен быть одним из: {', '.join(LEVELS)}.")
    endpoint = str(result.get("recommended_endpoint", ""))
    if endpoint not in ENDPOINTS:
        issues.append(f"recommended_endpoint должен быть одним из: {', '.join(ENDPOINTS)}.")
    elif category in CATEGORY_ENDPOINTS and endpoint not in CATEGORY_ENDPOINTS[category]:
        issues.append(
            f"Эндпоинт {endpoint} не подходит под категорию {category} "
            f"(ожидается один из: {', '.join(CATEGORY_ENDPOINTS[category])})."
        )
    if len(str(result.get("summary", "")).strip()) < MIN_TEXT_CHARS:
        issues.append("summary пустое или слишком короткое.")
    return issues


def triage_request(model: LanguageModel, text: str) -> tuple[dict[str, Any], bool]:
    """Классифицирует обращение; при невалидном ответе — одна попытка исправления."""
    system, user = build_triage_prompt(text)
    result = extract_json(model.complete(system, user))
    issues = validate_triage(result)
    if not issues:
        return result, False
    repaired = extract_json(
        model.complete(
            "Ты исправляешь JSON-классификацию юридического запроса. Устрани все перечисленные "
            "проблемы, используй только допустимые значения. Верни только исправленный JSON.",
            "Проблемы:\n- " + "\n- ".join(issues)
            + f"\n\nРезультат:\n{json.dumps(result, ensure_ascii=False)}\n\n"
            + user,
        )
    )
    remaining = validate_triage(repaired)
    if remaining:
        raise AgentError("Классификация не прошла контроль после исправления: " + " ".join(remaining))
    return repaired, True


class DemoTriageModel:
    """Демонстрационная классификация по ключевым словам, без внешних вызовов."""

    RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("dispute", "/timeline", ("просроч", "неустойк", "претензи", "иск", "суд", "задолжен", "не платит", "не оплатил")),
        ("compliance", "/check", ("контрагент", "банкрот", "санкци", "проверить фирму", "инн", "благонадёжн", "надёжн")),
        ("contract", "/review", ("договор", "соглашени", "дополнительн", "аренд", "поставк", "подписать")),
    )

    def complete(self, system: str, user: str) -> str:
        text = user.split("Классифицируй обращение:", 1)[-1].strip()
        lowered = normalize_text(text).lower()
        category, endpoint = "general", "/ask"
        for candidate, candidate_endpoint, keywords in self.RULES:
            if any(keyword in lowered for keyword in keywords):
                category, endpoint = candidate, candidate_endpoint
                break
        urgent_words = ("срочно", "дедлайн", "завтра", "сегодня", "блокировк", "арест")
        risk_words = ("суд", "расторжен", "штраф", "убытк", "млн", "блокировк", "арест")
        urgency = "high" if any(word in lowered for word in urgent_words) else "medium"
        risk = "high" if any(word in lowered for word in risk_words) else "medium"
        summary = re.sub(r"\s+", " ", text).strip()[:150]
        return json.dumps(
            {
                "category": category,
                "urgency": urgency,
                "risk": risk,
                "summary": summary,
                "recommended_endpoint": endpoint,
            },
            ensure_ascii=False,
        )


def handle_triage(payload: dict[str, Any]) -> dict[str, Any]:
    """Обработчик POST /triage: категория, срочность, риск и маршрут обращения."""
    if not isinstance(payload, dict):
        raise TriageError("Тело запроса должно быть JSON-объектом.")
    text = str(payload.get("text", "")).strip()
    if len(text) < MIN_TEXT_CHARS:
        raise TriageError(f"Поле text обязательно: текст обращения (от {MIN_TEXT_CHARS} символов).")
    demo = bool(payload.get("demo", False))
    model: LanguageModel = DemoTriageModel() if demo else OpenAICompatibleModel()
    result, repaired = triage_request(model, text)
    return {
        "status": "draft",
        "category": result["category"],
        "urgency": result["urgency"],
        "risk": result["risk"],
        "summary": result["summary"].strip(),
        "recommended_endpoint": result["recommended_endpoint"],
        "note": "Маршрутизация — рекомендация, а не решение: окончательное распределение за юристом.",
        "checked_on": datetime.now(timezone.utc).date().isoformat(),
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": demo,
        "triage_repaired": repaired,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Маршрутизация юридических запросов (triage)")
    parser.add_argument("file", type=Path, help="Файл с текстом обращения (.txt)")
    parser.add_argument("--demo", action="store_true", help="Работа без API-ключей и внешних запросов")
    args = parser.parse_args()
    if not args.file.exists():
        print(f"Ошибка: файл не найден: {args.file}", file=sys.stderr)
        return 1
    payload: dict[str, Any] = {"text": args.file.read_text(encoding="utf-8"), "demo": args.demo}
    load_dotenv()
    try:
        result = handle_triage(payload)
    except (AgentError, ValueError) as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
