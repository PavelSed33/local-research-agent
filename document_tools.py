"""Local PDF/OCR, redaction previews and plain DOCX drafts. No network calls."""
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from xml.sax.saxutils import escape

from doctext import MAX_INPUT_FILE_BYTES, read_document_file, document_fingerprint


def _tool_environment():
    env = dict(os.environ)
    installed = Path(__file__).resolve().parent / 'runtime/tessdata'
    if not env.get('TESSDATA_PREFIX') and (installed / 'rus.traineddata').is_file() and (installed / 'eng.traineddata').is_file():
        env['TESSDATA_PREFIX'] = str(installed)
    return env


def _decode(value):
    if not isinstance(value, str) or len(value) > ((MAX_INPUT_FILE_BYTES + 2) // 3) * 4:
        raise ValueError('Файл должен быть base64 размером до 10 МиБ.')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('Некорректный base64 файла.') from None
    if len(raw) > MAX_INPUT_FILE_BYTES:
        raise ValueError('Файл превышает 10 МиБ.')
    return raw


def capabilities():
    tools = {name: bool(shutil.which(name)) for name in ('pdfinfo', 'pdftotext', 'pdftoppm', 'tesseract')}
    languages = []
    if tools['tesseract']:
        try:
            result = subprocess.run(['tesseract', '--list-langs'], capture_output=True, timeout=3, env=_tool_environment())
            languages = [line for line in result.stdout.decode('utf-8', 'replace').splitlines() if re.fullmatch(r'[a-z_]{2,20}', line)]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {'tools': tools, 'ocr_languages': languages, 'max_pdf_pages': 30,
            'max_ocr_pages': 10, 'external_requests_attempted': False}


def parse_file(payload):
    if not isinstance(payload, dict) or set(payload) - {'filename', 'file_base64', 'ocr', 'ocr_language'}:
        raise ValueError('Допустимы filename, file_base64, ocr, ocr_language.')
    name = payload.get('filename')
    if not isinstance(name, str) or not 1 <= len(name) <= 200:
        raise ValueError('Требуется имя файла.')
    if Path(name).name != name or '\\' in name or any(ord(char) < 32 for char in name):
        raise ValueError('Имя файла не должно содержать путь или управляющие символы.')
    raw = _decode(payload.get('file_base64'))
    ocr = payload.get('ocr', False)
    if type(ocr) is not bool:
        raise ValueError('ocr должен быть true или false.')
    if not raw.startswith(b'%PDF') and not name.lower().endswith('.pdf'):
        text = read_document_file(raw, name).strip()
        pages, mode = [], 'txt_or_docx'
    else:
        text, pages, mode = _parse_pdf(raw, ocr, payload.get('ocr_language', 'rus+eng'))
    if not 1 <= len(text) <= 200000:
        raise ValueError('Разобранный текст должен содержать от 1 до 200000 символов.')
    return {'document': text, 'pages': pages, 'mode': mode,
            'original_sha256': hashlib.sha256(raw).hexdigest(),
            'document_sha256': document_fingerprint(text), 'verified': False,
            'layout_preserved': False, 'external_requests_attempted': False,
            'warning': 'Проверьте распознавание, таблицы, номера и цитаты; полнота и юридическая точность не подтверждены.'}


def _parse_pdf(raw, ocr, language):
    needed = ['pdfinfo', 'pdftotext'] + (['pdftoppm', 'tesseract'] if ocr else [])
    if any(not shutil.which(name) for name in needed):
        raise ValueError('Для PDF/OCR установите Poppler и Tesseract согласно инструкции.')
    if ocr:
        if not isinstance(language, str) or not re.fullmatch(r'[a-z_]{2,20}(?:\+[a-z_]{2,20})?', language):
            raise ValueError('Некорректный язык OCR.')
        if not set(language.split('+')) <= set(capabilities()['ocr_languages']):
            raise ValueError('Не установлен запрошенный языковой пакет OCR. Русский язык требует tesseract-ocr-rus.')
    deadline = time.monotonic() + 60
    def run(command, maximum=2 * 1024 * 1024):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('Истёк срок локального разбора PDF.')
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=min(20, remaining), env=_tool_environment())
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('Не удалось завершить локальный разбор PDF/OCR.') from None
        if result.returncode or len(result.stdout) > maximum:
            raise ValueError('PDF повреждён, зашифрован или превышает лимиты обработки.')
        return result.stdout
    with tempfile.TemporaryDirectory(prefix='legal-pdf-') as directory:
        pdf = Path(directory) / 'input.pdf'
        pdf.write_bytes(raw)
        info = run(['pdfinfo', str(pdf)]).decode('utf-8', 'replace')
        match = re.search(r'^Pages:\s+(\d+)', info, re.M)
        if not match or not 1 <= int(match[1]) <= (10 if ocr else 30):
            raise ValueError('PDF превышает лимит: 30 страниц для текста, 10 страниц для OCR.')
        extracted = run(['pdftotext', '-layout', '-enc', 'UTF-8', str(pdf), '-']).decode('utf-8')
        page_texts = extracted.split('\f')
        page_texts = page_texts[:int(match[1])]
        if len(page_texts) != int(match[1]):
            raise ValueError('Не удалось извлечь все страницы PDF.')
        rendered = False
        if ocr and any(len(page.strip()) < 20 for page in page_texts):
            run(['pdftoppm', '-r', '144', '-scale-to', '2000', '-png', str(pdf), str(Path(directory) / 'page')])
            images = sorted(Path(directory).glob('page-*.png'), key=lambda path: int(path.stem.split('-')[-1]))
            if len(images) != int(match[1]):
                raise ValueError('Не удалось получить все страницы PDF для OCR.')
            for index, image in enumerate(images):
                if len(page_texts[index].strip()) < 20:
                    page_texts[index] = run(['tesseract', str(image), 'stdout', '-l', language]).decode('utf-8')
                    rendered = True
        if any(not page.strip() for page in page_texts):
            raise ValueError('В PDF есть страницы без извлечённого текста. Включите OCR и проверьте результат.')
        pages, combined = [], ''
        for number, page in enumerate(page_texts, 1):
            if combined:
                combined += '\n\n'
            start = len(combined)
            combined += page.strip()
            pages.append({'page': number, 'start': start, 'end': len(combined)})
        return combined, pages, 'pdf_ocr' if rendered else 'pdf_text'


