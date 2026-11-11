#!/usr/bin/env python3
"""Протокол разногласий к договору: таблица «редакция А / редакция Б / предложение».

Техническая сверка редакций выполняется кодом (redline.diff_documents),
затем модель превращает спорные пункты в строки протокола с комментарием.
Код проверяет дословность редакций: «редакция А» обязана входить в текст
версии А, «редакция Б» — в текст версии Б. Выдуманная редакция не пройдёт.

Результат — Markdown-таблица, готовая к экспорту в DOCX через POST /export.

CLI:  python protocol.py старая.txt новая.txt [--demo]
API:  POST /protocol (см. api.py)
"""

from __future__ import annotations

import argparse
import json
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
from doctext import read_document_file, resolve_document
from redline import diff_documents
from review import MIN_DOCUMENT_CHARS, normalize_text

MIN_QUOTE_CHARS = 5
MAX_DISPUTED_FOR_PROMPT = 60


class ProtocolError(ValueError):
    """Понятная ошибка входных данных протокола разногласий."""


def collect_disputed(changes: list[dict[str, str]]) -> list[dict[str, str]]:
    """Сводит изменения к спорным парам «было/стало» для промпта."""
    disputed: list[dict[str, str]] = []
    for change in changes:
        if change["type"] == "changed":
            disputed.append({"kind": "changed", "edition_a": change["old"], "edition_b": change["new"]})
        elif change["type"] == "removed":
            disputed.append({"kind": "removed", "edition_a": change["text"], "edition_b": ""})
        elif change["type"] == "added":
            disputed.append({"kind": "added", "edition_a": "", "edition_b": change["text"]})
    return disputed


def build_protocol_prompt(disputed: list[dict[str, str]]) -> tuple[str, str]:
    system = (
        "Ты юрист, готовящий протокол разногласий к договору по списку спорных пунктов. "
        "Верни только JSON: {\"rows\": [{\"point\": \"номер или тема пункта\", "
        "\"edition_a\": \"дословная редакция из колонки «Редакция А» исходных данных\", "
        "\"edition_b\": \"дословная редакция из колонки «Редакция Б» исходных данных\", "
        "\"comment\": \"краткое юридическое предложение: принять, отклонить, переформулировать и почему\"}]}. "
        "Каждый спорный пункт — одна строка. Тексты edition_a и edition_b копируй дословно из исходных данных, "
        "ничего не сокращай и не переписывай; пустая строка означает отсутствие пункта в этой редакции. "
        "Комментарий пиши сам, кратко и по существу риска."
    )
    lines = ["Спорные пункты (номер | вид | редакция А | редакция Б):"]
    for index, item in enumerate(disputed, start=1):
        lines.append(f"{index} | {item['kind']} | {item['edition_a']} | {item['edition_b']}")
    return system, "\n".join(lines)


def validate_protocol(result: dict[str, Any], document_a: str, document_b: str) -> list[str]:
    """Проверяет структуру строк и дословность редакций."""
    issues: list[str] = []
    rows = result.get("rows", [])
    if not isinstance(rows, list) or not rows:
        return ["Поле rows пустое или не является списком."]
    corpus_a = normalize_text(document_a)
    corpus_b = normalize_text(document_b)
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            issues.append(f"Строка №{index}: не объект.")
            continue
        edition_a = str(row.get("edition_a", "")).strip()
        edition_b = str(row.get("edition_b", "")).strip()
        if not edition_a and not edition_b:
            issues.append(f"Строка №{index}: обе редакции пустые.")
        if edition_a and (
            len(edition_a) < MIN_QUOTE_CHARS or normalize_text(edition_a) not in corpus_a
        ):
            issues.append(f"Строка №{index}: редакция А не найдена дословно в версии А.")
        if edition_b and (
            len(edition_b) < MIN_QUOTE_CHARS or normalize_text(edition_b) not in corpus_b
        ):
            issues.append(f"Строка №{index}: редакция Б не найдена дословно в версии Б.")
        if len(str(row.get("comment", "")).strip()) < MIN_QUOTE_CHARS:
            issues.append(f"Строка №{index}: пустой комментарий.")
    return issues


def make_protocol(model: LanguageModel, disputed: list[dict[str, str]], document_a: str, document_b: str) -> tuple[dict[str, Any], bool]:
    """Готовит строки протокола; при ошибках — одна попытка исправления."""
    system, user = build_protocol_prompt(disputed)
    result = extract_json(model.complete(system, user))
    issues = validate_protocol(result, document_a, document_b)
    if not issues:
        return result, False
    repaired = extract_json(
        model.complete(
            "Ты исправляешь JSON-протокол разногласий. Устрани все перечисленные проблемы, "
            "редакции копируй дословно из исходных данных. Верни только исправленный JSON.",
            "Проблемы:\n- " + "\n- ".join(issues)
            + f"\n\nРезультат:\n{json.dumps(result, ensure_ascii=False)}\n\n"
            + user,
        )
    )
    remaining = validate_protocol(repaired, document_a, document_b)
    if remaining:
        raise AgentError("Протокол разногласий не прошёл контроль после исправления: " + " ".join(remaining))
    return repaired, True


