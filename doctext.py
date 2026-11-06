#!/usr/bin/env python3
"""Извлечение текста из DOCX без сторонних библиотек.

DOCX — это ZIP-архив с XML внутри: текст документа лежит в word/document.xml
в элементах w:t, абзацы — w:p. Этого достаточно, чтобы стандартными
zipfile и xml.etree.ElementTree получить плоский текст для проверки
договора или извлечения реквизитов.

PDF намеренно не поддерживается: надёжно извлечь текст из PDF средствами
стандартной библиотеки нельзя, а кривое извлечение хуже честной ошибки.
"""

from __future__ import annotations

import base64
import binascii
import io
import zipfile
from typing import Any
from xml.etree import ElementTree

WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOCX_MAGIC = b"PK\x03\x04"
PDF_MAGIC = b"%PDF"

PDF_ERROR = (
    "PDF не поддерживается: извлечь текст из PDF без сторонних библиотек нельзя. "
    "Сохраните документ как DOCX или скопируйте текст в поле «document»."
)
FORMAT_ERROR = (
    "Неизвестный формат файла: поддерживается DOCX. "
    "Либо передайте текст напрямую в поле «document»."
)


def extract_docx_text(data: bytes) -> str:
    """Извлекает плоский текст из байтов DOCX-файла (абзацы через перевод строки)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Файл повреждён или не является DOCX-документом.") from None
    try:
        xml_data = archive.read("word/document.xml")
    except KeyError:
        raise ValueError("В файле нет word/document.xml: это не DOCX-документ Word.") from None
    try:
        root = ElementTree.fromstring(xml_data)
    except ElementTree.ParseError:
        raise ValueError("Не удалось разобрать XML внутри DOCX-файла.") from None
    paragraphs: list[str] = []
    for paragraph in root.iter(WORD_NAMESPACE + "p"):
        parts = [node.text or "" for node in paragraph.iter(WORD_NAMESPACE + "t")]
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def decode_document_base64(encoded: str) -> str:
    """Декодирует base64-DOCX из запроса и извлекает из него текст."""
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Поле «document_base64» должно быть корректным base64.") from None
    if data.startswith(PDF_MAGIC):
        raise ValueError(PDF_ERROR)
    if not data.startswith(DOCX_MAGIC):
        raise ValueError(FORMAT_ERROR)
    return extract_docx_text(data)


def resolve_document(payload: dict[str, Any], text_field: str = "document", base64_field: str = "document_base64") -> str:
    """Возвращает текст договора из запроса: текстовое поле или DOCX в base64."""
    document = payload.get(text_field, "")
    if isinstance(document, str) and document.strip():
        return document.strip()
    encoded = payload.get(base64_field, "")
    if isinstance(encoded, str) and encoded.strip():
        return decode_document_base64(encoded.strip()).strip()
    raise ValueError(
        f"Передайте договор: поле «{text_field}» (текст) или «{base64_field}» (DOCX в base64)."
    )


def read_document_file(path_bytes: bytes, name: str) -> str:
    """Читает договор из файла для CLI: .docx — через ZIP/XML, .pdf — честная ошибка."""
    lowered = name.lower()
    if lowered.endswith(".pdf") or path_bytes.startswith(PDF_MAGIC):
        raise ValueError(PDF_ERROR)
    if lowered.endswith(".docx") or path_bytes.startswith(DOCX_MAGIC):
        return extract_docx_text(path_bytes)
    try:
        return path_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(FORMAT_ERROR) from None
