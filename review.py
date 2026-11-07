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
from pathlib import Path
from typing import Any

from agent import (
    LEGAL_DISCLAIMER,
    AgentError,
    LanguageModel,
    OpenAICompatibleModel,
    current_check_date,
    extract_json,
    load_dotenv,
)
from doctext import ensure_document_transfer_allowed, parse_demo_flag, read_document_path, resolve_document

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


MAX_DOCUMENT_CHARS = 200_000


def safe_quote_fields(record: dict, fields: tuple[str, ...]) -> dict:
    """Keep only declared string fields after validation; flags belong to the program."""
    return {**{key: record[key] for key in fields if isinstance(record.get(key), str)},
            'quote_checked': True, 'verified': False, 'lawyer_reviewed': False}


def safe_review_risk(record: dict) -> dict:
    return {**safe_quote_fields(record, ('category', 'severity', 'quote', 'comment', 'recommendation')),
            'legal_basis': [], 'basis_status': 'no_legal_evidence'}


def safe_review_result(result: dict) -> dict:
    return {'summary': result['summary'], 'risks': [safe_review_risk(risk) for risk in result['risks']],
            'missing_clauses': result.get('missing_clauses', []), 'verified': False, 'lawyer_reviewed': False}


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
        category = risk.get("category")
        if not isinstance(category, str) or not category.strip():
            issues.append(f"Риск №{index}: category должна быть непустой строкой.")
        elif category.strip() not in REVIEW_CATEGORIES and category.strip() != "демонстрационный режим":
            issues.append(f"Риск №{index}: неизвестная категория «{category.strip()}».")
        if risk.get("severity") not in SEVERITIES:
            issues.append(f"Риск №{index}: severity должен быть одним из {SEVERITIES}.")
        quote_value = risk.get("quote")
        if not isinstance(quote_value, str):
            issues.append(f"Риск №{index}: quote должна быть строкой.")
            quote = ""
        else:
            quote = quote_value.strip()
        if len(quote) < MIN_QUOTE_CHARS:
            issues.append(f"Риск №{index}: цитата короче {MIN_QUOTE_CHARS} символов.")
        elif normalize_text(quote) not in corpus:
            issues.append(f"Риск №{index}: цитата не найдена в тексте договора.")
        for field in ("comment", "recommendation"):
            value = risk.get(field)
            if not isinstance(value, str) or not value.strip():
                issues.append(f"Риск №{index}: {field} должна быть непустой строкой.")
    missing = result.get("missing_clauses", [])
    if not isinstance(missing, list) or any(not isinstance(item, str) or not item.strip() for item in missing):
        issues.append("Поле missing_clauses должно быть списком непустых строк.")
    elif len({normalize_text(item).casefold() for item in missing}) != len(missing):
        issues.append("Поле missing_clauses содержит повторяющиеся пункты.")
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
        return safe_review_result(result), False
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
    return safe_review_result(repaired), True


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
    document = resolve_document(payload)
    if not MIN_DOCUMENT_CHARS <= len(document) <= MAX_DOCUMENT_CHARS:
        raise ValueError(f"Текст договора должен содержать от {MIN_DOCUMENT_CHARS} до {MAX_DOCUMENT_CHARS} символов.")
    demo = parse_demo_flag(payload)
    if not demo:
        ensure_document_transfer_allowed()
    model: LanguageModel = DemoReviewModel() if demo else OpenAICompatibleModel()
    result, repaired = review_contract(model, document.strip())
    return {
        "status": "draft",
        "verified": False,
        "lawyer_reviewed": False,
        "summary": result["summary"].strip(),
        "risks": result.get("risks", []),
        "missing_clauses": result.get("missing_clauses", []),
        "checked_on": current_check_date(),
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": demo,
        "review_repaired": repaired,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка договора: риски и отсутствующие условия")
    parser.add_argument("file", type=Path, help="Файл с договором: .txt (UTF-8) или .docx")
    parser.add_argument("--demo", action="store_true", help="Проверка без API-ключей и внешних запросов")
    args = parser.parse_args()
    if not args.file.exists():
        print(f"Ошибка: файл не найден: {args.file}", file=sys.stderr)
        return 1
    load_dotenv()
    try:
        document = read_document_path(args.file)
        result = handle_review({"document": document, "demo": args.demo})
    except (AgentError, ValueError) as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

