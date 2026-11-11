"""Bounded local research workflows; user declarations never establish compliance."""
import json
from pathlib import Path

from agent import current_check_date, validate_as_of

CATALOG = Path(__file__).parent / 'fixtures' / 'legal_workflows.json'


def _workflows():
    return json.loads(CATALOG.read_text(encoding='utf-8'))['workflows']


def research_options():
    return {'scenarios': {item['id']: {key: item[key] for key in
            ('title', 'area', 'module_ids', 'questions', 'caution')} for item in _workflows()},
            'verified': False, 'lawyer_reviewed': False}


def contract_research_questions(profile, contract_type, as_of):
    """An extra checklist, not retrieved statutory text or a finding of violation."""
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    # Research date is only a conservative evidence cutoff, not a law's effective date.
    if as_of < catalog['researched_on']:
        return []
    return [{key: item[key] for key in ('id', 'title', 'card_ids', 'questions', 'caution', 'verified')}
            for item in catalog['workflows']
            if profile in item['profiles'] and contract_type in item['contract_types']]


def handle_legal_research(payload):
    from legal_coverage import coverage_report
    if not isinstance(payload, dict) or set(payload) - {'scenario', 'facts', 'as_of', 'demo'}:
        raise ValueError('Допустимы только scenario, facts, as_of и demo; документы не нужны.')
    if 'demo' in payload and not isinstance(payload['demo'], bool):
        raise ValueError('demo должен быть true или false.')
    scenario = payload.get('scenario')
    if not isinstance(scenario, str):
        raise ValueError('scenario должен быть строкой из GET /legal/research/options.')
    item = next((w for w in _workflows() if w['id'] == scenario), None)
    if item is None:
        raise ValueError('Выберите scenario из GET /legal/research/options.')
    date = validate_as_of(payload.get('as_of')) or current_check_date()
    facts = payload.get('facts', {})
    if not isinstance(facts, dict) or set(facts) - set(item['questions']):
        raise ValueError('facts содержит неизвестные ключи выбранного сценария.')
    if any(value is not None and not isinstance(value, bool) for value in facts.values()):
        raise ValueError('Факты: true, false или null.')
    missing = [{'key': key, 'question': title} for key, title in item['questions'].items()
               if facts.get(key) is None]
    unresolved = [{'key': key, 'question': title} for key, title in item['questions'].items()
                  if facts.get(key) is False]
    coverage = coverage_report(item['module_ids'], date, card_ids=item['card_ids'])
    extracts = [card['statutory_extract'] for module in coverage['modules'] for card in module['cards']
                if card['statutory_extract']]
    source_links = [{'title': entry['title'] + ' [справочная копия; история не подтверждена]',
                     'url': entry['url'], 'temporal_status': entry['temporal_status'],
                     'period_supported': False, 'verified': False} for entry in extracts]
    linked = {source['url'] for source in source_links}
    source_links.extend(source for source in coverage['sources'] if source['url'] not in linked)
    return {'scenario': scenario, 'title': item['title'], 'as_of': date,
            'checked_on': current_check_date(), 'status': 'needs_facts' if missing else 'needs_legal_review',
            'summary': item['title'] + ': план исследования; правовая квалификация не установлена.',
            'questions': missing, 'unresolved_items': unresolved,
            'declared_facts': {key: facts.get(key) for key in item['questions']},
            'research_card_ids': item['card_ids'], 'legal_coverage': coverage,
            'sources': source_links, 'caution': item['caution'],
            'verified': False, 'lawyer_reviewed': False,
            'limitation': 'Ответы — заявления пользователя. Нужны документы, проверка редакций, '
                          'исключений и применимости. Сроки, штрафы и вывод о нарушении не назначаются автоматически.'}
