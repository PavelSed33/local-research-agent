#!/usr/bin/env python3
"""Сравнение двух редакций договора (lite-redlining) на difflib.

Полностью детерминированный модуль: языковая модель не используется,
внешних запросов нет. Результат — список изменений (added/removed/changed)
и unified diff. Это не полноценный протокол разногласий, а техническая
сверка редакций: юридическую оценку изменений делает POST /review.

CLI:  python redline.py старая_версия.txt новая_версия.txt
API:  POST /diff (см. api.py)
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent import LEGAL_DISCLAIMER
from doctext import read_document_file, resolve_document
from review import MIN_DOCUMENT_CHARS

MAX_CHANGED_PAIRS_NOTE = "Количество удалённых и добавленных строк в блоке различается."
MAX_DIFF_DOCUMENT_CHARS = 200_000


def diff_documents(old: str, new: str) -> dict[str, Any]:
    """Сравнивает две редакции построчно и возвращает структурированный список изменений."""
    if max(len(old), len(new)) > MAX_DIFF_DOCUMENT_CHARS:
        raise ValueError(f"Длина каждой версии не должна превышать {MAX_DIFF_DOCUMENT_CHARS} символов.")
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    changes: list[dict[str, str]] = []
    counts = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            counts["unchanged"] += i2 - i1
            continue
        if tag == "delete":
            for line in old_lines[i1:i2]:
                changes.append({"type": "removed", "text": line})
            counts["removed"] += i2 - i1
            continue
        if tag == "insert":
            for line in new_lines[j1:j2]:
                changes.append({"type": "added", "text": line})
            counts["added"] += j2 - j1
            continue
        # replace: сопоставляем строки внутри блока попарно — это «изменённые» пункты.
        removed_block = old_lines[i1:i2]
        added_block = new_lines[j1:j2]
        paired = min(len(removed_block), len(added_block))
        for index in range(paired):
            changes.append({"type": "changed", "old": removed_block[index], "new": added_block[index]})
        for line in removed_block[paired:]:
            changes.append({"type": "removed", "text": line})
        for line in added_block[paired:]:
            changes.append({"type": "added", "text": line})
        counts["changed"] += paired
        counts["removed"] += len(removed_block) - paired
        counts["added"] += len(added_block) - paired
    unified = "\n".join(
        difflib.unified_diff(old_lines, new_lines, fromfile="версия A", tofile="версия B", lineterm="")
    )
    return {"summary": counts, "changes": changes, "diff": unified}


def _resolve_side(payload: dict[str, Any], side: str) -> str:
    document = resolve_document(payload, f"document_{side}", f"document_{side}_base64")
    if len(document) < MIN_DOCUMENT_CHARS:
        raise ValueError(f"Текст версии «{side}» слишком короткий: нужно от {MIN_DOCUMENT_CHARS} символов.")
    return document


def handle_diff(payload: dict[str, Any]) -> dict[str, Any]:
    """Валидирует запрос и сравнивает две редакции документа."""
    old = _resolve_side(payload, "a")
    new = _resolve_side(payload, "b")
    result = diff_documents(old, new)
    return {
        "status": "draft",
        "summary": result["summary"],
        "changes": result["changes"],
        "diff": result["diff"],
        "checked_on": datetime.now(timezone.utc).date().isoformat(),
        "disclaimer": LEGAL_DISCLAIMER,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Сравнение двух редакций договора (lite-redlining)")
    parser.add_argument("old_file", type=Path, help="Старая редакция: .txt (UTF-8) или .docx")
    parser.add_argument("new_file", type=Path, help="Новая редакция: .txt (UTF-8) или .docx")
    args = parser.parse_args()
    for path in (args.old_file, args.new_file):
        if not path.exists():
            print(f"Ошибка: файл не найден: {path}", file=sys.stderr)
            return 1
    try:
        old = read_document_file(args.old_file.read_bytes(), args.old_file.name)
        new = read_document_file(args.new_file.read_bytes(), args.new_file.name)
        result = handle_diff({"document_a": old, "document_b": new})
    except ValueError as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
