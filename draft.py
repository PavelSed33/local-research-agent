#!/usr/bin/env python3
"""Драфтинг юридических документов: претензии, ответы, допсоглашения, уведомления.

Модель готовит проект документа по описанию ситуации и (опционально) тексту
договора. Код проверяет структуру документа по типу и дословность ссылок на
договор: цитата, которой нет в тексте договора, — это выдуманное основание.
Результат всегда имеет status=draft: проект для проверки юристом.

CLI:  python draft.py claim --facts ситуация.txt [--contract договор.docx] [--demo]
API:  POST /draft (см. api.py)
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
from doctext import read_document_file
from review import normalize_text

MIN_FACTS_CHARS = 30
MIN_QUOTE_CHARS = 10

DOCUMENT_TYPES: dict[str, dict[str, Any]] = {
    "claim": {
        "title": "Претензия",
        "required_blocks": ("треб", "срок"),
        "description": "досудебная претензия контрагенту",
    },
    "claim_response": {
        "title": "Ответ на претензию",
        "required_blocks": ("рассмотр", "претензи"),
        "description": "мотивированный ответ на претензию контрагента",
    },
    "amendment": {
        "title": "Дополнительное соглашение",
        "required_blocks": ("договор", "измен"),
        "description": "дополнительное соглашение к договору",
    },
    "notification": {
        "title": "Уведомление",
        "required_blocks": ("уведомл",),
        "description": "уведомление контрагента (о расторжении, просрочке и т.п.)",
    },
}


class DraftError(ValueError):
    """Понятная ошибка входных данных драфтинга."""


def build_draft_prompt(doc_type: str, facts: str, contract: str) -> tuple[str, str]:
    meta = DOCUMENT_TYPES[doc_type]
    contract_block = (
        f"\n\nТекст договора (основание):\n\n{contract}" if contract else "\n\nТекст договора не передан."
    )
    system = (
        f"Ты юрист-драфтер, готовящий документ по праву РФ: {meta['description']}. "
        "Верни только JSON: {\"title\": \"название документа\", \"document\": \"полный текст документа в Markdown\", "
        "\"references\": [{\"quote\": \"дословная цитата из договора, на которую опирается документ\"}]}. "
        "Пиши строго по фактам из описания ситуации; имена, суммы и даты бери только из описания и текста договора. "
        "Не выдумывай реквизиты, номера документов, суммы и статьи законов. "
        "Если каких-то данных не хватает, оставь в документе плейсхолдер в квадратных скобках, например [номер договора]. "
        "Ссылки на договор приводи дословными цитатами в поле references; если договор не передан, оставь references пустым. "
        "В конце документа — блок подписи с плейсхолдером [должность, ФИО]. "
        "Не выдавай документ за финальную версию: это проект для проверки юристом."
    )
    user = f"Подготовь проект документа.\n\nОписание ситуации:\n{facts}{contract_block}"
    return system, user


def validate_draft(result: dict[str, Any], doc_type: str, contract: str) -> list[str]:
    """Проверяет структуру документа и дословность ссылок на договор."""
    issues: list[str] = []
    meta = DOCUMENT_TYPES[doc_type]
    if not str(result.get("title", "")).strip():
        issues.append("Пустое поле title.")
    document = str(result.get("document", ""))
    if len(document.strip()) < 200:
        issues.append("Документ слишком короткий (менее 200 символов).")
    lowered = normalize_text(document).lower()
    for block in meta["required_blocks"]:
        if block not in lowered:
            issues.append(f"В документе типа «{meta['title']}» не найден обязательный блок «{block}».")
    if "[должность" not in lowered and "подпис" not in lowered:
        issues.append("Нет блока подписи или плейсхолдера подписанта.")
    references = result.get("references", [])
    if not isinstance(references, list):
        issues.append("Поле references должно быть списком.")
        references = []
    corpus = normalize_text(contract) if contract else ""
    for index, ref in enumerate(references, start=1):
        quote = str(ref.get("quote", "")).strip() if isinstance(ref, dict) else ""
        if len(quote) < MIN_QUOTE_CHARS:
            issues.append(f"Ссылка №{index}: цитата короче {MIN_QUOTE_CHARS} символов.")
        elif not corpus:
            issues.append(f"Ссылка №{index}: договор не передан, цитат быть не должно.")
        elif normalize_text(quote) not in corpus:
            issues.append(f"Ссылка №{index}: цитата не найдена в тексте договора.")
    return issues


def draft_document(model: LanguageModel, doc_type: str, facts: str, contract: str) -> tuple[dict[str, Any], bool]:
    """Готовит проект; при ошибках структуры или цитат — одна попытка исправления."""
    system, user = build_draft_prompt(doc_type, facts, contract)
    result = extract_json(model.complete(system, user))
    issues = validate_draft(result, doc_type, contract)
    if not issues:
        return result, False
    repaired = extract_json(
        model.complete(
            "Ты исправляешь JSON-проект юридического документа. Устрани все перечисленные проблемы, "
            "не добавляй новых фактов и цитат. Верни только исправленный JSON.",
            "Проблемы:\n- " + "\n- ".join(issues)
            + f"\n\nРезультат:\n{json.dumps(result, ensure_ascii=False)}\n\n"
            + user,
        )
    )
    remaining = validate_draft(repaired, doc_type, contract)
    if remaining:
        raise AgentError("Проект документа не прошёл контроль после исправления: " + " ".join(remaining))
    return repaired, True


class DemoDraftModel:
    """Демонстрационный проект документа: шаблон без внешних вызовов."""

    def complete(self, system: str, user: str) -> str:
        facts = user.split("Описание ситуации:", 1)[-1].split("Текст договора", 1)[0].strip()
        quote = ""
        if "Текст договора (основание):" in user:
            contract = user.split("Текст договора (основание):", 1)[-1].strip()
            quote = normalize_text(contract)[:140]
        title = system.split("готовящий документ по праву РФ: ", 1)[-1].split(".", 1)[0]
        document = (
            f"# {title.capitalize()} (демонстрационный проект)\n\n"
            f"Кому: [наименование контрагента, адрес]\n\n"
            f"От: [наименование организации, адрес]\n\n"
            f"Описание ситуации (по материалам запроса): {facts[:300]}\n\n"
            f"Требования: [сформулировать требование]. Просим выполнить требование в срок "
            f"до [дата].\n\n"
            f"Настоящий документ подготовлен в демонстрационном режиме и проверяет структуру ответа; "
            f"содержательный проект формируется при подключённой языковой модели.\n\n"
            f"Уведомляем, что при неисполнении мы вправе обратиться в суд.\n\n"
            f"[должность, ФИО]  ______________  [дата]"
        )
        return json.dumps(
            {
                "title": f"{title.capitalize()} (демо)",
                "document": document,
                "references": [{"quote": quote}] if quote else [],
            },
            ensure_ascii=False,
        )


def handle_draft(payload: dict[str, Any]) -> dict[str, Any]:
    """Обработчик POST /draft: проект документа по типу и ситуации."""
    if not isinstance(payload, dict):
        raise DraftError("Тело запроса должно быть JSON-объектом.")
    doc_type = str(payload.get("type", "")).strip()
    if doc_type not in DOCUMENT_TYPES:
        allowed = ", ".join(sorted(DOCUMENT_TYPES))
        raise DraftError(f"Поле type обязательно и должно быть одним из: {allowed}.")
    facts = str(payload.get("facts", "")).strip()
    if len(facts) < MIN_FACTS_CHARS:
        raise DraftError(f"Поле facts обязательно: опишите ситуацию (от {MIN_FACTS_CHARS} символов).")
    contract = str(payload.get("contract", "")).strip()
    demo = bool(payload.get("demo", False))
    model: LanguageModel = DemoDraftModel() if demo else OpenAICompatibleModel()
    result, repaired = draft_document(model, doc_type, facts, contract)
    placeholders = [
        token for token in normalize_text(result["document"]).split()
        if token.startswith("[") and len(token) > 2
    ]
    warnings = []
    if placeholders:
        warnings.append(
            f"В проекте {len(placeholders)} плейсхолдер(ов) для заполнения: проверьте реквизиты перед отправкой."
        )
    return {
        "status": "draft",
        "doc_type": doc_type,
        "title": result["title"].strip(),
        "document": result["document"],
        "references": result.get("references", []),
        "warnings": warnings,
        "note": "Документ — проект, а не финальная версия: обязательна проверка юристом, "
        "плейсхолдеры в квадратных скобках нужно заполнить фактическими данными.",
        "checked_on": datetime.now(timezone.utc).date().isoformat(),
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": demo,
        "draft_repaired": repaired,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Драфтинг юридических документов")
    parser.add_argument("type", choices=sorted(DOCUMENT_TYPES), help="Тип документа")
    parser.add_argument("--facts", type=Path, required=True, help="Файл с описанием ситуации (.txt)")
    parser.add_argument("--contract", type=Path, default=None, help="Файл договора-основания (.txt или .docx)")
    parser.add_argument("--demo", action="store_true", help="Работа без API-ключей и внешних запросов")
    args = parser.parse_args()
    if not args.facts.exists():
        print(f"Ошибка: файл не найден: {args.facts}", file=sys.stderr)
        return 1
    payload: dict[str, Any] = {
        "type": args.type,
        "facts": args.facts.read_text(encoding="utf-8"),
        "demo": args.demo,
    }
    if args.contract is not None:
        if not args.contract.exists():
            print(f"Ошибка: файл не найден: {args.contract}", file=sys.stderr)
            return 1
        try:
            payload["contract"] = read_document_file(args.contract.read_bytes(), args.contract.name)
        except ValueError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
            return 1
    load_dotenv()
    try:
        result = handle_draft(payload)
    except (AgentError, ValueError) as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
