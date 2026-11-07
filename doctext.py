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
import ipaddress
import os
import zipfile
from urllib.parse import urlparse
from typing import Any
from xml.etree import ElementTree

WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOCX_MAGIC = b"PK\x03\x04"
PDF_MAGIC = b"%PDF"
MAX_DOCX_BYTES = 10 * 1024 * 1024
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_DOCX_ENTRIES = 2000
MAX_INPUT_FILE_BYTES = 10 * 1024 * 1024

PDF_ERROR = (
    "PDF не поддерживается: извлечь текст из PDF без сторонних библиотек нельзя. "
    "Сохраните документ как DOCX или скопируйте текст в поле «document»."
)
FORMAT_ERROR = (
    "Неизвестный формат файла: поддерживается DOCX. "
    "Либо передайте текст напрямую в поле «document»."
)

def ensure_document_transfer_allowed() -> None:
    """Require explicit opt-in before sending contract text to a remote LLM."""
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip()
    hostname = urlparse(base_url).hostname or ""
    is_local = hostname.lower() == "localhost"
    if not is_local:
        try:
            is_local = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_local = False
    consent = os.getenv("ALLOW_CLOUD_DOCUMENTS", "").strip().lower() in {"1", "true", "yes"}
    if not is_local and not consent:
        raise ValueError(
            "Отправка текста договора во внешний LLM заблокирована. "
            "Для локальной модели задайте LLM_BASE_URL на localhost/loopback; "
            "для осознанного использования облачной модели установите ALLOW_CLOUD_DOCUMENTS=true."
        )



def extract_docx_text(data: bytes) -> str:
    """Извлекает текст DOCX с ограничениями против ZIP-бомб и чрезмерных архивов."""
    if len(data) > MAX_DOCX_BYTES:
        raise ValueError(f"DOCX превышает допустимый размер {MAX_DOCX_BYTES} байт.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_DOCX_ENTRIES:
                raise ValueError(f"DOCX содержит слишком много элементов (максимум {MAX_DOCX_ENTRIES}).")
            total_size = sum(item.file_size for item in entries)
            if total_size > MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES:
                raise ValueError("Распакованный DOCX превышает допустимый общий размер.")
            try:
                info = archive.getinfo("word/document.xml")
            except KeyError:
                raise ValueError("В файле нет word/document.xml: это не DOCX-документ Word.") from None
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise ValueError("Основной XML документа превышает допустимый размер.")
            if info.flag_bits & 0x1:
                raise ValueError("Зашифрованные DOCX-файлы не поддерживаются.")
            xml_data = archive.read(info)
    except zipfile.BadZipFile:
        raise ValueError("Файл повреждён или не является DOCX-документом.") from None
    except (RuntimeError, OSError, EOFError):
        raise ValueError("Не удалось безопасно прочитать DOCX-архив.") from None
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
    if len(encoded) > ((MAX_DOCX_BYTES + 2) // 3) * 4:
        raise ValueError(f"Поле «document_base64» превышает допустимый размер DOCX {MAX_DOCX_BYTES} байт.")
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


def parse_demo_flag(payload: dict[str, Any]) -> bool:
    """Accept only a real JSON boolean for the demo switch."""
    value = payload.get("demo", False)
    if not isinstance(value, bool):
        raise ValueError("Поле «demo» должно быть логическим значением true или false.")
    return value


def read_document_path(path: Any) -> str:
    """Check file size before allocating memory for CLI document input."""
    from pathlib import Path

    file_path = Path(path)
    size = file_path.stat().st_size
    if size > MAX_INPUT_FILE_BYTES:
        raise ValueError(f"Файл превышает допустимый размер {MAX_INPUT_FILE_BYTES} байт.")
    return read_document_file(file_path.read_bytes(), file_path.name)
