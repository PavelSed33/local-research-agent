#!/usr/bin/env python3
"""Скоринг договора: сводная оценка риска 0–100 поверх проверки /review.

Риски и недостающие условия находит модель (review.review_contract с контролем
цитат), а итоговую оценку считает код по фиксированным весам: число
воспроизводимо и не зависит от «настроения» модели. Одна цифра отвечает
на вопрос руководства «можно подписывать или нет».

Веса: high −20, medium −10, low −5 (суммарно не более −70 за риски);
каждое недостающее существенное условие −15 (не более −45).

CLI:  python scoring.py договор.docx [--demo]
API:  POST /scoring (см. api.py)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent import LEGAL_DISCLAIMER, AgentError, LanguageModel, OpenAICompatibleModel, load_dotenv
from doctext import read_document_file, resolve_document, ensure_document_transfer_allowed
from review import MIN_DOCUMENT_CHARS, DemoReviewModel, review_contract

BASE_SCORE = 100
SEVERITY_WEIGHTS = {"high": 20, "medium": 10, "low": 5}
MAX_RISK_DEDUCTION = 70
MISSING_CLAUSE_WEIGHT = 15
MAX_MISSING_DEDUCTION = 45

VERDICTS = (
    (80, "green", "Можно подписывать с учётом правок: критичных рисков нет."),
    (50, "yellow", "Договор требует доработки: есть существенные риски или пробелы."),
    (0, "red", "Подписывать не рекомендуется: критичные риски и/или отсутствуют существенные условия."),
)


class ScoringError(ValueError):
    """Понятная ошибка входных данных скоринга."""


def compute_score(risks: list[dict[str, Any]], missing_clauses: list[str]) -> dict[str, Any]:
    """Детерминированно сводит риски и пробелы в оценку 0–100."""
    deductions: list[dict[str, Any]] = []
    risk_deduction = 0
    for risk in risks:
        severity = risk.get("severity", "low")
        weight = SEVERITY_WEIGHTS.get(severity, SEVERITY_WEIGHTS["low"])
        risk_deduction += weight
        deductions.append(
            {
                "reason": f"Риск ({severity}): {str(risk.get('category', '')).strip() or 'без категории'}",
                "weight": -weight,
            }
        )
    risk_capped = min(risk_deduction, MAX_RISK_DEDUCTION)
    missing_deduction = min(len(missing_clauses) * MISSING_CLAUSE_WEIGHT, MAX_MISSING_DEDUCTION)
    for clause in missing_clauses:
        deductions.append({"reason": f"Нет существенного условия: {clause}", "weight": -MISSING_CLAUSE_WEIGHT})
    score = max(0, BASE_SCORE - risk_capped - missing_deduction)
    for threshold, color, text in VERDICTS:
        if score >= threshold:
            verdict, verdict_color = text, color
            break
    return {
        "score": score,
        "verdict": verdict,
        "verdict_color": verdict_color,
        "deductions": deductions,
        "risk_deduction": risk_capped,
        "risk_deduction_capped": risk_deduction > MAX_RISK_DEDUCTION,
        "missing_deduction": missing_deduction,
        "weights": {
            "base": BASE_SCORE,
            "severity": SEVERITY_WEIGHTS,
            "max_risk_deduction": MAX_RISK_DEDUCTION,
            "missing_clause": MISSING_CLAUSE_WEIGHT,
            "max_missing_deduction": MAX_MISSING_DEDUCTION,
        },
    }


def handle_scoring(payload: dict[str, Any]) -> dict[str, Any]:
    """Обработчик POST /scoring: проверка договора + сводная оценка."""
    document = resolve_document(payload)
    if len(document) < MIN_DOCUMENT_CHARS:
        raise ScoringError(f"Текст договора слишком короткий: нужно от {MIN_DOCUMENT_CHARS} символов.")
    demo = payload.get("demo", False)
    if not isinstance(demo, bool):
        raise ScoringError("Поле demo должно быть логическим значением.")
    if not demo:
        ensure_document_transfer_allowed()
    model: LanguageModel = DemoReviewModel() if demo else OpenAICompatibleModel()
    review, repaired = review_contract(model, document.strip(), demo=demo)
    scoring = compute_score(review.get("risks", []), review.get("missing_clauses", []))
    return {
        "status": "draft",
        **scoring,
        "summary": review["summary"].strip(),
        "risks": review.get("risks", []),
        "missing_clauses": review.get("missing_clauses", []),
        "note": "Оценка считается кодом по фиксированным весам (поле weights), "
        "риски и пробелы находит модель с дословным контролем цитат. "
        "Скоринг — ориентир для решения, а не замена юридического анализа.",
        "checked_on": datetime.now(timezone.utc).date().isoformat(),
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": demo,
        "review_repaired": repaired,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Скоринг договора: сводная оценка риска 0–100")
    parser.add_argument("file", type=Path, help="Файл с договором: .txt (UTF-8) или .docx")
    parser.add_argument("--demo", action="store_true", help="Работа без API-ключей и внешних запросов")
    args = parser.parse_args()
    if not args.file.exists():
        print(f"Ошибка: файл не найден: {args.file}", file=sys.stderr)
        return 1
    load_dotenv()
    try:
        document = read_document_file(args.file.read_bytes(), args.file.name)
        result = handle_scoring({"document": document, "demo": args.demo})
    except (AgentError, ValueError) as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
