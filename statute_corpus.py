"""Read bounded statutory extracts; integrity is not legal or temporal verification."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from agent import Source, current_check_date, validate_as_of

ROOT = Path(__file__).parent / 'fixtures' / 'statute_corpus'
HOSTS = {'secondary_statutory_reproduction': {'www.consultant.ru', 'base.garant.ru'},
         'secondary_regulatory_reproduction': {'www.consultant.ru', 'base.garant.ru'},
         'government_consolidated_copy': {'mintrud.gov.ru'},
         'supreme_court_plenum': {'www.vsrf.ru', 'vsrf.ru'}}
MAX_FILE_BYTES = 4 * 1024 * 1024


def _read(root, name, digest=None):
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9_-]+\.json', name):
        raise ValueError('Недопустимый файл корпуса.')
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Файл вне корпуса.')
    with path.open('rb') as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES or (digest and hashlib.sha256(raw).hexdigest() != digest):
        raise ValueError('Нарушена целостность корпуса.')
    return json.loads(raw)


def _url(value, kind):
    url = urlsplit(value)
    if (url.scheme != 'https' or url.hostname not in HOSTS.get(kind, set())
            or url.username or url.password or url.port not in (None, 443)):
        raise ValueError('Недопустимый источник.')


def corpus_report(card_ids=None, as_of=None, *, corpus_dir=None, include_text=False):
    date = validate_as_of(as_of) or current_check_date()
    if card_ids is not None and (not isinstance(card_ids, list) or len(card_ids) > 1000
            or any(not isinstance(cid, str) or len(cid) > 100 for cid in card_ids)):
        raise ValueError('card_ids должен быть списком идентификаторов норм (не более 1000).')
    root = Path(corpus_dir) if corpus_dir is not None else ROOT
    selected = None if card_ids is None else set(card_ids)
    registry = json.loads((Path(__file__).parent / 'fixtures/legal_coverage.json').read_text(encoding='utf-8'))
    registered_cards = {card['id']: card for card in registry['cards']}
    registered_acts = {act['id']: act for act in registry['acts']}
    registered_sources = {source['id']: source for source in registry['sources']}
    if selected is not None and not selected <= registered_cards.keys():
        raise ValueError('Неизвестный card_id.')
    entries, errors = [], []
    try:
        manifest = _read(root, 'manifest.json')
        groups = manifest['groups']
        if type(manifest['schema_version']) is not int or manifest['schema_version'] != 1 or not isinstance(groups, list) or len(groups) > 50:
            raise ValueError('Неверный манифест.')
    except (ValueError, OSError, TypeError, KeyError):
        return {'entries': [], 'errors': ['Корпус нормативных текстов недоступен.'],
                'status': 'unavailable', 'as_of': date, 'verified': False}
    seen = set()
    for index, group in enumerate(groups):
        try:
            sections = _read(root, group['file'], group['sha256'])['sections']
            if not isinstance(sections, list) or len(sections) > 500:
                raise ValueError('Неверный набор статей.')
            staged = []
            group_ids = set()
            for section in sections:
                cid = section['card_id']
                if not isinstance(cid, str) or cid in seen or cid in group_ids:
                    raise ValueError('Повторный идентификатор.')
                group_ids.add(cid)
                if selected is not None and cid not in selected:
                    continue
                kind, text = section['kind'], section['text']
                _url(section['url'], kind)
                card = registered_cards[cid]
                act = registered_acts[card['act_id']]
                source = registered_sources[act['source_ids'][0]]
                if (section['act_id'] != card['act_id'] or section['provision'] != card['provision']
                        or section['act_id'] != group['act_id'] or kind != source['kind']
                        or not section['url'].startswith(source['url'])):
                    raise ValueError('Источник не соответствует акту и норме.')
                captured = validate_as_of(section['captured_on'])
                if captured is None or not isinstance(text, str) or not 30 <= len(text) <= 200000:
                    raise ValueError('Неверное извлечение.')
                if hashlib.sha256(text.encode('utf-8')).hexdigest() != section['sha256']:
                    raise ValueError('Неверный хеш текста.')
                if section['heading'] not in text or len(section['heading']) < 10:
                    raise ValueError('Нет обозначения нормы.')
                if not re.fullmatch(r'[a-f0-9]{64}', section['response_sha256']):
                    raise ValueError('Нет хеша полученного ответа.')
                entry = {key: section[key] for key in
                         ('card_id', 'act_id', 'provision', 'title', 'heading', 'url', 'kind',
                          'captured_on', 'sha256', 'response_sha256', 'edition_label')}
                entry.update(text_received=True, integrity_checked=True,
                             heading_checked=True, capture_kind='html_normative_text_extract',
                             raw_response_archived=False, original_publication=False,
                             complete_article_verified=False, period_supported=False, verified=False,
                             temporal_status=('incomplete_history' if date >= captured else
                                              'later_capture_unusable_for_historical_review'),
                             gaps=['История изменений, полнота извлечения, переходные положения и действие на дату ситуации не подтверждены.',
                                   'Сохранён извлечённый текст, исходный HTTP-ответ не архивирован.'])
                if include_text:
                    entry['text'] = text
                staged.append(entry)
            seen.update(group_ids)
            entries.extend(staged)
        except (ValueError, OSError, TypeError, KeyError, UnicodeError, AttributeError):
            errors.append(f'Группа {index + 1} исключена: проверка корпуса не пройдена.')
    received = {entry['card_id'] for entry in entries}
    return {'entries': entries, 'errors': errors, 'as_of': date,
            'status': 'partial_statutory_texts' if entries else 'no_usable_texts',
            'text_received_count': len(entries),
            'missing_card_ids': sorted(selected - received) if selected is not None else [],
            'refresh_gaps': manifest.get('gaps', []),
            'original_publication_count': 0, 'period_supported': False, 'verified': False,
            'limitation': 'Полученные тексты — материал для исследования, не принятые редакции законодательства.'}


REVIEW_PROVISIONS = {
    'gk1': ['432', '431', '165.1', '309', '310', '314', '328', '330', '333', '395', '401', '450.1', '453'],
    'consumer': ['16', '13', '15', '17', '18', '19', '25', '26.1', '28', '29', '32'],
    'tk': ['57', '58', '59', '67', '70', '74', '136', '80', '81', '84.1', '140', '236', '392'],
}
SPECIAL_PROVISIONS = {
    'supply': ['506', '508', '513', '516', '518', '523'],
    'services': ['779', '781', '782', '783'],
    'lease': ['607', '609', '610', '614', '616', '619', '620', '621', '622'],
    'work': ['702', '708', '709', '717', '720', '723', '724', '725'],
}


def statutory_review_sources(profile, contract_type, as_of):
    """Bounded, article-specific context; later captures are never historic evidence."""
    plans = [('tk', 'LAW-05', REVIEW_PROVISIONS['tk'])] if profile == 'employment' else [
        ('gk1', 'LAW-02', REVIEW_PROVISIONS['gk1'])]
    if profile == 'consumer':
        plans.append(('consumer', 'LAW-04', REVIEW_PROVISIONS['consumer']))
    if profile != 'employment' and contract_type in SPECIAL_PROVISIONS:
        plans.append(('gk2', 'LAW-03', SPECIAL_PROVISIONS[contract_type]))
    requested = [f'{mid}:{p}' for _, mid, ps in plans for p in ps]
    report = corpus_report(requested, as_of, include_text=True)
    by_id = {entry['card_id']: entry for entry in report['entries']
             if entry['temporal_status'] == 'incomplete_history'}
    sources = []
    # Each material retains its own article URL; never attribute one article to another URL.
    # One article from each applicable act comes first; special rules must not be crowded out.
    ordered = [(mid, ps[0]) for _, mid, ps in plans]
    ordered += [(mid, p) for _, mid, ps in plans for p in ps[1:]]
    for mid, p in ordered:
        entry = by_id.get(f'{mid}:{p}')
        if entry:
            sources.append(Source(entry['title'] + ' [справочная копия; история не подтверждена]',
                                  entry['url'], entry['text'][:12000], 'local statutory extract'))
        if len(sources) >= 3:
            break
    return sources, report


def handle_corpus(payload):
    if not isinstance(payload, dict) or set(payload) - {'card_ids', 'as_of', 'include_text'}:
        raise ValueError('Допустимы card_ids, as_of и include_text.')
    include = payload.get('include_text', False)
    if not isinstance(include, bool):
        raise ValueError('include_text должен быть true или false.')
    ids = payload.get('card_ids')
    if include and (not isinstance(ids, list) or not 1 <= len(ids) <= 50):
        raise ValueError('Для текста задайте от 1 до 50 card_ids.')
    return corpus_report(ids, payload.get('as_of'), include_text=include)


def search_statutory_sources(question, as_of, limit=2):
    """Conservative local retrieval by an identified act and article/title words."""
    if not isinstance(question, str) or type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError('Invalid local statutory search')
    aliases = {
        'pd152': r'(?<!\d)152\s*[-–]?\s*фз|персональн',
        'gk1': r'\bгк\b|гражданск\w* кодекс|договор|исков\w* давност|убытк|неустойк',
        'gk2': r'аренд|поставк|подряд|оказан\w* услуг',
        'consumer': r'(?<!\d)2300\s*[-–]\s*1(?!\d)|потребител',
        'tk': r'\bтк\b|трудов|работодател|увольнен|заработн',
        'gpk': r'\bгпк\b|гражданск\w* процессуальн',
        'apk': r'\bапк\b|арбитражн\w* процессуальн',
        'nk2': r'\bнк\b|госпошлин|государственн\w* пошлин',
        'info149': r'(?<!\d)149\s*[-–]?\s*фз',
        'esign63': r'(?<!\d)63\s*[-–]?\s*фз|электронн\w* подпис',
        'koap': r'коап|административн\w* ответственност',
    }
    normalized = question.lower()
    acts = {aid for aid, pattern in aliases.items() if re.search(pattern, normalized)}
    if not acts:
        return []
    articles = set(re.findall(r'(?:стат(?:ья|ьи|ье|ей|ью)|ст\.)\s*(\d+(?:\.\d+)?)', normalized))
    words = set(re.findall(r'[а-яё]{4,}', normalized)) - {'статья', 'статьи', 'кодекс', 'российской', 'федерации'}
    report = corpus_report(as_of=as_of, include_text=True)
    ranked = []
    for entry in report['entries']:
        if entry['act_id'] not in acts or entry['temporal_status'] != 'incomplete_history':
            continue
        if articles and entry['provision'] not in articles:
            continue
        heading = entry['heading'].lower()
        overlap = sum(word in heading for word in words)
        if not articles and overlap == 0:
            continue
        ranked.append((overlap, entry))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]['card_id']))
    selected, seen_acts = [], set()
    for _, entry in ranked:
        if entry['act_id'] not in seen_acts:
            selected.append(entry)
            seen_acts.add(entry['act_id'])
    selected += [entry for _, entry in ranked if entry not in selected]
    return [Source(entry['title'] + ' [справочная копия; получено ' + entry['captured_on'] +
                   '; история не подтверждена]', entry['url'], entry['text'][:9000], 'local statutory extract')
            for entry in selected[:limit]]


if __name__ == '__main__':
    report = corpus_report()
    print(json.dumps({key: value for key, value in report.items() if key != 'entries'},
                     ensure_ascii=False, indent=2))
    raise SystemExit(1 if report['errors'] else 0)