def redact(payload):
    """Explicit preview only: detection is incomplete; never label anonymized."""
    if not isinstance(payload, dict) or set(payload) - {'document', 'terms'}:
        raise ValueError('Допустимы document и terms.')
    text = payload.get('document')
    terms = payload.get('terms', [])
    if (not isinstance(text, str) or not 1 <= len(text) <= 200000 or not isinstance(terms, list)
            or len(terms) > 100 or any(not isinstance(term, str) or not 2 <= len(term) <= 200 for term in terms)):
        raise ValueError('Некорректный текст или список строк для скрытия.')
    patterns = [r'[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}',
                r'(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)',
                r'(?<!\d)\d{4}\s+\d{6}(?!\d)', r'(?<!\d)\d{3}-\d{3}-\d{3}\s?\d{2}(?!\d)']
    matches = []
    for pattern in patterns + [re.escape(term) for term in terms]:
        matches.extend((match.start(), match.end()) for match in re.finditer(pattern, text, re.I))
    merged = []
    for start, end in sorted(set(matches)):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    result, previous = '', 0
    for index, (start, end) in enumerate(merged, 1):
        result += text[previous:start] + f'[СКРЫТО_{index}]'
        previous = end
    result += text[previous:]
    return {'document': result, 'redaction_count': len(merged), 'review_required': True,
            'anonymization_verified': False, 'external_requests_attempted': False}


def export_docx(payload):
    if not isinstance(payload, dict) or set(payload) - {'document', 'replacements', 'title'}:
        raise ValueError('Допустимы document, replacements, title.')
    text, changes = payload.get('document'), payload.get('replacements', [])
    title = payload.get('title', 'Черновик документа')
    if (not isinstance(text, str) or not 1 <= len(text) <= 200000 or not isinstance(changes, list)
            or len(changes) > 100 or not isinstance(title, str) or len(title) > 200):
        raise ValueError('Некорректный текст, заголовок или список замен.')
    spans = []
    for change in changes:
        if not isinstance(change, dict) or set(change) != {'quote', 'replacement'}:
            raise ValueError('Для замены требуются quote и replacement.')
        quote, replacement = change['quote'], change['replacement']
        if (not isinstance(quote, str) or not quote or not isinstance(replacement, str)
                or len(replacement) > 20000 or text.count(quote) != 1):
            raise ValueError('Заменяемая цитата должна встречаться ровно один раз; замена — до 20000 символов.')
        start = text.index(quote)
        spans.append((start, start + len(quote), replacement))
    spans.sort()
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
        raise ValueError('Заменяемые фрагменты пересекаются.')
    edited = text
    for start, end, replacement in reversed(spans):
        edited = edited[:start] + replacement + edited[end:]
    if len(edited) > 200000:
        raise ValueError('Итоговый документ превышает 200000 символов.')
    def paragraph(value):
        # Do not allow XML control characters, including unpaired surrogates.
        if any((ord(char) < 32 and char not in '\t\n\r') or 0xD800 <= ord(char) <= 0xDFFF or ord(char) in (0xFFFE, 0xFFFF) for char in value):
            raise ValueError('Текст содержит недопустимые XML-символы.')
        return '<w:p><w:r><w:t xml:space="preserve">' + escape(value) + '</w:t></w:r></w:p>'
    content = paragraph(title) + paragraph('ЧЕРНОВИК — требует проверки человеком')
    content += ''.join(paragraph(line) for line in edited.splitlines())
    if spans:
        content += paragraph('ПРОТОКОЛ ПРЕДЛОЖЕННЫХ ИЗМЕНЕНИЙ')
        for start, end, replacement in spans:
            content += paragraph('Исходный текст: ' + text[start:end]) + paragraph('Предложение: ' + replacement)
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + content + '</w:body></w:document>'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml', xml)
    raw = stream.getvalue()
    return {'filename': 'draft.docx', 'document_base64': base64.b64encode(raw).decode(),
            'sha256': hashlib.sha256(raw).hexdigest(), 'replacement_count': len(spans),
            'format': 'plain_docx_with_change_protocol', 'word_track_changes': False,
            'verified': False, 'lawyer_reviewed': False, 'external_requests_attempted': False}
