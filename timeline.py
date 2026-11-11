#!/usr/bin/env python3
"""Хронология дела: события из переписки и первички в единую цепочку.

LLM извлекает события из переданных материалов, код проверяет каждое:
дата разбирается в ДД.ММ.ГГГГ, цитата дословно входит в указанный источник,
источник существует. Сортировка по датам выполняется кодом — порядок
событий не зависит от модели. События без даты попадают в отдельный список.

CLI:  python timeline.py файл1.txt файл2.docx [--demo]
API:  POST /timeline (см. api.py)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
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
from penalty import parse_date_ru
from review import normalize_text

MAX_MATERIALS = 50
MAX_MATERIAL_CHARS = 60000
MIN_QUOTE_CHARS = 10

DATE_PATTERN = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")


class TimelineError(ValueError):
    """Понятная ошибка входных данных хронологии."""


def validate_materials(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Проверяет и нормализует список материалов из запроса."""
    materials = payload.get("materials")
    if not isinstance(materials, list) or not materials:
        raise TimelineError("Поле materials обязательно: непустой список материалов дела.")
    if len(materials) > MAX_MATERIALS:
        raise TimelineError(f"Материалов больше {MAX_MATERIALS}: разбейте дело на части.")
    normalized: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for index, item in enumerate(materials, start=1):
        if not isinstance(item, dict):
            raise TimelineError(f"Материал №{index} должен быть объектом с полями name и text.")
        name = str(item.get("name", "")).strip() or f"материал-{index}"
        if name in seen_names:
            raise TimelineError(f"Дублируется имя материала «{name}».")
        seen_names.add(name)
        text = str(item.get("text", "")).strip()
        if len(text) < 20:
            raise TimelineError(f"Материал «{name}» слишком короткий: нужно от 20 символов.")
        if len(text) > MAX_MATERIAL_CHARS:
            raise TimelineError(f"Материал «{name}» больше {MAX_MATERIAL_CHARS} символов.")
        normalized.append({"name": name, "text": text})
    return normalized


def build_timeline_prompt(materials: list[dict[str, str]]) -> tuple[str, str]:
    listing = "\n\n".join(
        f"=== Источник: {item['name']} ===\n{item['text']}" for item in materials
    )
    system = (
        "Ты юрист-процессуалист, восстанавливающий хронологию дела по материалам. "
        "Извлекай только события, прямо подтверждённые текстом: платежи, поставки, письма, претензии, "
        "акты, сроки, ответы контрагента. Не выдумывай события и даты. "
        "Верни только JSON: {\"events\": [{\"date\": \"ДД.ММ.ГГГГ или пустая строка, если даты нет\", "
        "\"event\": \"краткое описание события\", \"source\": \"точное имя источника из списка\", "
        "\"quote\": \"дословная цитата из источника, подтверждающая событие\"}]}. "
        "Цитаты приводи дословно, без сокращений и перефразирования. "
        "Поле source заполняй точным именем источника после слова «Источник:». "
        "Если дата события в тексте не указана, оставь date пустым — не угадывай."
    )
    user = f"Восстанови хронологию дела по материалам.\n\n{listing}"
    return system, user


def validate_timeline(result: dict[str, Any], materials: list[dict[str, str]]) -> list[str]:
    """Проверяет даты, источники и дословность цитат."""
    issues: list[str] = []
    events = result.get("events")
    if not isinstance(events, list):
        return ["Поле events должно быть списком."]
    corpus = {item["name"]: normalize_text(item["text"]) for item in materials}
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            issues.append(f"Событие №{index} не является объектом.")
            continue
        source = str(event.get("source", "")).strip()
        if source not in corpus:
            issues.append(f"Событие №{index}: неизвестный источник «{source}».")
        raw_date = str(event.get("date", "")).strip()
        if raw_date:
            try:
                parse_date_ru(raw_date)
            except ValueError:
                issues.append(f"Событие №{index}: дата «{raw_date}» не в формате ДД.ММ.ГГГГ.")
        quote = str(event.get("quote", "")).strip()
        if len(quote) < MIN_QUOTE_CHARS:
            issues.append(f"Событие №{index}: цитата короче {MIN_QUOTE_CHARS} символов.")
        elif source in corpus and normalize_text(quote) not in corpus[source]:
            issues.append(f"Событие №{index}: цитата не найдена в источнике «{source}».")
        if not str(event.get("event", "")).strip():
            issues.append(f"Событие №{index}: пустое описание.")
    return issues


