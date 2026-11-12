#!/usr/bin/env python3
"""Извлечение текста из DOCX без сторонних библиотек.

DOCX — это ZIP-архив с XML внутри: текст документа лежит в word/document.xml
в элементах w:t, абзацы — w:p, таблицы — w:tbl. Этого достаточно, чтобы
стандартными zipfile и xml.etree.ElementTree получить плоский текст
для проверки договора или извлечения реквизитов. Таблицы (спецификации,
цены) сохраняются строками вида «| ячейка | ячейка |» на своём месте
в документе, чтобы соседство с поясняющими абзацами не терялось.

PDF намеренно не поддерживается: надёжно извлечь текст из PDF средствами
стандартной библиотеки нельзя, а кривое извлечение хуже честной ошибки.

Безопасность: ограничения на размеры, запрет DTD/сущностей до парсера,
контроль за повторяющимися членами архива, отказ от управляющих символов.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOCX_MAGIC = b"PK\x03\x04"
PDF_MAGIC = b"%PDF"

# Лимиты ресурсов: честная ошибка вместе перегрузки парсеров.
MAX_INPUT_FILE_BYTES = 2 * 1024 * 1024
MAX_DOCX_BYTES = 10 * 1024 * 1024
MAX_DOCX_XML_BYTES = 5 * 1024 * 1024
MAX_XML_MARKUP_TOKENS = 200_000
MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_DOCX_ENTRIES = 5000

LOCAL_LLM_HOSTS = {"127.0.0.1", "localhost", "::1"}

PDF_ERROR = (
    "PDF не поддерживается: извлечь текст из PDF без сторонних библиотек нельзя. "
    "Сохраните документ как DOCX или скопируйте текст в поле «document»."
)
FORMAT_ERROR = (
    "Неизвестный формат файла: поддерживается DOCX. "
    "Либо передайте текст напрямую в поле «document»."
)


def _local_name(tag: str) -> str:
    """Локальное имя тега: и «w:p», и «{ns}p» дают «p»."""
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _has_control_characters(text: str) -> bool:
    return any(ord(char) < 32 and char not in "\t\n\r" for char in text)


def document_fingerprint(document: str) -> str:
    """SHA-256 текста документа: ссылка на проанализированную версию без хранения самого текста."""
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def ensure_document_transfer_allowed() -> None:
    """Разрешает отправку текста договора во внешний LLM только при явном согласии.

    Локальные LLM (loopback) не требуют согласия. Для облачных нужен
    ALLOW_CLOUD_DOCUMENTS=true — иначе текст договора наружу не уходит.
    """
    base = os.environ.get("LLM_BASE_URL", "").strip()
    if not base:
        return
    try:
        from urllib.parse import urlsplit
        host = (urlsplit(base).hostname or "").lower()
    except ValueError:
        host = ""
    if host in LOCAL_LLM_HOSTS:
        return
    if os.environ.get("ALLOW_CLOUD_DOCUMENTS", "").strip().lower() == "true":
        return
    raise ValueError(
        "Отправка текста договора во внешний облачный сервис запрещена. "
        "Для явного согласия задайте ALLOW_CLOUD_DOCUMENTS=true или используйте локальную модель."
    )


def parse_demo_flag(payload: dict[str, Any]) -> bool:
    """Строгий разбор demo-флага: только явный True считается демонстрацией."""
    return isinstance(payload, dict) and payload.get("demo") is True


def quote_span(document: str, quote: str) -> dict[str, Any] | None:
    """Позиция цитаты в исходном тексте: точное вхождение или с нормализацией пробелов.

    Никакие позиции не выдумываются: при отсутствии вхождения возвращается None.
    """
    quote = (quote or "").strip()
    if not quote or not isinstance(document, str):
        return None
    start = document.find(quote)
    if start >= 0:
        return {"start": start, "end": start + len(quote), "match_mode": "exact"}
    normalized_quote = " ".join(quote.split())
    if not normalized_quote:
        return None
    # Каждый пробел нормализованной цитаты сопоставляется с любой run-ой пробелов оригинала.
    pattern = re.compile(re.escape(normalized_quote).replace("\\ ", "\\s+"))
    match = pattern.search(document)
    if match is None:
        return None
    return {
        "start": match.start(),
        "end": match.end(),
        "match_mode": "whitespace_normalized",
    }


def _decode_xml_bytes(data: bytes) -> str:
    """Декодирует XML документа; опора только на BOM, декларации не доверяем."""
    if data.startswith(b"\xff\xfe\x00\x00") or data.startswith(b"\x00\x00\xfe\xff"):
        encoding = "utf-32"
    elif data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        encoding = "utf-16"
    elif data.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ValueError("Основной XML документа должен быть в UTF-8/UTF-16/UTF-32.") from exc


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    """Собирает текст абзаца в порядке следования, включая tab и разрывы строк."""
    parts: list[str] = []
    for node in paragraph.iter():
        local = _local_name(node.tag)
        if local == "t":
            parts.append(node.text or "")
        elif local == "tab":
            parts.append("\t")
        elif local == "br":
            parts.append("\n")
    return "".join(parts).strip()


def _cell_text(cell: ElementTree.Element) -> str:
    """Текст ячейки таблицы: абзацы внутри ячейки соединяются пробелом."""
    parts = [
        text
        for paragraph in cell.iter()
        if _local_name(paragraph.tag) == "p" and (text := _paragraph_text(paragraph))
    ]
    return " ".join(parts).strip()


def _table_lines(table: ElementTree.Element) -> list[str]:
    """Превращает таблицу в строки вида «| ячейка | ячейка |»."""
    lines: list[str] = []
    for row in table:
        if _local_name(row.tag) != "tr":
            continue
        cells = [
            _cell_text(cell)
            for cell in row
            if _local_name(cell.tag) == "tc"
        ]
        if any(cells):
            lines.append("| " + " | ".join(cells) + " |")
    return lines


def _collect_lines(container: ElementTree.Element, lines: list[str]) -> None:
    """Обходит детей контейнера, сохраняя порядок абзацев и таблиц."""
    for child in container:
        local = _local_name(child.tag)
        if local == "p":
            text = _paragraph_text(child)
            if text:
                lines.append(text)
        elif local == "tbl":
            lines.extend(_table_lines(child))
        elif len(child) and local != "sectPr":
            # Обёртки вроде w:sdt: не теряем их содержимое.
            _collect_lines(child, lines)


def extract_docx_text(data: bytes) -> str:
    """Извлекает плоский текст из байтов DOCX-файла, сохраняя таблицы и их место."""
    if len(data) > MAX_DOCX_BYTES:
        raise ValueError(f"DOCX-архив превышает допустимый размер ({MAX_DOCX_BYTES} байт).")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Файл повреждён или не является DOCX-документом.") from None
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError("В архиве DOCX есть повторяющиеся члены: файл отклонён.")
    try:
        xml_data = archive.read("word/document.xml")
    except KeyError:
        raise ValueError("В файле нет word/document.xml: это не DOCX-документ Word.") from None
    if len(xml_data) > MAX_DOCX_XML_BYTES:
        raise ValueError(f"Основной XML документа превышает допустимый размер ({MAX_DOCX_XML_BYTES} байт).")
    xml_text = _decode_xml_bytes(xml_data)
    # DTD и сущности отклоняются до парсера — в любой кодировке.
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise ValueError("DTD и объявления сущностей внутри DOCX запрещены.")
    if xml_text.count("<") > MAX_XML_MARKUP_TOKENS:
        raise ValueError("В документе слишком много элементов разметки.")
    try:
        root = ElementTree.fromstring(xml_data)
    except ElementTree.ParseError:
        raise ValueError("Не удалось разобрать XML внутри DOCX-файла.") from None
    if _local_name(root.tag) != "document":
        raise ValueError("Корневой элемент не «document»: это не документ Word.")
    body = None
    for child in root:
        if _local_name(child.tag) == "body":
            body = child
            break
    container = body if body is not None else root
    lines: list[str] = []
    _collect_lines(container, lines)
    return "\n".join(lines)


def decode_document_base64(encoded: str) -> str:
    """Декодирует base64-DOCX из запроса и извлекает из него текст."""
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Поле «document_base64» должно быть корректным base64.") from None
    if len(data) > MAX_DOCX_BYTES:
        raise ValueError(f"Документ превышает допустимый размер ({MAX_DOCX_BYTES} байт).")
    if data.startswith(PDF_MAGIC):
        raise ValueError(PDF_ERROR)
    if not data.startswith(DOCX_MAGIC):
        raise ValueError(FORMAT_ERROR)
    return extract_docx_text(data)


def resolve_document(payload: dict[str, Any], text_field: str = "document", base64_field: str = "document_base64") -> str:
    """Возвращает текст договора из запроса: текстовое поле или DOCX в base64.

    Однозначность обязательна: если заполнены оба поля — отказ.
    """
    if not isinstance(payload, dict):
        raise ValueError("Запрос должен быть JSON-объектом с полем документа.")
    document = payload.get(text_field, "")
    encoded = payload.get(base64_field, "")
    has_document = bool(isinstance(document, str) and document.strip())
    has_encoded = bool(isinstance(encoded, str) and encoded.strip())
    if has_document and has_encoded:
        raise ValueError(f"Поля «{text_field}» и «{base64_field}» взаимоисключающи: передайте одно.")
    if has_document:
        if _has_control_characters(document):
            raise ValueError("Текст документа содержит управляющие символы.")
        return document.strip()
    if "document" in payload and not isinstance(document, str):
        raise ValueError(f"Поле «{text_field}» должно быть строкой.")
    if has_encoded:
        return decode_document_base64(encoded.strip()).strip()
    raise ValueError(
        f"Передайте договор: поле «{text_field}» (текст) или «{base64_field}» (DOCX в base64)."
    )


def read_document_file(path_bytes: bytes, name: str) -> str:
    """Читает договор из файла для CLI: .docx — через ZIP/XML, .pdf — честная ошибка."""
    if len(path_bytes) > MAX_INPUT_FILE_BYTES:
        raise ValueError(f"Файл превышает допустимый размер ({MAX_INPUT_FILE_BYTES} байт).")
    lowered = name.lower()
    if lowered.endswith(".pdf") or path_bytes.startswith(PDF_MAGIC):
        raise ValueError(PDF_ERROR)
    if lowered.endswith(".docx") or path_bytes.startswith(DOCX_MAGIC):
        return extract_docx_text(path_bytes)
    try:
        text = path_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Текстовый файл должен быть в кодировке UTF-8.") from None
    if _has_control_characters(text):
        raise ValueError("Файл содержит управляющие символы.")
    return text


def read_document_path(path: Path) -> str:
    """Читает договор из пути CLI: размер проверяется до чтения содержимого."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Файл не найден: {path}")
    if path.stat().st_size > MAX_INPUT_FILE_BYTES:
        raise ValueError(f"Файл «{path.name}» превышает допустимый размер ({MAX_INPUT_FILE_BYTES} байт).")
    return read_document_file(path.read_bytes(), path.name)
