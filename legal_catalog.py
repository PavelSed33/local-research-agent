"""Select researched review questions, without asserting current-law coverage."""
import json
from pathlib import Path

from agent import validate_as_of

CONTRACT_TYPES = ('general', 'supply', 'lease', 'services', 'work')


def review_checklist(profile: str, contract_type: str, as_of: str) -> dict:
    if profile not in {'business', 'consumer', 'employment'}:
        raise ValueError('Неизвестный профиль проверки.')
    if contract_type not in CONTRACT_TYPES:
        raise ValueError('contract_type: general, supply, lease, services или work.')
    if profile == 'employment' and contract_type != 'general':
        raise ValueError('Для трудового сценария используйте contract_type=general.')
    if validate_as_of(as_of) is None:
        raise ValueError('Требуется дата as_of.')
    catalog = json.loads((Path(__file__).parent / 'fixtures' / 'contract_rule_catalog.json').read_text(encoding='utf-8'))
    sources = {source['id']: source for source in catalog['sources']}
    selected, excluded, not_yet_in_force = [], [], []
    for rule in catalog['rules']:
        if profile not in rule['profiles'] or contract_type not in rule['contract_types']:
            continue
        if rule.get('not_before') and as_of < rule['not_before']:
            not_yet_in_force.append(rule['id'])
            continue
        # Source dates are a conservative evidence cutoff, not effective dates.
        eligible = [sources[sid] for sid in rule['source_ids'] if sources[sid]['source_date'] <= as_of]
        if not eligible:
            excluded.append(rule['id'])
            continue
        selected.append({**rule, 'source_ids': [source['id'] for source in eligible], 'sources': eligible, 'verified': False,
                         'status': 'research_question_not_legal_finding'})
    return {'profile': profile, 'contract_type': contract_type, 'as_of': as_of,
            'rules': selected, 'excluded_later_source_rules': excluded,
            'excluded_not_yet_effective_rules': not_yet_in_force,
            'verified': False, 'lawyer_reviewed': False,
            'limitation': 'Вопросы для исследования, не выводы о нарушении. Дата источника не является датой действия нормы; исторические редакции и актуальность требуют проверки.'}
