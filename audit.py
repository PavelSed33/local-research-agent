#!/usr/bin/env python3
"""Пакетный аудит договорной базы: скоринг каждого файла + сводный отчёт.

Для каждого файла .txt/.docx в папке выполняется та же проверка с оценкой,
что и в POST /scoring (модуль scoring.py). Результаты складываются в CSV
и в сводный Markdown-отчёт: самые проблемные договоры — наверху.

CLI:  python audit.py папка_с_договорами [--demo] [--output-dir аудит]

PDF пропускается с предупреждением: без сторонних библиотек текст из PDF
не извлекается (см. doctext.py).
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent import AgentError, load_dotenv
from doctext import read_document_file
from scoring import handle_scoring

CSV_NAME = "аудит.csv"
REPORT_NAME = "аудит.md"
SUPPORTED_SUFFIXES = (".txt", ".docx")

COLUMNS = (
    "файл",
    "оценка",
    "вердикт",
    "рисков_high",
    "рисков_medium",
    "рисков_low",
    "нет_условий",
    "вычет_за_риски",
    "вычет_за_пробелы",
    "ошибка",
)

VERDICT_SHORT = {"green": "можно подписывать", "yellow": "требует доработки", "red": "не рекомендуется"}


def build_row(path: Path, scoring: dict[str, Any], error: str = "") -> dict[str, Any]:
    """Собирает строку аудита из результата скоринга."""
    risks = scoring.get("risks", [])
    counts = {"high": 0, "medium": 0, "low": 0}
    for risk in risks:
        severity = risk.get("severity", "low")
        counts[severity if severity in counts else "low"] += 1
    return {
        "файл": path.name,
        "оценка": scoring.get("score", ""),
        "вердикт": VERDICT_SHORT.get(scoring.get("verdict_color", ""), scoring.get("verdict_color", "")),
        "рисков_high": counts["high"],
        "рисков_medium": counts["medium"],
        "рисков_low": counts["low"],
        "нет_условий": len(scoring.get("missing_clauses", [])),
        "вычет_за_риски": scoring.get("risk_deduction", ""),
        "вычет_за_пробелы": scoring.get("missing_deduction", ""),
        "ошибка": error,
        "_scoring": scoring,
    }


def build_audit(folder: Path, demo: bool) -> tuple[list[dict[str, Any]], list[str]]:
    """Проверяет все поддерживаемые файлы папки; ошибки по файлу не рушат обход."""
    rows: list[dict[str, Any]] = []
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
            document = read_document_file(path.read_bytes(), path.name)
            scoring = handle_scoring({"document": document, "demo": demo})
        except (AgentError, ValueError, OSError) as exc:
            rows.append(build_row(path, {}, error=str(exc)))
            continue
        rows.append(build_row(path, scoring))
    return rows, skipped


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """Пишет CSV с разделителем «;» и BOM, чтобы файл корректно открывался в Excel."""
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS), delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_report(rows: list[dict[str, Any]], path: Path, checked_on: str) -> None:
    """Пишет сводный Markdown-отчёт: худшие договоры сверху."""
    scored = sorted(
        (row for row in rows if row["оценка"] != ""),
        key=lambda row: row["оценка"],
    )
    failed = [row for row in rows if row["ошибка"]]
    lines = [
        "# Аудит договорной базы",
        "",
        f"Дата проверки: {checked_on}",
        f"Файлов проверено: {len(scored)}, ошибок обработки: {len(failed)}",
        "",
    ]
    if scored:
        average = sum(row["оценка"] for row in scored) / len(scored)
        red = sum(1 for row in scored if row["вердикт"] == "не рекомендуется")
        yellow = sum(1 for row in scored if row["вердикт"] == "требует доработки")
        lines += [
            f"Средняя оценка: {average:.0f}/100. "
            f"Не рекомендуется подписывать: {red}, требуют доработки: {yellow}.",
            "",
            "| Файл | Оценка | Вердикт | Риски (h/m/l) | Нет условий |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in scored:
            risks = f"{row['рисков_high']}/{row['рисков_medium']}/{row['рисков_low']}"
            lines.append(f"| {row['файл']} | {row['оценка']} | {row['вердикт']} | {risks} | {row['нет_условий']} |")
        worst = scored[0]["_scoring"]
        lines += [
            "",
            "## Самый проблемный договор",
            "",
            f"**{scored[0]['файл']}** — оценка {scored[0]['оценка']}/100.",
            "",
            str(worst.get("summary", "")),
            "",
        ]
        for risk in worst.get("risks", [])[:5]:
            lines.append(
                f"- ({risk.get('severity', '?')}) {risk.get('category', '')}: "
                f"{risk.get('comment', '')}"
            )
    if failed:
        lines += ["", "## Ошибки обработки", ""]
        for row in failed:
            lines.append(f"- {row['файл']}: {row['ошибка']}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Пакетный аудит договоров: скоринг каждого файла + сводный отчёт")
    parser.add_argument("folder", type=Path, help="Папка с договорами (.txt, .docx)")
    parser.add_argument("--output-dir", type=Path, default=Path("аудит"), help="Папка для отчётов (по умолчанию «аудит»)")
    parser.add_argument("--demo", action="store_true", help="Проверка без API-ключей и внешних запросов")
    args = parser.parse_args()
    if not args.folder.is_dir():
        print(f"Ошибка: папка не найдена: {args.folder}", file=sys.stderr)
        return 1
    load_dotenv()
    rows, skipped = build_audit(args.folder, args.demo)
    if not rows:
        print(f"В папке {args.folder} нет файлов .txt или .docx.", file=sys.stderr)
        return 1
    args.output_dir.mkdir(parents=True, exist_ok=True)
    checked_on = datetime.now(timezone.utc).date().isoformat()
    csv_path = args.output_dir / CSV_NAME
    write_csv(rows, csv_path)
    report_path = args.output_dir / REPORT_NAME
    write_report(rows, report_path, checked_on)
    print(f"Обработано файлов: {len(rows)}")
    print(f"CSV: {csv_path}")
    print(f"Отчёт: {report_path}")
    for note in skipped:
        print(f"Пропущено: {note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
