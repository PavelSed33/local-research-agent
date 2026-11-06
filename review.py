#!/usr/bin/env python3
"""Проверка договоров: риски, отсутствующие условия и дословные цитаты.

Модуль не обращается к внешним источникам: анализируется только переданный
текст договора. Каждая цитата из результата проверяется на вхождение в текст
документа — это защита от выдуманных формулировок. Текст договора нигде
не сохраняется.

CLI:  python review.py договор.txt [--demo]
API:  POST /review (см. api.py)
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

MIN_DOCUMENT_CHARS = 20
MIN_QUOTE_CHARS = 10

REVIEW_CATEGORIES = (
    "предмет и существенные условия",
    "цена и порядок расчётов",
    "сроки и пролонгация",
    "ответственность и штрафы",
    "односторонние права",
    "изменение и расторжение",
    "подсудность и споры",
    "скрытые комиссии и доплаты",
)
SEVERITIES = ("high", "medium", "low")


def normalize_text(text: str) -> str:
    """Схлопывает пробельные символы для устойчивого сравнения цитат."""
    return re.sub(r"\s+", " ", text).strip()


def validate_review(result: dict[str, Any], document: str) -> list[str]:
    """Проверяет структуру результата и вхождение цитат в текст договора."""
    issues: list[str] = []
    if not isinstance(result.get("summary"), str) or not result["summary"].strip():
        issues.append("Пустое или отсутствующее поле summary.")
    risks = result.get("risks")
    if not isinstance(risks, list):
        issues.append("Поле risks должно быть списком.")
        risks = []
    corpus = normalize_text(document)
    for index, risk in enumerate(risks, start=1):
        if not isinstance(risk, dict):
            issues.append(f"Риск №{index} не является объектом.")
            continue
        if not str(risk.get("category", "")).strip():
            issues.append(f"Риск №{index}: пустая категория.")
        if risk.get("severity") not in SEVERITIES:
            issues.append(f"Риск №{index}: severity должен быть одним из {SEVERITIES}.")
        quote = str(risk.get("quote", "")).strip()
        if len(quote) < MIN_QUOTE_CHARS:
            issues.append(f"Риск №{index}: цитата короче {MIN_QUOTE_CHARS} символов.")
        elif normalize_text(quote) not in corpus:
            issues.append(f"Риск №{index}: цитата не найдена в тексте договора.")
        if not str(risk.get("comment", "")).strip():
            issues.append(f"Риск №{index}: пустой комментарий.")
    missing = result.get("missing_clauses", [])
    if not isinstance(missing, list) or any(not isinstance(item, str) for item in missing):
        issues.append("Поле missing_clauses должно быть списком строк.")
    return issues


def build_review_prompt(document: str) -> tuple[str, str]:
    system = (
        "Ты юрист-аналитик, проверяющий договор по праву РФ. Анализируй только переданный текст договора. "
        "Верни только JSON без пояснений со структурой: "
        '{"summary": "общий вывод в 2-3 предложениях", '
        '"risks": [{"category": "категория риска", "severity": "high|medium|low", '
        '"quote": "точная цитата из договора", "comment": "в чём риск", "recommendation": "что изменить"}], '
        '"missing_clauses": ["отсутствующее существенное условие"]}. '
        "Проверь категории: " + "; ".join(REVIEW_CATEGORIES) + ". "
        "Цитаты приводи дословно из текста договора, не сокращай и не перефразируй. "
        "Не выдумывай риски и условия, которых нет в тексте. Если категория не вызывает замечаний, не включай её. "
        "Не выдавай результат за индивидуальную юридическую консультацию."
    )
    user = f"Проверь договор.\n\nТекст договора:\n\n{document}"
    return system, user


def review_contract(model: LanguageModel, document: str) -> tuple[dict[str, Any], bool]:
    """Проверяет договор; при ошибках структуры или цитат — одна попытка исправления."""
    system, user = build_review_prompt(document)
    result = extract_json(model.complete(system, user))
    issues = validate_review(result, document)
    if not issues:
        return result, False
    repaired = extract_json(
        model.complete(
            "Ты исправляешь JSON-результат проверки договора. Устрани все перечисленные проблемы, "
            "не добавляй новых рисков и фактов. Цитаты должны дословно входить в текст договора. "
            "Верни только исправленный JSON.",
            "Проблемы:\n- " + "\n- ".join(issues)
            + f"\n\nРезультат:\n{json.dumps(result, ensure_ascii=False)}\n\nТекст договора:\n\n{document}",
        )
    )
    remaining = validate_review(repaired, document)
    if remaining:
        raise AgentError("Проверка договора не прошла контроль после исправления: " + " ".join(remaining))
    return repaired, True


class DemoReviewModel:
    """Демонстрационная проверка договора без внешних вызовов."""

    def complete(self, system: str, user: str) -> str:
        document = user.split("Текст договора:", 1)[-1].strip()
        quote = normalize_text(document)[:120].strip() or "условный текст договора"
        return json.dumps(
            {
                "summary": "Демонстрационная проверка договора: реальный анализ рисков выполняется при подключённой языковой модели.",
                "risks": [
                    {
                        "category": "демонстрационный режим",
                        "severity": "low",
                        "quote": quote,
                        "comment": "Цитата взята из начала документа, чтобы проверить механизм контроля цитат.",
                        "recommendation": "Подключите LLM_API_KEY и LLM_MODEL для реальной проверки.",
                    }
                ],
                "missing_clauses": [],
            },
            ensure_ascii=False,
        )


def handle_review(payload: dict[str, Any]) -> dict[str, Any]:
    """Валидирует запрос и выполняет проверку договора."""
    document = payload.get("document", "")
    if not isinstance(document, str) or len(document.strip()) < MIN_DOCUMENT_CHARS:
        raise ValueError(f"Поле «document» обязательно: текст договора от {MIN_DOCUMENT_CHARS} символов.")
    demo = bool(payload.get("demo", False))
    model: LanguageModel = DemoReviewModel() if demo else OpenAICompatibleModel()
    result, repaired = review_contract(model, document.strip())
    return {
        "summary": result["summary"].strip(),
        "risks": result.get("risks", []),
        "missing_clauses": result.get("missing_clauses", []),
        "checked_on": datetime.now(timezone.utc).date().isoformat(),
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": demo,
        "review_repaired": repaired,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка договора: риски и отсутствующие условия")
    parser.add_argument("file", type=Path, help="Текстовый файл с договором в кодировке UTF-8")
    parser.add_argument("--demo", action="store_true", help="Проверка без API-ключей и внешних запросов")
    args = parser.parse_args()
    if not args.file.exists():
        print(f"Ошибка: файл не найден: {args.file}", file=sys.stderr)
        return 1
    load_dotenv()
    document = args.file.read_text(encoding="utf-8")
    try:
        result = handle_review({"document": document, "demo": args.demo})
    except (AgentError, ValueError) as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
