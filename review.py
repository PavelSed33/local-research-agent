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
from doctext import ensure_document_transfer_allowed, document_fingerprint, quote_span, read_document_file, resolve_document

MIN_DOCUMENT_CHARS = 20
MAX_DOCUMENT_CHARS = 500_000
MIN_QUOTE_CHARS = 10

RISK_FIELDS = ("category", "severity", "quote", "comment", "recommendation")


def safe_review_risk(candidate: Any) -> dict[str, Any] | None:
    """Санитизирует один риск: только известные поля, валидный severity, непустая цитата.

    Кандидат, который нельзя привести к корректной форме, отбрасывается (None).
    """
    if not isinstance(candidate, dict):
        return None
    risk: dict[str, Any] = {}
    for field in RISK_FIELDS:
        value = candidate.get(field, "")
        risk[field] = str(value).strip() if value is not None else ""
    if not risk["quote"]:
        return None
    if risk["category"] not in REVIEW_CATEGORIES and len(risk["category"]) > 200:
        return None
    if risk["severity"] not in SEVERITIES:
        risk["severity"] = "medium"
    if not risk["category"]:
        risk["category"] = "прочее"
    return risk


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
        if not isinstance(risk.get("category"), str) or risk["category"] not in REVIEW_CATEGORIES:
            issues.append(f"Риск №{index}: категория должна быть из разрешённого перечня.")
        if risk.get("severity") not in SEVERITIES:
            issues.append(f"Риск №{index}: severity должен быть одним из {SEVERITIES}.")
        quote = str(risk.get("quote", "")).strip()
        if len(quote) < MIN_QUOTE_CHARS:
            issues.append(f"Риск №{index}: цитата короче {MIN_QUOTE_CHARS} символов.")
        elif normalize_text(quote) not in corpus:
            issues.append(f"Риск №{index}: цитата не найдена в тексте договора.")
        if not isinstance(risk.get("recommendation"), str) or not risk["recommendation"].strip():
            issues.append(f"Риск №{index}: recommendation обязательно и не должно быть пустым.")
        if not str(risk.get("comment", "")).strip():
            issues.append(f"Риск №{index}: пустой комментарий.")
    missing = result.get("missing_clauses", [])
    if not isinstance(missing, list) or any(not isinstance(item, str) for item in missing):
        issues.append("Поле missing_clauses должно быть списком строк.")
    return issues


def build_review_prompt(document: str, demo: bool = False) -> str:
    """Промпт для модели: строгая схема JSON и требование цитат из текста."""
    demo_note = (
        "\n\nРЕЖИМ ДЕМОНСТРАЦИИ: верни 2 риска и 2 отсутствующих условия "
        "с короткими цитатами из текста выше."
        if demo
        else ""
    )
    return f"""Ты юрист, проверяющий договор перед подписанием. Проанализируй договор и верни ТОЛЬКО JSON вида:

{{
  "summary": "2-3 предложения: что за договор и главный вывод",
  "risks": [
    {{
      "category": "одна из категорий: {'; '.join(REVIEW_CATEGORIES)}",
      "severity": "high|medium|low",
      "quote": "дословная цитата из договора (10-300 символов)",
      "comment": "чём риск",
      "recommendation": "как защититься или что уточнить"
    }}
  ],
  "missing_clauses": ["условие, которого не хватает", ...]
}}

Правила:
- 3-6 рисков, отсортированных от high к low.
- Каждый quote — ДОСЛОВНАЯ цитата из договора (сохрани пунктуацию), иначе результат будет отклонён.
- severity: high — деньги/ответственность без защиты; medium — неоднозначность; low — косметика.
- Без комментариев вне JSON.

ДОГОВОР:
\"\"\"
{document}
\"\"\"{demo_note}"""


