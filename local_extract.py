"""Bounded local pattern extraction. No party attribution or legal interpretation."""
from datetime import datetime
import hashlib
import re

from agent import LEGAL_DISCLAIMER, current_check_date
from doctext import resolve_document
from extract import inn_checksum_ok, ogrn_checksum_ok
from review import MAX_DOCUMENT_CHARS, MIN_DOCUMENT_CHARS

SPACE = r'[ \t\u00a0\u202f]*'
IDENTIFIER = re.compile(r'(?<!\w)(ИНН|ОГРНИП|ОГРН)' + SPACE + r'[:№]?' + SPACE + r'([0-9]{1,20})(?!\w)', re.I)
AMOUNT = re.compile(r'(?<![\w.,])[-−]?(?:[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+|[0-9]+)'
                    r'(?:[.,][0-9]{1,2})?' + SPACE + r'(руб(?:лей|ля|ль)?\.?|₽|RUB|USD|EUR|€|\$)(?!\w)', re.I)
DATE = re.compile(r'(?<![0-9])(?:[0-9]{2}\.[0-9]{2}\.[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})(?![0-9])')


def handle_local_extract(payload):
    allowed = {'document', 'document_base64', 'max_matches'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError('Допустимы только document, document_base64 и max_matches.')
    inputs = [key for key in ('document', 'document_base64') if key in payload]
    if len(inputs) != 1 or not isinstance(payload[inputs[0]], str) or not payload[inputs[0]].strip():
        raise ValueError('Передайте ровно один непустой текст или DOCX в base64.')
    limit = payload.get('max_matches', 200)
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('max_matches должен быть целым числом от 1 до 500.')
    document = resolve_document(payload)
    if not MIN_DOCUMENT_CHARS <= len(document) <= 200_000:
        raise ValueError('Текст должен содержать от 20 до 200000 символов.')
    candidates = []
    for kind, pattern in [('identifier', IDENTIFIER), ('amount', AMOUNT), ('date', DATE)]:
        for index, match in enumerate(pattern.finditer(document)):
            if index > limit:
                break
            entry = {'kind': kind, 'quote': match.group(), 'value': match.group(),
                     'span': {'start': match.start(), 'end': match.end()},
                     'quote_checked': True, 'verified': False, 'lawyer_reviewed': False}
            if kind == 'identifier':
                entry['kind'] = match[1].lower()
                entry['value'] = match[2]
                lengths = {'инн': {10, 12}, 'огрн': {13}, 'огрнип': {15}}
                entry['checksum_matches'] = len(match[2]) in lengths[entry['kind']] and (
                    inn_checksum_ok(match[2]) if entry['kind'] == 'инн' else ogrn_checksum_ok(match[2]))
                entry['registry_checked'] = False
            elif kind == 'amount':
                unit = match[1].upper()
                entry['currency'] = 'USD' if unit in {'USD', '$'} else 'EUR' if unit in {'EUR', '€'} else 'RUB'
            else:
                try:
                    date = datetime.strptime(match.group(), '%Y-%m-%d' if '-' in match.group() else '%d.%m.%Y')
                    entry.update(calendar_valid=True, normalized_date=date.date().isoformat())
                except ValueError:
                    entry.update(calendar_valid=False, normalized_date=None)
            candidates.append(entry)
    candidates.sort(key=lambda row: row['span']['start'])
    matches = candidates[:limit]
    warnings = []
    for index, entry in enumerate(matches):
        if entry.get('checksum_matches') is False:
            warnings.append({'match_index': index, 'code': 'identifier_checksum_mismatch'})
        if entry.get('calendar_valid') is False:
            warnings.append({'match_index': index, 'code': 'invalid_calendar_date'})
    return {'status': 'draft', 'mode': 'local_patterns', 'demo': False,
            'verified': False, 'lawyer_reviewed': False, 'external_requests_attempted': False,
            'document_persisted': False, 'checked_on': current_check_date(),
            'text_sha256': hashlib.sha256(document.encode('utf-8')).hexdigest(),
            'text_char_count': len(document), 'span_reference': 'resolved_text_after_outer_whitespace_strip',
            'span_unit': 'unicode_code_points',
            'matches': matches, 'truncated': len(candidates) > limit, 'max_matches': limit,
            'warnings': warnings, 'disclaimer': LEGAL_DISCLAIMER,
            'limitation': 'Только явно написанные ИНН/ОГРН/ОГРНИП, суммы с обозначением валюты и числовые даты. '
                          'Полнота не гарантируется; принадлежность реквизитов, реестр, назначение суммы, сроки и применимость не определяются.'}
