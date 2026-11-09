"""Bounded original-package DOCX edits with real Word insertion/deletion markup."""
import base64
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import io
from pathlib import PurePosixPath
import re
import stat
from urllib.parse import urlsplit
from xml.dom import minidom, Node
import zipfile
import zlib

from doctext import MAX_DOCX_BYTES, MAX_DOCX_XML_BYTES, MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES, MAX_DOCX_ENTRIES, MAX_XML_MARKUP_TOKENS
from document_tools import _decode

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XML = 'http://www.w3.org/XML/1998/namespace'
REVISIONS = {'ins', 'del', 'moveFrom', 'moveTo', 'moveFromRangeStart', 'moveToRangeStart', 'rPrChange', 'pPrChange', 'tblPrChange', 'trPrChange', 'tcPrChange', 'sectPrChange'}


def _parse(raw):
    if (len(raw) > MAX_DOCX_XML_BYTES or raw.count(b'<') > MAX_XML_MARKUP_TOKENS
            or re.search(br'<!\s*(DOCTYPE|ENTITY)\b', raw.replace(b'\x00', b''), re.I)):
        raise ValueError('XML DOCX превышает лимит или содержит запрещённые объявления.')
    try:
        return minidom.parseString(raw)
    except Exception:
        raise ValueError('Не удалось разобрать XML DOCX.') from None


def _elements(node):
    return [child for child in node.childNodes if child.nodeType == Node.ELEMENT_NODE]


def _word(node, name):
    return node.namespaceURI == W and node.localName == name


def _text(node):
    return ''.join(child.data for child in node.childNodes if child.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE))


def _valid_text(value, maximum, *, empty=False):
    return (isinstance(value, str) and (empty or bool(value)) and len(value) <= maximum
            and all(32 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF for char in value))


