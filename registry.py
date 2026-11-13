#!/usr/bin/env python3
"""Реестр договоров: обход папки, извлечение реквизитов, CSV и отчёт по срокам.

Для каждого файла .txt/.docx в папке выполняется то же извлечение сущностей,
что и в POST /extract (модуль extract.py). Результаты складываются в CSV,
а даты в ближайшие N дней — в отдельный отчёт «истекающие договоры».

CLI:  python registry.py папка_с_договорами [--demo] [--output-dir реестр] [--days 30]

PDF пропускается с предупреждением: без сторонних библиотек текст из PDF
не извлекается (см. doctext.py).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent import AgentError, load_dotenv
from doctext import read_document_file
from extract import handle_extract

CSV_NAME = "реестр.csv"
EXPIRING_NAME = "истекающие-договоры.md"
SUPPORTED_SUFFIXES = (".txt", ".docx")
MAX_REGISTRY_FILE_BYTES = 10 * 1024 * 1024

COLUMNS = (
    "файл",
    "стороны",
    "инн",
    "суммы",
    "даты",
    "срок_действия",
    "подсудность",
    "предупреждения",
    "ошибка",
)

DATE_VALUE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")


def parse_date(value: str) -> date | None:
    """Разбирает дату ДД.ММ.ГГГГ; при неудаче возвращает None."""
    match = DATE_VALUE.search(value)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def build_row(path: Path, extraction: dict[str, Any], error: str = "") -> dict[str, str]:
    """Собирает строку реестра из результата извлечения."""
    parties = extraction.get("parties", [])
    return {
        "файл": path.name,
        "стороны": "; ".join(
            f"{entry.get('name', '').strip()} ({entry.get('role', '').strip()})".strip(" ()")
            for entry in parties
        ),
        "инн": "; ".join(str(entry.get("inn", "")) for entry in parties if entry.get("inn")),
        "суммы": "; ".join(
            f"{entry.get('label', '').strip()}: {entry.get('value', '').strip()}".strip(": ")
            for entry in extraction.get("amounts", [])
        ),
        "даты": "; ".join(
            f"{entry.get('label', '').strip()}: {entry.get('date', '').strip()}".strip(": ")
            for entry in extraction.get("dates", [])
        ),
        "срок_действия": str((extraction.get("term") or {}).get("duration", "")),
        "подсудность": str((extraction.get("jurisdiction") or {}).get("court", "")),
        "предупреждения": "; ".join(extraction.get("warnings", [])),
        "ошибка": error,
    }


def find_expiring(rows: list[dict[str, str]], extractions: list[dict[str, Any]], today: date, days: int) -> list[dict[str, str]]:
    """Находит даты, попадающие в ближайшие N дней (включая просроченные не более чем на N)."""
    window_end = today + timedelta(days=days)
    expiring: list[tuple[date, dict[str, str]]] = []
    for row, extraction in zip(rows, extractions):
        for entry in extraction.get("dates", []):
            value = parse_date(str(entry.get("date", "")))
            if value is None or not (today - timedelta(days=days) <= value <= window_end):
                continue
            expiring.append(
                (
                    value,
                    {
                        "файл": row["файл"],
                        "метка": str(entry.get("label", "")).strip(),
                        "дата": value.strftime("%d.%m.%Y"),
                        "осталось_дней": str((value - today).days),
                    },
                )
            )
    expiring.sort(key=lambda item: item[0])
    return [item for _, item in expiring]


def build_registry(folder: Path, demo: bool) -> tuple[list[dict[str, str]], list[dict[str, Any]], list[str]]:
    """Обрабатывает все поддерживаемые файлы папки; ошибки по файлу не рушат обход."""
    rows: list[dict[str, str]] = []
    extractions: list[dict[str, Any]] = []
    skipped: list[str] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".pdf":
            skipped.append(f"{path.name}: PDF не поддерживается, сохраните как DOCX или TXT")
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_REGISTRY_FILE_BYTES:
                skipped.append(f"{path.name}: файл больше допустимых {MAX_REGISTRY_FILE_BYTES} байт")
                continue
            document = read_document_file(path.read_bytes(), path.name)
            extraction = handle_extract({"document": document, "demo": demo})
        except (AgentError, ValueError, OSError) as exc:
            rows.append(build_row(path, {}, error=str(exc)))
            extractions.append({})
            continue
        rows.append(build_row(path, extraction))
        extractions.append(extraction)
    return rows, extractions, skipped


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    """Пишет CSV с разделителем «;» и BOM, чтобы файл корректно открывался в Excel."""
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def write_expiring(expiring: list[dict[str, str]], path: Path, days: int, checked_on: str) -> None:
    lines = [
        f"# Договоры с датами в ближайшие {days} дней",
        "",
        f"Дата проверки: {checked_on}",
        "",
    ]
    if not expiring:
        lines.append("Истекающих дат не найдено.")
    else:
        lines.append("| Файл | Что за дата | Дата | Осталось дней |")
        lines.append("| --- | --- | --- | --- |")
        for item in expiring:
            lines.append(f"| {item['файл']} | {item['метка']} | {item['дата']} | {item['осталось_дней']} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Реестр договоров: CSV + отчёт по истекающим срокам")
    parser.add_argument("folder", type=Path, help="Папка с договорами (.txt, .docx)")
    parser.add_argument("--output-dir", type=Path, default=Path("реестр"), help="Папка для реестра (по умолчанию «реестр»)")
    parser.add_argument("--days", type=int, default=30, help="Горизонт отчёта по срокам в днях (по умолчанию 30)")
    parser.add_argument("--demo", action="store_true", help="Извлечение без API-ключей и внешних запросов")
    args = parser.parse_args()
    if not args.folder.is_dir():
        print(f"Ошибка: папка не найдена: {args.folder}", file=sys.stderr)
        return 1
    load_dotenv()
    rows, extractions, skipped = build_registry(args.folder, args.demo)
    if not rows:
        print(f"В папке {args.folder} нет файлов .txt или .docx.", file=sys.stderr)
        return 1
    args.output_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date()
    csv_path = args.output_dir / CSV_NAME
    write_csv(rows, csv_path)
    expiring = find_expiring(rows, extractions, today, args.days)
    expiring_path = args.output_dir / EXPIRING_NAME
    write_expiring(expiring, expiring_path, args.days, today.isoformat())
    print(f"Обработано файлов: {len(rows)}")
    print(f"Реестр: {csv_path}")
    print(f"Истекающие даты: {expiring_path} (найдено: {len(expiring)})")
    for note in skipped:
        print(f"Пропущено: {note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