def protocol_table(rows: list[dict[str, Any]], party_a: str, party_b: str) -> str:
    """Собирает Markdown-таблицу протокола разногласий."""
    lines = [
        "# ПРОТОКОЛ РАЗНОГЛАСИЙ",
        "",
        f"к проекту договора: редакция «{party_a}» против редакции «{party_b}»",
        "",
        f"| Пункт | Редакция «{party_a}» | Редакция «{party_b}» | Предложение |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        point = str(row.get("point", "")).replace("|", "/")
        edition_a = str(row.get("edition_a", "")).replace("|", "/") or "—"
        edition_b = str(row.get("edition_b", "")).replace("|", "/") or "—"
        comment = str(row.get("comment", "")).replace("|", "/")
        lines.append(f"| {point} | {edition_a} | {edition_b} | {comment} |")
    lines += [
        "",
        "Настоящий протокол составлен в [число] экземплярах.",
        "",
        f"«{party_a}»: [должность, ФИО]  ______________",
        f"«{party_b}»: [должность, ФИО]  ______________",
    ]
    return "\n".join(lines)


class DemoProtocolModel:
    """Демонстрационный протокол: комментарии-заглушки, редакции из diff."""

    def complete(self, system: str, user: str) -> str:
        rows = []
        for line in user.splitlines()[1:]:
            parts = [part.strip() for part in line.split(" | ")]
            if len(parts) != 4:
                continue
            number, kind, edition_a, edition_b = parts
            if kind == "changed":
                comment = "Демонстрационный комментарий: предложить компромиссную формулировку."
            elif kind == "removed":
                comment = "Демонстрационный комментарий: настаиваем на сохранении пункта."
            else:
                comment = "Демонстрационный комментарий: новый пункт требует правовой оценки."
            rows.append(
                {
                    "point": f"Спорный пункт №{number}",
                    "edition_a": edition_a,
                    "edition_b": edition_b,
                    "comment": comment,
                }
            )
        return json.dumps({"rows": rows}, ensure_ascii=False)


def handle_protocol(payload: dict[str, Any]) -> dict[str, Any]:
    """Обработчик POST /protocol: протокол разногласий по двум редакциям."""
    if not isinstance(payload, dict):
        raise ProtocolError("Тело запроса должно быть JSON-объектом.")
    document_a = resolve_document(payload, "document_a", "document_a_base64")
    document_b = resolve_document(payload, "document_b", "document_b_base64")
    if len(document_a) < MIN_DOCUMENT_CHARS or len(document_b) < MIN_DOCUMENT_CHARS:
        raise ProtocolError(f"Тексты редакций слишком короткие: нужно от {MIN_DOCUMENT_CHARS} символов каждый.")
    party_a = str(payload.get("party_a", "")).strip() or "Сторона А"
    party_b = str(payload.get("party_b", "")).strip() or "Сторона Б"
    demo = bool(payload.get("demo", False))

    diff = diff_documents(document_a, document_b)
    disputed = collect_disputed(diff["changes"])
    if not disputed:
        raise ProtocolError("Редакции совпадают: спорных пунктов нет, протокол разногласий не нужен.")
    warnings: list[str] = []
    if len(disputed) > MAX_DISPUTED_FOR_PROMPT:
        warnings.append(
            f"Спорных пунктов {len(disputed)} — в протокол включены первые {MAX_DISPUTED_FOR_PROMPT}; "
            "разбейте документ на части или согласуйте очевидные правки заранее."
        )
        disputed = disputed[:MAX_DISPUTED_FOR_PROMPT]

    model: LanguageModel = DemoProtocolModel() if demo else OpenAICompatibleModel()
    result, repaired = make_protocol(model, disputed, document_a, document_b)
    return {
        "status": "draft",
        "party_a": party_a,
        "party_b": party_b,
        "summary": diff["summary"],
        "rows": result["rows"],
        "protocol_markdown": protocol_table(result["rows"], party_a, party_b),
        "warnings": warnings,
        "note": "Протокол — проект для переговоров, а не финальная редакция: "
        "комментарии и предложения проверяет юрист. Таблицу можно сразу передать в POST /export.",
        "checked_on": datetime.now(timezone.utc).date().isoformat(),
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": demo,
        "protocol_repaired": repaired,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Протокол разногласий к договору")
    parser.add_argument("old_file", type=Path, help="Редакция А: .txt (UTF-8) или .docx")
    parser.add_argument("new_file", type=Path, help="Редакция Б: .txt (UTF-8) или .docx")
    parser.add_argument("--party-a", default="Сторона А", help="Название стороны редакции А")
    parser.add_argument("--party-b", default="Сторона Б", help="Название стороны редакции Б")
    parser.add_argument("--demo", action="store_true", help="Работа без API-ключей и внешних запросов")
    args = parser.parse_args()
    for path in (args.old_file, args.new_file):
        if not path.exists():
            print(f"Ошибка: файл не найден: {path}", file=sys.stderr)
            return 1
    try:
        payload: dict[str, Any] = {
            "document_a": read_document_file(args.old_file.read_bytes(), args.old_file.name),
            "document_b": read_document_file(args.new_file.read_bytes(), args.new_file.name),
            "party_a": args.party_a,
            "party_b": args.party_b,
            "demo": args.demo,
        }
        load_dotenv()
        result = handle_protocol(payload)
    except (AgentError, ValueError) as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
