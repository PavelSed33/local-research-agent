"""Verify local capture integrity, never source authenticity or legal applicability."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from agent import Source, validate_as_of, current_check_date

DEFAULT_CORPUS = Path(__file__).parent / 'fixtures' / 'legal_evidence'
MAX_CAPTURE_BYTES = 128 * 1024
ALLOWED_HOSTS = {
    'government_consolidated_copy': {'mintrud.gov.ru'},
    'supreme_court_plenum': {'vsrf.ru', 'www.vsrf.ru'},
}


def _capture(root, entry):
    """Reject path escapes, unrecognised provenance and malformed quote spans."""
    url = urlsplit(entry['url'])
    if (url.scheme != 'https' or url.hostname not in ALLOWED_HOSTS.get(entry['kind'], set())
            or url.username or url.password or url.port not in (None, 443)):
        raise ValueError('Недопустимый URL или вид источника.')
    if entry.get('capture_kind') != 'web_extract_selected_lines':
        raise ValueError('Неизвестный способ извлечения.')
    captured_on = validate_as_of(entry['captured_on'])
    if captured_on is None:
        raise ValueError('Нет даты получения.')
    document_date = validate_as_of(entry.get('document_signed_on'))
    if document_date and document_date > captured_on:
        raise ValueError('Документ датирован позже получения.')
    name = entry['file']
    if not isinstance(name, str) or Path(name).name != name or name in ('.', '..'):
        raise ValueError('Недопустимое имя файла.')
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Файл вне корпуса.')
    with path.open('rb') as stream:
        raw = stream.read(MAX_CAPTURE_BYTES + 1)
    if len(raw) > MAX_CAPTURE_BYTES:
        raise ValueError('Извлечение слишком велико.')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != entry['sha256']:
        raise ValueError('Контрольная сумма не совпадает.')
    text = raw.decode('utf-8')
    quotes = entry['quotes']
    if not isinstance(quotes, list) or not quotes or len(quotes) > 30:
        raise ValueError('Не задан ограниченный набор цитат.')
    checked = []
    for quote in quotes:
        start, end, value = quote['start'], quote['end'], quote['quote']
        if (type(start) is not int or type(end) is not int or not isinstance(value, str)
                or len(value.strip()) < 10 or not 0 <= start < end <= len(text)
                or text[start:end] != value):
            raise ValueError('Цитата и её положение в извлечении не совпадают.')
        checked.append({**quote, 'quote_checked': True, 'verified': False})
    return text, digest, captured_on, document_date, checked


def evidence_report(card_ids=None, as_of=None, *, corpus_dir=None):
    date = validate_as_of(as_of) or current_check_date()
    root = Path(corpus_dir) if corpus_dir is not None else DEFAULT_CORPUS
    try:
        manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        if not isinstance(manifest, dict):
            raise ValueError('Неверный формат корпуса.')
        entries = manifest['entries']
        if manifest.get('schema_version') != 1 or not isinstance(entries, list) or len(entries) > 100:
            raise ValueError('Неизвестный формат корпуса.')
    except (OSError, ValueError, KeyError, TypeError):
        return {'entries': [], 'errors': ['Корпус недоступен или повреждён.'],
                'status': 'unavailable', 'verified': False, 'as_of': date}
    selected = None if card_ids is None else set(card_ids)
    accepted, errors = [], []
    seen_ids = set()
    for index, entry in enumerate(entries):
        try:
            if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or entry['id'] in seen_ids:
                raise ValueError('Повторный или неверный идентификатор.')
            seen_ids.add(entry['id'])
            text, digest, captured, document_date, quotes = _capture(root, entry)
            quotes = [q for q in quotes if selected is None or q['card_id'] in selected]
            if not quotes:
                continue
            accepted.append({
                'id': entry['id'], 'title': entry['title'], 'url': entry['url'],
                'kind': entry['kind'], 'capture_kind': 'web_extract_selected_lines',
                'file': entry['file'], 'sha256': digest, 'captured_on': captured,
                'document_signed_on': document_date, 'edition_on': None,
                'raw_http_response': False, 'original_publication': False,
                'integrity_checked': True, 'quotes': quotes, 'text': text,
                'temporal_status': 'incomplete_history' if date >= captured else 'later_capture_unusable_for_historical_review',
                'period_supported': False, 'verified': False,
                'gaps': ['Сохранены выбранные строки веб-извлечения, не исходный HTTP-ответ и не полный документ.',
                         'Хеш и совпадение цитат не удостоверяют подлинность, редакцию, дату действия и применимость.']})
        except (OSError, ValueError, KeyError, TypeError, UnicodeError, OverflowError, AttributeError):
            # Never serve unvalidated text or leak a local path/error to the API.
            errors.append(f'Извлечение {index + 1} не прошло проверку; текст исключён.')
    return {'as_of': date, 'entries': accepted, 'errors': errors,
            'status': 'partial_captures' if accepted else 'no_usable_captures',
            'verified': False, 'original_publication_count': 0,
            'limitation': 'Локальные извлечения неполны; первичные публикации и история норм не подтверждены.'}


def cached_contract_sources(as_of):
    """Only a court interpretation currently belongs in the civil review context."""
    report = evidence_report(['LAW-02:310'], as_of)
    return [Source(entry['title'], entry['url'], entry['text'], 'local incomplete web extract')
            for entry in report['entries'] if entry['temporal_status'] == 'incomplete_history']


if __name__ == '__main__':
    report = evidence_report()
    for entry in report['entries']:
        entry.pop('text')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if report['errors'] else 0)
