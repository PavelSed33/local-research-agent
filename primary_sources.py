"""Bounded original archives: integrity and identity checks are not legal acceptance."""
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent / 'fixtures/primary_sources'
HOSTS = {'vsrf.ru', 'www.vsrf.ru', 'pravo.gov.ru', 'publication.pravo.gov.ru',
         'government.ru', 'static.government.ru'}
MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
          'августа', 'сентября', 'октября', 'ноября', 'декабря']


class PrimarySourceError(ValueError):
    """Server-owned archive failure, not invalid client data."""


def _read(root, name, maximum):
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9-]+\.(?:json|pdf|txt)', name):
        raise ValueError('invalid_archive_path')
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError('archive_file_unavailable')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('archive_file_too_large')
    return raw


def _url(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError('invalid_archive_url')
    url = urlsplit(value)
    if (url.scheme != 'https' or url.hostname not in HOSTS or url.username or url.password
            or url.fragment or url.port not in (None, 443)):
        raise ValueError('invalid_archive_url')


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('invalid_archive_date')
    return date.fromisoformat(value)


def _number(value, text):
    return (isinstance(value, str) and re.fullmatch(r'\d{1,4}(?:/\d{1,4})?', value)
            and re.search(r'№\s*' + re.escape(value) + r'(?![\d/])', text))


def _normalized(value):
    return ' '.join(value.casefold().split())


def _human_day(day):
    return f'{day.day} {MONTHS[day.month-1]} {day.year}'


def _date_in_text(day, value):
    forms = (_human_day(day), day.isoformat(), day.strftime('%d.%m.%Y'))
    return any(re.search(r'(?<!\d)' + re.escape(form) + r'(?!\d)', _normalized(value)) for form in forms)


def _document_identity(row, first_page, signed):
    number = row['document_number']
    if row['material_type'] != 'official_court_decision':
        return _number(number, first_page) and _date_in_text(signed, first_page)
    return (isinstance(number, str) and re.fullmatch(r'\d{1,3}-ЭС\d{2}-\d{1,8}(?:\(\d{1,3}\))?', number)
            and re.search(r'№\s*' + re.escape(number) + r'(?![\w(])', first_page)
            and _date_in_text(signed, first_page))


def _load(root):
    manifest = json.loads(_read(root, 'manifest.json', 262144))
    if (not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int
            or manifest['schema_version'] != 1 or not isinstance(manifest.get('sources'), list)
            or not 1 <= len(manifest['sources']) <= 32):
        raise ValueError('invalid_archive_schema')
    result, seen = [], set()
    required = {'id', 'material_type', 'title', 'source_url', 'source_page', 'captured_on',
                'original_file', 'original_sha256', 'text_file', 'text_sha256', 'pages',
                'original_bytes_archived', 'all_pdf_pages_extracted', 'legal_completeness_verified',
                'current_validity_verified', 'lawyer_reviewed', 'document_number', 'signed_on',
                'identity_terms', 'edition_label', 'amendment_mentions'}
    for row in manifest['sources']:
        if not isinstance(row, dict) or set(row) - (required | {'retrieved_url', 'case_number', 'disposition_quote', 'procedural_mentions'}) or not required <= row.keys():
            raise ValueError('invalid_archive_entry')
        identity = row['id']
        if not isinstance(identity, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,79}', identity) or identity in seen:
            raise ValueError('invalid_archive_identity')
        seen.add(identity)
        if row['material_type'] not in ('supreme_court_plenum', 'official_legislative_text', 'official_court_decision'):
            raise ValueError('invalid_archive_kind')
        for field in ('title', 'edition_label'):
            if not isinstance(row[field], str) or not 1 <= len(row[field].strip()) <= 300:
                raise ValueError('invalid_archive_title')
        for field in ('source_url', 'source_page', *(['retrieved_url'] if 'retrieved_url' in row else [])):
            _url(row[field])
        signed, captured = _day(row['signed_on']), _day(row['captured_on'])
        if signed > captured or captured > date.today():
            raise ValueError('invalid_archive_chronology')
        if any(row[key] is not False for key in ('legal_completeness_verified', 'current_validity_verified', 'lawyer_reviewed')):
            raise ValueError('archive_cannot_promote_legal_trust')
        if row['original_bytes_archived'] is not True or row['all_pdf_pages_extracted'] is not True:
            raise ValueError('archive_capture_incomplete')
        originals = {}
        for field, digest_field, extension in [('original_file', 'original_sha256', 'pdf'), ('text_file', 'text_sha256', 'txt')]:
            if row[field] != f'{identity}.{extension}' or not isinstance(row[digest_field], str) or not re.fullmatch(r'[0-9a-f]{64}', row[digest_field]):
                raise ValueError('invalid_archive_fingerprint')
            raw = _read(root, row[field], 10 * 1024 * 1024 if extension == 'pdf' else 800000)
            if hashlib.sha256(raw).hexdigest() != row[digest_field]:
                raise ValueError('archive_fingerprint_mismatch')
            originals[extension] = raw
        if not originals['pdf'].startswith(b'%PDF'):
            raise ValueError('archive_original_not_pdf')
        value = originals['txt'].decode('utf-8')
        if not 20 <= len(value) <= 200000 or value != value.strip():
            raise ValueError('invalid_archive_text')
        pages = row['pages']
        if not isinstance(pages, list) or not 1 <= len(pages) <= 30:
            raise ValueError('invalid_archive_pages')
        previous = 0
        for number, page in enumerate(pages, 1):
            if (not isinstance(page, dict) or set(page) != {'page', 'start', 'end'}
                    or any(type(page[key]) is not int for key in page)
                    or page['page'] != number or not previous <= page['start'] < page['end'] <= len(value)
                    or value[previous:page['start']].strip()):
                raise ValueError('invalid_archive_pages')
            previous = page['end']
        if previous != len(value):
            raise ValueError('archive_pages_incomplete')
        terms, first_page = row['identity_terms'], value[:pages[0]['end']]
        if (not isinstance(terms, list) or not 2 <= len(terms) <= 8
                or any(not isinstance(term, str) or not 5 <= len(term) <= 500 or _normalized(term) not in _normalized(first_page) for term in terms)
                or not _document_identity(row, first_page, signed)):
            raise ValueError('archive_document_identity_mismatch')
        if row['material_type'] == 'official_court_decision':
            case, disposition = row.get('case_number'), row.get('disposition_quote')
            if (not isinstance(case, str) or not re.fullmatch(r'А\d{1,3}-\d{1,9}/\d{4}', case)
                    or not re.search(r'№\s*' + re.escape(case) + r'(?![\d/])', first_page)
                    or not isinstance(disposition, str) or not 30 <= len(disposition) <= 4000
                    or disposition not in value or not re.search(r'определила\s*:', value, re.I)
                    or disposition not in re.split(r'определила\s*:', value, flags=re.I)[-1]):
                raise ValueError('archive_court_disposition_mismatch')
        elif 'case_number' in row or 'disposition_quote' in row:
            raise ValueError('unexpected_court_metadata')
        procedural = row.get('procedural_mentions', [])
        if (not isinstance(procedural, list) or len(procedural) > 10
                or (procedural and row['material_type'] != 'official_court_decision')):
            raise ValueError('invalid_procedural_mentions')
        mention_keys = set()
        for mention in procedural:
            if not isinstance(mention, dict) or set(mention) != {'court_mention', 'decision_date', 'quote'}:
                raise ValueError('invalid_procedural_mention')
            court, quote, mentioned_day = mention['court_mention'], mention['quote'], _day(mention['decision_date'])
            if (not isinstance(court, str) or not 5 <= len(court) <= 300
                    or not isinstance(quote, str) or not 30 <= len(quote) <= 6000 or quote not in value
                    or _normalized(court) not in _normalized(quote) or row['case_number'] not in quote
                    or mentioned_day > signed or not _date_in_text(mentioned_day, quote)
                    or (court, mentioned_day) in mention_keys):
                raise ValueError('unconfirmed_procedural_mention')
            mention_keys.add((court, mentioned_day))
        mentions = row['amendment_mentions']
        if not isinstance(mentions, list) or len(mentions) > 20:
            raise ValueError('invalid_amendment_mentions')
        for mention in mentions:
            if not isinstance(mention, dict) or set(mention) != {'document_number', 'signed_on', 'quote'}:
                raise ValueError('invalid_amendment_mention')
            day, quote = _day(mention['signed_on']), mention['quote']
            if (not signed <= day <= captured or not isinstance(quote, str) or not 10 <= len(quote) <= 2000
                    or quote not in value or not _number(mention['document_number'], quote)
                    or _human_day(day) not in _normalized(quote)):
                raise ValueError('unconfirmed_amendment_mention')
        result.append({**row, 'text': value, 'integrity_checked': True, 'history_complete': False})
    return result


def load_sources(root=ROOT):
    try:
        return _load(Path(root))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError) as exc:
        raise PrimarySourceError('Архив первоисточников недоступен или не прошёл проверку целостности.') from exc


def source_catalog():
    keys = ('id', 'material_type', 'title', 'source_url', 'source_page', 'captured_on',
            'document_number', 'signed_on', 'edition_label', 'original_sha256', 'text_sha256',
            'integrity_checked', 'current_validity_verified', 'legal_completeness_verified',
            'lawyer_reviewed', 'history_complete', 'amendment_mentions')
    return {'sources': [{**{key: row[key] for key in keys},
                         **{key: row[key] for key in ('case_number', 'disposition_quote') if key in row}, 'page_count': len(row['pages']),
                         'text_char_count': len(row['text'])} for row in load_sources()],
            'legal_acceptance': False, 'external_requests_attempted': False}


def original_bytes(source, root=ROOT):
    try:
        raw = _read(Path(root), source['original_file'], 10 * 1024 * 1024)
        if not raw.startswith(b'%PDF') or hashlib.sha256(raw).hexdigest() != source['original_sha256']:
            raise ValueError('original_changed_after_catalog_read')
        return raw
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PrimarySourceError('Оригинал первоисточника не прошёл проверку целостности.') from exc
