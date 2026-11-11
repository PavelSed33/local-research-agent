#!/usr/bin/env python3
"""Экспорт текстовых результатов агента в DOCX без сторонних библиотек.

DOCX — это ZIP-архив с XML: достаточно [Content_Types].xml, _rels/.rels
и word/document.xml. Обратная операция к doctext.py: Markdown-подобный текст
превращается в документ Word, который юрист может сразу открыть и править.

Поддерживаемая разметка: «# » и «## » — заголовки, строки вида
«| ячейка | ячейка |» — таблицы, «- » — маркированные пункты,
остальное — обычные абзацы. Стили минимальные (полужирные заголовки,
границы таблиц), этого достаточно для рабочего документа.

CLI:  python export.py документ.md -o документ.docx
API:  POST /export (см. api.py)
"""

from __future__ import annotations

import argparse
import base64
import io
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from agent import LEGAL_DISCLAIMER

MAX_DOCUMENT_CHARS = 500_000

CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""


class ExportError(ValueError):
    """Понятная ошибка входных данных экспорта."""


def _paragraph(text: str, bold: bool = False, size: int | None = None, bullet: bool = False) -> str:
    """Собирает XML абзаца; size в полупунктах (24 = 12 пт)."""
    props = ""
    if bold or size:
        inner = ""
        if bold:
            inner += "<w:b/>"
        if size:
            inner += f'<w:sz w:val="{size}"/>'
        props = f"<w:rPr>{inner}</w:rPr>"
    prefix = "• " if bullet else ""
    return (
        "<w:p><w:r>"
        f"{props}"
        f'<w:t xml:space="preserve">{escape(prefix + text)}</w:t>'
        "</w:r></w:p>"
    )


def _table(rows: list[list[str]]) -> str:
    """Собирает XML таблицы с границами из строк-ячеек."""
    parts = [
        "<w:tbl><w:tblPr><w:tblBorders>"
        '<w:top w:val="single" w:sz="4"/>'
        '<w:left w:val="single" w:sz="4"/>'
        '<w:bottom w:val="single" w:sz="4"/>'
        '<w:right w:val="single" w:sz="4"/>'
        '<w:insideH w:val="single" w:sz="4"/>'
        '<w:insideV w:val="single" w:sz="4"/>'
        "</w:tblBorders></w:tblPr>"
    ]
    for row_index, cells in enumerate(rows):
        parts.append("<w:tr>")
        for cell in cells:
            parts.append(
                "<w:tc><w:tcPr><w:tcW w:w=\"2400\" w:type=\"dxa\"/></w:tcPr>"
                + _paragraph(cell, bold=(row_index == 0))
                + "</w:tc>"
            )
        parts.append("</w:tr>")
    parts.append("</w:tbl>")
    return "".join(parts)


def _table_row(line: str) -> list[str]:
    """Разбирает строку вида «| a | b |» в список ячеек."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def markdown_to_body_xml(text: str) -> str:
    """Превращает Markdown-подобный текст в содержимое w:body."""
    blocks: list[str] = []
    table_rows: list[list[str]] = []

    def flush_table() -> None:
        if table_rows:
            blocks.append(_table(table_rows))
            table_rows.clear()

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("|") and line.strip().endswith("|"):
            table_rows.append(_table_row(line))
            continue
        flush_table()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            blocks.append(_paragraph(stripped[3:], bold=True, size=26))
        elif stripped.startswith("# "):
            blocks.append(_paragraph(stripped[2:], bold=True, size=32))
        elif stripped.startswith("- "):
            blocks.append(_paragraph(stripped[2:], bullet=True))
        else:
            blocks.append(_paragraph(stripped))
    flush_table()
    if not blocks:
        raise ExportError("Документ пуст: нечего экспортировать.")
    return "".join(blocks)


def build_docx(text: str) -> bytes:
    """Собирает готовый DOCX (байты) из Markdown-подобного текста."""
    if len(text) > MAX_DOCUMENT_CHARS:
        raise ExportError(f"Документ больше {MAX_DOCUMENT_CHARS} символов — разбейте на части.")
    body = markdown_to_body_xml(text)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        archive.writestr("_rels/.rels", RELS_XML)
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def safe_filename(title: str) -> str:
    """Имя файла из заголовка: только безопасные символы, до 80 знаков."""
    cleaned = re.sub(r"[^\w\s.-]", "", title, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)[:80].strip(" .")
    return (cleaned or "документ") + ".docx"


def handle_export(payload: dict) -> dict:
    """Обработчик POST /export: Markdown → DOCX (base64)."""
    if not isinstance(payload, dict):
        raise ExportError("Тело запроса должно быть JSON-объектом.")
    document = str(payload.get("document", "")).strip()
    if not document:
        raise ExportError("Поле document обязательно: текст для экспорта (Markdown).")
    title = str(payload.get("title", "")).strip() or "документ"
    data = build_docx(document)
    return {
        "status": "draft",
        "filename": safe_filename(title),
        "docx_base64": base64.b64encode(data).decode("ascii"),
        "size_bytes": len(data),
        "note": "DOCX собран стандартной библиотекой: заголовки, абзацы, списки и таблицы. "
        "Откройте файл в Word и проверьте перед использованием.",
        "checked_on": datetime.now(timezone.utc).date().isoformat(),
        "disclaimer": LEGAL_DISCLAIMER,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Экспорт Markdown-текста в DOCX")
    parser.add_argument("file", type=Path, help="Файл с текстом (.md/.txt)")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Куда сохранить DOCX (по умолчанию — рядом с исходником)")
    args = parser.parse_args()
    if not args.file.exists():
        print(f"Ошибка: файл не найден: {args.file}", file=sys.stderr)
        return 1
    text = args.file.read_text(encoding="utf-8")
    try:
        data = build_docx(text)
    except ExportError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    output = args.output or args.file.with_suffix(".docx")
    output.write_bytes(data)
    print(f"Готово: {output} ({len(data)} байт)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
