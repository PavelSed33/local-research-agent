#!/usr/bin/env python3
"""Извлечение сущностей из договора: стороны, суммы, сроки, неустойки, подсудность.

Модуль не обращается к внешним источникам: анализируется только переданный
текст договора. Каждая цитата и каждый идентификатор (ИНН/ОГРН) проверяются
на вхождение в текст документа — это защита от выдуманных реквизитов.
Дополнительно ИНН и ОГРН проверяются по контрольным суммам: несоответствие
попадает в warnings, так как может быть опечаткой в самом договоре.
Текст договора нигде не сохраняется.

CLI:  python extract.py договор.txt [--demo]
API:  POST /extract (см. api.py)
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
from doctext import ensure_document_transfer_allowed, read_document_file, resolve_document
from review import MIN_DOCUMENT_CHARS, MIN_QUOTE_CHARS, normalize_text

ENTITY_LISTS = ("parties", "amounts", "dates", "penalties")
REQUIRED_FIELDS = {
    "parties": ("name",),
    "amounts": ("label", "value"),
    "dates": ("label", "date"),
    "penalties": ("label", "rate"),
}

INN_PATTERN = re.compile(r"(?<!\d)\d{10}(?!\d)|(?<!\d)\d{12}(?!\d)")
OGRN_PATTERN = re.compile(r"(?<!\d)\d{13}(?!\d)|(?<!\d)\d{15}(?!\d)")
DATE_PATTERN = re.compile(r"(?<!\d)\d{2}\.\d{2}\.\d{4}(?!\d)")
AMOUNT_PATTERN = re.compile(r"\d[\d ]*(?:[.,]\d{2})?\s*(?:руб(?:лей|ля|ль)?\.?|₽)")


def inn_checksum_ok(inn: str) -> bool:
    """Проверяет контрольные цифры ИНН (10 или 12 знаков)."""
    if not inn.isdigit() or len(inn) not in (10, 12):
        return False
    digits = [int(char) for char in inn]
    if len(digits) == 10:
        coeffs = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        return sum(d * c for d, c in zip(digits[:9], coeffs)) % 11 % 10 == digits[9]
    coeffs_11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    if sum(d * c for d, c in zip(digits[:10], coeffs_11)) % 11 % 10 != digits[10]:
        return False
    coeffs_12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    return sum(d * c for d, c in zip(digits[:11], coeffs_12)) % 11 % 10 == digits[11]


def ogrn_checksum_ok(ogrn: str) -> bool:
    """Проверяет контрольную цифру ОГРН (13 знаков) или ОГРНИП (15 знаков)."""
    if not ogrn.isdigit() or len(ogrn) not in (13, 15):
        return False
    if len(ogrn) == 13:
        return int(ogrn[:12]) % 11 % 10 == int(ogrn[12])
    return int(ogrn[:14]) % 13 % 10 == int(ogrn[14])


def validate_extraction(result: dict[str, Any], document: str) -> list[str]:
    """Проверяет структуру результата и вхождение цитат и реквизитов в текст."""
    issues: list[str] = []
    corpus = normalize_text(document)
    for section in ENTITY_LISTS:
        entries = result.get(section)
        if not isinstance(entries, list):
            issues.append(f"Поле {section} должно быть списком.")
            continue
        for index, entry in enumerate(entries, start=1):
            label = f"{section} №{index}"
            if not isinstance(entry, dict):
                issues.append(f"{label} не является объектом.")
                continue
            for field in REQUIRED_FIELDS[section]:
                if field in entry and not isinstance(entry[field], str):
                    issues.append(f"{label}: поле {field} должно быть строкой.")
                if not str(entry.get(field, "")).strip():
                    issues.append(f"{label}: пустое поле {field}.")
            if not isinstance(entry.get("quote"), str):
                issues.append(f"{label}: цитата должна быть строкой.")
            quote = str(entry.get("quote", "")).strip()
            if len(quote) < MIN_QUOTE_CHARS:
                issues.append(f"{label}: цитата короче {MIN_QUOTE_CHARS} символов.")
            elif normalize_text(quote) not in corpus:
                issues.append(f"{label}: цитата не найдена в тексте договора.")
            if section == "amounts" and entry.get("currency") not in ("RUB", "USD", "EUR"):
                issues.append(f"{label}: currency должна быть допустимым кодом ISO 4217.")
            if section == "dates":
                from datetime import datetime as _date_parser
                value = entry.get("date")
                if not isinstance(value, str):
                    issues.append(f"{label}: дата должна быть строкой.")
                else:
                    try:
                        _date_parser.strptime(value, "%d.%m.%Y")
                    except ValueError:
                        issues.append(f"{label}: дата должна быть реальной календарной датой.")
            if section == "parties":
                for field, kind in (("inn", "ИНН"), ("ogrn", "ОГРН")):
                    value = str(entry.get(field, "") or "").strip()
                    if not value:
                        continue
                    if not value.isdigit() or len(value) not in ((10, 12) if kind == "ИНН" else (13, 15)):
                        issues.append(f"{label}: {kind} «{value}» не похож на {kind}.")
                    elif value not in document:
                        issues.append(f"{label}: {kind} «{value}» не найден в тексте договора.")
    for field in ("term", "jurisdiction"):
        entry = result.get(field)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            issues.append(f"Поле {field} должно быть объектом или null.")
            continue
        if not isinstance(entry.get("quote"), str):
            issues.append(f"Поле {field}: цитата должна быть строкой.")
        for name in (("duration",) if field == "term" else ("court",)):
            if name in entry and not isinstance(entry[name], str):
                issues.append(f"Поле {field}: {name} должно быть строкой.")
        quote = str(entry.get("quote", "")).strip()
        if len(quote) < MIN_QUOTE_CHARS:
            issues.append(f"Поле {field}: цитата короче {MIN_QUOTE_CHARS} символов.")
        elif normalize_text(quote) not in corpus:
            issues.append(f"Поле {field}: цитата не найдена в тексте договора.")
    return issues


def checksum_warnings(result: dict[str, Any]) -> list[str]:
    """Находит ИНН/ОГРН, не проходящие контрольные суммы (возможна опечатка)."""
    warnings: list[str] = []
    for entry in result.get("parties", []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip() or "сторона без названия"
        inn = str(entry.get("inn", "") or "").strip()
        if inn and not inn_checksum_ok(inn):
            warnings.append(f"ИНН {inn} ({name}) не проходит контрольную сумму — проверьте, возможна опечатка в договоре.")
        ogrn = str(entry.get("ogrn", "") or "").strip()
        if ogrn and not ogrn_checksum_ok(ogrn):
            warnings.append(f"ОГРН {ogrn} ({name}) не проходит контрольную сумму — проверьте, возможна опечатка в договоре.")
    return warnings


def build_extract_prompt(document: str) -> tuple[str, str]:
    system = (
        "Ты юрист-аналитик, извлекающий реквизиты из договора по праву РФ. Используй только переданный текст договора. "
        "Верни только JSON без пояснений со структурой: "
        '{"parties": [{"role": "роль стороны", "name": "наименование", "inn": "ИНН или пустая строка", '
        '"ogrn": "ОГРН или пустая строка", "quote": "точная цитата с реквизитами"}], '
        '"amounts": [{"label": "что за сумма", "value": "сумма как в тексте", "currency": "RUB", "quote": "точная цитата"}], '
        '"dates": [{"label": "что за дата", "date": "дата как в тексте", "quote": "точная цитата"}], '
        '"penalties": [{"label": "вид санкции", "rate": "размер как в тексте", "quote": "точная цитата"}], '
        '"term": {"duration": "срок действия", "quote": "точная цитата"} или null, '
        '"jurisdiction": {"court": "подсудность споров", "quote": "точная цитата"} или null}. '
        "Цитаты приводи дословно из текста договора, не сокращай и не перефразируй. "
        "ИНН и ОГРН копируй цифра в цифру из текста. Не выдумывай реквизиты, которых нет в договоре: "
        "если сведений нет, оставляй список пустым или null."
    )
    user = f"Извлеки реквизиты из договора.\n\nТекст договора:\n\n{document}"
    return system, user


def _strip_model_attestations(result):
    """Discard self-declared approvals and unknown fields from model output."""
    cleaned = {"verified": False, "lawyer_reviewed": False}
    fields = {
        "parties": ("role", "name", "inn", "ogrn", "quote"),
        "amounts": ("label", "value", "currency", "quote"),
        "dates": ("label", "date", "quote"),
        "penalties": ("label", "rate", "quote"),
        "term": ("duration", "quote"),
        "jurisdiction": ("court", "quote"),
    }
    for name in ("parties", "amounts", "dates", "penalties"):
        cleaned[name] = [{**{k: item[k] for k in fields[name] if k in item},
                          "verified": False, "lawyer_reviewed": False}
                         for item in result.get(name, []) if isinstance(item, dict)]
    for name in ("term", "jurisdiction"):
        item = result.get(name)
        cleaned[name] = ({**{k: item[k] for k in fields[name] if k in item},
                          "verified": False, "lawyer_reviewed": False}
                         if isinstance(item, dict) else None)
    return cleaned


def extract_entities(model: LanguageModel, document: str) -> tuple[dict[str, Any], bool]:
    """Извлекает сущности; при ошибках структуры или цитат — одна попытка исправления."""
    system, user = build_extract_prompt(document)
    result = extract_json(model.complete(system, user))
    issues = validate_extraction(result, document)
    if not issues:
        return _strip_model_attestations(result), False
    repaired = extract_json(
        model.complete(
            "Ты исправляешь JSON-результат извлечения реквизитов договора. Устрани все перечисленные проблемы, "
            "не добавляй новых реквизитов и фактов. Цитаты и идентификаторы должны дословно входить в текст договора. "
            "Верни только исправленный JSON.",
            "Проблемы:\n- " + "\n- ".join(issues)
            + f"\n\nРезультат:\n{json.dumps(result, ensure_ascii=False)}\n\nТекст договора:\n\n{document}",
        )
    )
    remaining = validate_extraction(repaired, document)
    if remaining:
        raise AgentError("Извлечение реквизитов не прошло контроль после исправления.")
    return _strip_model_attestations(repaired), True


def _line_with(document: str, start: int) -> str:
    """Возвращает строку документа, содержащую позицию start (для демо-цитат)."""
    line_start = document.rfind("\n", 0, start) + 1
    line_end = document.find("\n", start)
    if line_end == -1:
        line_end = len(document)
    return document[line_start:line_end].strip()


class DemoExtractModel:
    """Демонстрационное извлечение реквизитов регулярными выражениями без внешних вызовов."""

    def complete(self, system: str, user: str) -> str:
        document = user.split("Текст договора:", 1)[-1].strip()
        parties = []
        for inn in dict.fromkeys(INN_PATTERN.findall(document)):
            line = _line_with(document, document.find(inn))
            ogrn_match = OGRN_PATTERN.search(line)
            parties.append(
                {
                    "role": "сторона (демонстрационное извлечение)",
                    "name": f"Сторона с ИНН {inn}",
                    "inn": inn,
                    "ogrn": ogrn_match.group(0) if ogrn_match else "",
                    "quote": line,
                }
            )
        amounts = [
            {
                "label": "денежная сумма (демонстрационное извлечение)",
                "value": match.group(0).strip(),
                "currency": "RUB",
                "quote": _line_with(document, match.start()),
            }
            for match in AMOUNT_PATTERN.finditer(document)
        ]
        dates = [
            {
                "label": "дата (демонстрационное извлечение)",
                "date": match.group(0),
                "quote": _line_with(document, match.start()),
            }
            for match in DATE_PATTERN.finditer(document)
        ]
        return json.dumps(
            {
                "parties": parties,
                "amounts": amounts,
                "dates": dates,
                "penalties": [],
                "term": None,
                "jurisdiction": None,
            },
            ensure_ascii=False,
        )


def handle_extract(payload: dict[str, Any]) -> dict[str, Any]:
    """Валидирует запрос и выполняет извлечение реквизитов."""
    document = resolve_document(payload)
    if len(document) < MIN_DOCUMENT_CHARS:
        raise ValueError(f"Текст договора слишком короткий: нужно от {MIN_DOCUMENT_CHARS} символов.")
    if len(document) > 500_000:
        raise ValueError("Текст договора превышает допустимый размер (500000 символов).")
    demo = payload.get("demo", False)
    if type(demo) is not bool:
        raise ValueError("Поле demo должно быть логическим значением.")
    model: LanguageModel = DemoExtractModel() if demo else OpenAICompatibleModel()
    result, repaired = extract_entities(model, document.strip())
    return {
        "status": "draft",
        "verified": False,
        "lawyer_reviewed": False,
        "parties": result.get("parties", []),
        "amounts": result.get("amounts", []),
        "dates": result.get("dates", []),
        "penalties": result.get("penalties", []),
        "term": result.get("term"),
        "jurisdiction": result.get("jurisdiction"),
        "warnings": checksum_warnings(result),
        "checked_on": datetime.now(timezone.utc).date().isoformat(),
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": demo,
        "extraction_repaired": repaired,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Извлечение реквизитов из договора: стороны, суммы, сроки, подсудность")
    parser.add_argument("file", type=Path, help="Файл с договором: .txt (UTF-8) или .docx")
    parser.add_argument("--demo", action="store_true", help="Извлечение без API-ключей и внешних запросов")
    args = parser.parse_args()
    if not args.file.exists():
        print(f"Ошибка: файл не найден: {args.file}", file=sys.stderr)
        return 1
    load_dotenv()
    try:
        document = read_document_file(args.file.read_bytes(), args.file.name)
        result = handle_extract({"document": document, "demo": args.demo})
    except (AgentError, ValueError) as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