def _package(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist(); names = [entry.filename for entry in entries]
            if (len(entries) > MAX_DOCX_ENTRIES or len(names) != len(set(names))
                    or sum(entry.file_size for entry in entries) > MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES):
                raise ValueError('DOCX превышает ограничения архива или содержит повторяющиеся части.')
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (path.is_absolute() or '..' in path.parts or '\\' in entry.filename
                        or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1
                        or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise ValueError('DOCX содержит неподдерживаемую часть архива.')
                lowered = entry.filename.casefold()
                if ('vbaproject' in lowered or lowered.startswith(('word/embeddings/', '_xmlsignatures/'))):
                    raise ValueError('Макросы, вложенные объекты и подписанные пакеты в этом режиме не поддерживаются.')
            if not {'word/document.xml', '[Content_Types].xml', '_rels/.rels'} <= set(names):
                raise ValueError('Не найден обязательный состав пакета DOCX.')
            parts = {entry.filename: archive.read(entry) for entry in entries}
            for name, data in parts.items():
                if not name.casefold().endswith(('.xml', '.rels')):
                    continue
                dom = _parse(data)
                if name == '[Content_Types].xml':
                    if b'macroenabled' in data.lower():
                        raise ValueError('Файлы Word с макросами не поддерживаются.')
                    overrides = [node for node in dom.getElementsByTagName('*') if node.localName == 'Override' and node.getAttribute('PartName') == '/word/document.xml']
                    if len(overrides) != 1 or overrides[0].getAttribute('ContentType') != 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml':
                        raise ValueError('Неподдерживаемый тип основной части DOCX.')
                if name == '_rels/.rels':
                    main = [node for node in dom.getElementsByTagName('*') if node.localName == 'Relationship' and node.getAttribute('Type').endswith('/officeDocument')]
                    if len(main) != 1 or main[0].getAttribute('Target').lstrip('/') != 'word/document.xml' or main[0].getAttribute('TargetMode') == 'External':
                        raise ValueError('Неподдерживаемая связь основной части DOCX.')

                for node in dom.getElementsByTagName('*'):
                    if node.namespaceURI == W and node.localName in REVISIONS:
                        raise ValueError('Сначала примите или отклоните существующие исправления Word в исходном файле.')
                    if _word(node, 'documentProtection') and node.getAttributeNS(W, 'enforcement') in ('1', 'true', 'on'):
                        raise ValueError('Исходный документ защищён от изменений.')
                    if node.localName == 'Relationship' and node.getAttribute('TargetMode') == 'External':
                        if (not node.getAttribute('Type').endswith('/hyperlink')
                                or urlsplit(node.getAttribute('Target')).scheme.casefold() not in ('https', 'http', 'mailto')):
                            raise ValueError('Внешние шаблоны, связи с данными и файловые ссылки не поддерживаются.')
                dom.unlink()
            return entries, parts, archive.comment
    except ValueError:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError, zlib.error):
        raise ValueError('Не удалось безопасно прочитать пакет DOCX.') from None


def _run_text(run):
    return ''.join(_text(child) for child in _elements(run) if _word(child, 't'))


def _runs(paragraph):
    for child in _elements(paragraph):
        if not (_word(child, 'pPr') or _word(child, 'r')):
            raise ValueError('Цитата находится в сложном участке: закладки, ссылки, поля и другие контейнеры не редактируются.')
        if _word(child, 'r') and any(not (_word(node, 'rPr') or _word(node, 't')) for node in _elements(child)):
            raise ValueError('Цитата находится рядом с рисунком, полем или разрывом; требуется ручная правка.')
    ancestor = paragraph.parentNode
    while ancestor is not None and ancestor.nodeType == Node.ELEMENT_NODE:
        if ancestor.namespaceURI != W or ancestor.localName not in ('body', 'tc', 'tr', 'tbl', 'document'):
            raise ValueError('Текстовые блоки и специальные контейнеры не редактируются автоматически.')
        ancestor = ancestor.parentNode
    result, offset = [], 0
    for run in _elements(paragraph):
        if _word(run, 'r'):
            value = _run_text(run)
            result.append((offset, offset + len(value), run, value)); offset += len(value)
    return result, offset


def _clone_run(dom, source, value, *, deleted=False):
    clone = source.cloneNode(False)
    for child in _elements(source):
        if _word(child, 'rPr'):
            clone.appendChild(child.cloneNode(True))
    prefix = source.prefix or 'w'
    node = dom.createElementNS(W, prefix + (':delText' if deleted else ':t'))
    node.setAttributeNS(XML, 'xml:space', 'preserve'); node.appendChild(dom.createTextNode(value))
    clone.appendChild(node)
    return clone


def redline_docx(payload):
    if not isinstance(payload, dict) or set(payload) - {'filename', 'file_base64', 'replacements', 'author'}:
        raise ValueError('Допустимы filename, file_base64, replacements и author.')
    filename, author, changes = payload.get('filename'), payload.get('author', 'Правовой агент'), payload.get('replacements')
    if (not isinstance(filename, str) or not 1 <= len(filename) <= 200 or PurePosixPath(filename).name != filename
            or '\\' in filename or not filename.casefold().endswith('.docx') or any(ord(char) < 32 for char in filename)):
        raise ValueError('Требуется имя исходного файла DOCX без пути.')
    if not _valid_text(author, 100) or not author.strip():
        raise ValueError('Автор исправлений — строка до 100 символов без управляющих знаков.')
    if not isinstance(changes, list) or not 1 <= len(changes) <= 50:
        raise ValueError('Требуется от 1 до 50 явных замен.')
    for change in changes:
        if (not isinstance(change, dict) or set(change) != {'quote', 'replacement'}
                or not _valid_text(change['quote'], 20000) or not _valid_text(change['replacement'], 20000, empty=True)
                or change['quote'] == change['replacement']):
            raise ValueError('Замена требует разные quote и replacement, до 20000 символов, без табуляций и разрывов строк.')
    raw = _decode(payload.get('file_base64'))
    entries, parts, comment = _package(raw)
    dom = _parse(parts['word/document.xml']); root = dom.documentElement
    if not _word(root, 'document') or not root.getElementsByTagNameNS(W, 'body'):
        raise ValueError('Основная часть не содержит документ Word поддерживаемого формата.')
    paragraphs = list(root.getElementsByTagNameNS(W, 'p'))
    texts = [''.join(_text(node) for node in paragraph.getElementsByTagNameNS(W, 't')) for paragraph in paragraphs]
    plans = defaultdict(list)
    for change in changes:
        occurrences = []
        for index, value in enumerate(texts):
            offset = value.find(change['quote'])
            while offset >= 0:
                occurrences.append((index, offset))
                if len(occurrences) > 1:
                    break
                offset = value.find(change['quote'], offset + 1)
            if len(occurrences) > 1:
                break
        if len(occurrences) != 1:
            raise ValueError('Каждая цитата должна встречаться ровно один раз в одном абзаце основного текста или таблицы.')
        index, start = occurrences[0]
        _runs(paragraphs[index])
        plans[index].append((start, start + len(change['quote']), change['replacement']))
    for spans in plans.values():
        spans.sort()
        if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
            raise ValueError('Цитаты замен пересекаются.')
    revision_id, timestamp = 1, datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    # Preserve all original ids, including bookmarks/comments, without reusing their numeric range.
    for node in root.getElementsByTagName('*'):
        value = node.getAttributeNS(W, 'id')
        if value.isdigit():
            revision_id = max(revision_id, int(value) + 1)
    prefix = root.prefix or 'w'
    if not root.hasAttribute('xmlns:' + prefix):
        root.setAttribute('xmlns:' + prefix, W)

    def revision(kind):
        nonlocal revision_id
        node = dom.createElementNS(W, prefix + ':' + kind)
        for key, value in [('id', str(revision_id)), ('author', author), ('date', timestamp)]:
            node.setAttributeNS(W, prefix + ':' + key, value)
        revision_id += 1
        return node

    for index, spans in plans.items():
        paragraph = paragraphs[index]; runs, total = _runs(paragraph); output = []
        def slices(start, end, *, deleted=False):
            return [_clone_run(dom, run, value[max(start,left)-left:min(end,right)-left], deleted=deleted)
                    for left, right, run, value in runs if left < end and right > start]
        cursor = 0
        for start, end, replacement in spans:
            output.extend(slices(cursor, start))
            deletion = revision('del')
            for run in slices(start, end, deleted=True):
                deletion.appendChild(run)
            output.append(deletion)
            if replacement:
                insertion = revision('ins')
                source = next(run for left, right, run, value in runs if left <= start < right)
                insertion.appendChild(_clone_run(dom, source, replacement)); output.append(insertion)
            cursor = end
        output.extend(slices(cursor, total))
        for child in list(paragraph.childNodes):
            if not (child.nodeType == Node.ELEMENT_NODE and _word(child, 'pPr')):
                paragraph.removeChild(child)
        for node in output:
            paragraph.appendChild(node)
    edited = dom.toxml(encoding='utf-8'); dom.unlink()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.comment = comment
        for entry in entries:
            archive.writestr(entry, edited if entry.filename == 'word/document.xml' else parts[entry.filename])
    output = stream.getvalue()
    if len(output) > MAX_DOCX_BYTES:
        raise ValueError('DOCX после правок превышает лимит 10 МиБ.')
    return {'filename': 'redlined.docx', 'document_base64': base64.b64encode(output).decode(),
            'source_sha256': hashlib.sha256(raw).hexdigest(), 'sha256': hashlib.sha256(output).hexdigest(),
            'replacement_count': len(changes), 'word_track_changes': True, 'tracking_setting_modified': False,
            'changed_parts': ['word/document.xml'], 'other_parts_preserved': True,
            'formatting_policy': 'unchanged_parts_preserved_insertions_inherit_first_run',
            'review_required': True, 'anonymization_verified': False, 'lawyer_reviewed': False,
            'external_requests_attempted': False,
            'warning': 'Проверьте правки в Word. Исходные метаданные, комментарии и разрешённые гиперссылки сохранены; это не обезличивание. Пагинация может измениться.'}