def review_contract(model: LanguageModel, document: str, demo: bool = False) -> tuple[dict[str, Any], bool]:
    """Один проход проверки с валидацией и одной попыткой починки JSON."""
    prompt = build_review_prompt(document, demo=demo)
    response = model.complete(prompt, "")
    result = extract_json(response)
    issues = validate_review(result, document)
    repaired = False
    if issues:
        repair_prompt = (
            "Исправь JSON. Проблемы: "
            + "; ".join(issues[:5])
            + ". Верни полный корректный JSON с теми же разделами."
        )
        try:
            result = extract_json(model.complete(repair_prompt, ""))
            remaining = validate_review(result, document)
            if remaining:
                raise AgentError("Исправленный анализ договора не прошёл проверку структуры и цитат.")
            repaired = True
        except AgentError:
            raise
    # Model output is untrusted: only schema-approved fields can leave this boundary.
    cleaned = {
        "summary": result.get("summary", ""),
        "missing_clauses": result.get("missing_clauses", []),
        "risks": [],
        "verified": False, "lawyer_reviewed": False,
    }
    for risk in result.get("risks", []):
        if not isinstance(risk, dict):
            continue
        safe = {key: risk[key] for key in RISK_FIELDS if key in risk}
        safe.update(verified=False, lawyer_reviewed=False, legal_basis=[],
                    basis_status="no_legal_evidence")
        cleaned["risks"].append(safe)
    return cleaned, repaired


class DemoReviewModel:
    """Детерминированный ответ для проверки без API-ключей."""

    def complete(self, prompt: str, user: str = "") -> str:
        return json.dumps(
            {
                "summary": "Демонстрационный анализ: договор короткий, типовые риски не проверялись по внешним источникам.",
                "risks": [
                    {
                        "category": "ответственность и штрафы",
                        "severity": "high",
                        "quote": normalize_text(_demo_quote(prompt)),
                        "comment": "В демонстрационном режиме риск сформулирован по первому фрагменту текста.",
                        "recommendation": "Проверьте реальную модель с API-ключом и полным текстом договора.",
                    }
                ],
                "missing_clauses": ["Порядок приёмки по количеству и качеству"],
            },
            ensure_ascii=False,
        )


def _demo_quote(prompt: str) -> str:
    """Первый осмысленный фрагмент договора для демонстрационной цитаты."""
    document = prompt.split('"""')[1] if '"""' in prompt else prompt
    words = document.split()
    return " ".join(words[:20])


def handle_review(payload: dict[str, Any]) -> dict[str, Any]:
    """Валидирует запрос и выполняет проверку договора."""
    document = resolve_document(payload)
    if len(document) < MIN_DOCUMENT_CHARS:
        raise ValueError(f"Текст договора слишком короткий: нужно от {MIN_DOCUMENT_CHARS} символов.")
    if len(document) > MAX_DOCUMENT_CHARS:
        raise ValueError(f"Текст договора превышает допустимый размер ({MAX_DOCUMENT_CHARS} символов).")
    demo = payload.get("demo", False)
    if type(demo) is not bool:
        raise ValueError("Поле demo должно быть логическим значением.")
    model: LanguageModel = DemoReviewModel() if demo else OpenAICompatibleModel()
    result, repaired = review_contract(model, document.strip(), demo=demo)
    risks = []
    for risk in result.get("risks", []):
        span = quote_span(document, str(risk.get("quote", "")))
        risks.append({**risk, "document_span": span, "verified": False, "lawyer_reviewed": False})
    return {
        "status": "draft",
        "verified": False,
        "lawyer_reviewed": False,
        "summary": result["summary"].strip(),
        "risks": risks,
        "missing_clauses": result.get("missing_clauses", []),
        "checked_on": datetime.now(timezone.utc).date().isoformat(),
        "document_sha256": document_fingerprint(document),
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": demo,
        "review_repaired": repaired,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка договора на риски")
    parser.add_argument("file", type=Path, help="Файл договора: .txt (UTF-8) или .docx")
    parser.add_argument("--demo", action="store_true", help="Демонстрация без API-ключей")
    args = parser.parse_args()
    if not args.file.is_file():
        print(f"Ошибка: файл не найден: {args.file}", file=sys.stderr)
        return 1
    load_dotenv()
    try:
        document = read_document_file(args.file.read_bytes(), args.file.name)
        result = handle_review({"document": document, "demo": args.demo})
    except (AgentError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