def extract_timeline(model: LanguageModel, materials: list[dict[str, str]]) -> tuple[dict[str, Any], bool]:
    """Извлекает события; при ошибках валидации — одна попытка исправления."""
    system, user = build_timeline_prompt(materials)
    result = extract_json(model.complete(system, user))
    issues = validate_timeline(result, materials)
    if not issues:
        return result, False
    listing = "\n\n".join(f"=== Источник: {item['name']} ===\n{item['text']}" for item in materials)
    repaired = extract_json(
        model.complete(
            "Ты исправляешь JSON-хронологию дела. Устрани все перечисленные проблемы, не добавляй новых событий. "
            "Цитаты должны дословно входить в указанный источник, имена источников — точно из списка. "
            "Верни только исправленный JSON.",
            "Проблемы:\n- " + "\n- ".join(issues)
            + f"\n\nРезультат:\n{json.dumps(result, ensure_ascii=False)}\n\nМатериалы:\n\n{listing}",
        )
    )
    remaining = validate_timeline(repaired, materials)
    if remaining:
        raise AgentError("Хронология не прошла контроль после исправления: " + " ".join(remaining))
    return repaired, True


def sort_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Разделяет датированные и недатированные события; датированные сортирует кодом."""
    dated: list[tuple[date, int, dict[str, Any]]] = []
    undated: list[dict[str, Any]] = []
    for position, event in enumerate(events):
        raw_date = str(event.get("date", "")).strip()
        if raw_date:
            dated.append((parse_date_ru(raw_date), position, event))
        else:
            undated.append(event)
    dated.sort(key=lambda item: (item[0], item[1]))
    return [event for _, _, event in dated], undated


class DemoTimelineModel:
    """Демонстрационное извлечение событий: ищет даты регулярным выражением."""

    def complete(self, system: str, user: str) -> str:
        events: list[dict[str, Any]] = []
        for block in user.split("=== Источник: ")[1:]:
            name, _, text = block.partition(" ===")
            name = name.strip()
            text = text.strip()
            match = DATE_PATTERN.search(text)
            if not match:
                continue
            start = max(0, match.start() - 60)
            quote = normalize_text(text[start : match.end() + 120])
            events.append(
                {
                    "date": match.group(0),
                    "event": f"Событие из источника «{name}» (демонстрационное описание)",
                    "source": name,
                    "quote": quote,
                }
            )
        if not events:
            first = user.split("=== Источник: ", 1)[1]
            name, _, text = first.partition(" ===")
            events.append(
                {
                    "date": "",
                    "event": "Дат в материалах не найдено (демонстрационный режим)",
                    "source": name.strip(),
                    "quote": normalize_text(text.strip())[:140],
                }
            )
        return json.dumps({"events": events}, ensure_ascii=False)


def handle_timeline(payload: dict[str, Any]) -> dict[str, Any]:
    """Обработчик POST /timeline: хронология дела из материалов."""
    if not isinstance(payload, dict):
        raise TimelineError("Тело запроса должно быть JSON-объектом.")
    materials = validate_materials(payload)
    demo = bool(payload.get("demo", False))
    model: LanguageModel = DemoTimelineModel() if demo else OpenAICompatibleModel()
    result, repaired = extract_timeline(model, materials)
    dated, undated = sort_events(result.get("events", []))
    return {
        "status": "draft",
        "events": dated,
        "undated_events": undated,
        "events_count": len(dated) + len(undated),
        "materials": [item["name"] for item in materials],
        "note": "Хронология — проект для проверки юристом: даты и привязка к источникам проверены кодом, "
        "полнота извлечения событий зависит от модели.",
        "checked_on": datetime.now(timezone.utc).date().isoformat(),
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": demo,
        "timeline_repaired": repaired,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Хронология дела из переписки и первички")
    parser.add_argument("files", type=Path, nargs="+", help="Файлы материалов: .txt (UTF-8) или .docx")
    parser.add_argument("--demo", action="store_true", help="Работа без API-ключей и внешних запросов")
    args = parser.parse_args()
    materials = []
    for file in args.files:
        if not file.exists():
            print(f"Ошибка: файл не найден: {file}", file=sys.stderr)
            return 1
        try:
            materials.append({"name": file.name, "text": read_document_file(file.read_bytes(), file.name)})
        except ValueError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
            return 1
    load_dotenv()
    try:
        result = handle_timeline({"materials": materials, "demo": args.demo})
    except (AgentError, ValueError) as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
