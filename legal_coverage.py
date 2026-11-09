"""Expose research gaps separately from runtime/legal verification claims."""
import json
from pathlib import Path

from agent import current_check_date, validate_as_of
from legal_evidence import evidence_report
from statute_corpus import corpus_report


def coverage_report(module_ids=None, as_of=None, *, card_ids=None):
    date = validate_as_of(as_of) or current_check_date()
    data = json.loads((Path(__file__).parent / 'fixtures/legal_coverage.json').read_text(encoding='utf-8'))
    known = {module['id'] for module in data['modules']}
    if module_ids is not None and (not isinstance(module_ids, list) or
            any(not isinstance(mid, str) or mid not in known for mid in module_ids)):
        raise ValueError('module_ids должен содержать известные ID LAW-01 … LAW-09.')
    selected = known if module_ids is None else set(module_ids)
    available = {card['id'] for card in data['cards'] if card['module_id'] in selected}
    if card_ids is not None and (not isinstance(card_ids, list) or
            any(not isinstance(cid, str) or cid not in available for cid in card_ids)):
        raise ValueError('card_ids должен содержать известные нормы выбранных модулей.')
    requested = available if card_ids is None else set(card_ids)
    evidence = evidence_report(list(requested), date)
    statutory = corpus_report(list(requested), date)
    by_statute = {entry['card_id']: entry for entry in statutory['entries']}
    by_card = {}
    for entry in evidence['entries']:
        for quote in entry['quotes']:
            by_card.setdefault(quote['card_id'], []).append({
                'capture_id': entry['id'], 'kind': entry['kind'], 'url': entry['url'],
                'captured_on': entry['captured_on'], 'temporal_status': entry['temporal_status'],
                'capture_kind': entry['capture_kind'], 'file': entry['file'], 'sha256': entry['sha256'],
                'original_publication': False, 'period_supported': False, **quote, 'verified': False})
    modules = []
    for module in data['modules']:
        if module['id'] not in selected:
            continue
        cards = [card for card in data['cards'] if card['module_id'] == module['id'] and card['id'] in requested]
        # A card or an observed explanation is not an accepted statutory version.
        # This initial registry intentionally has no verifier/promotion path.
        modules.append({**module, 'status': 'research_incomplete' if cards else 'planned',
                        'planned_provision_count': len(module['planned_provisions']),
                        'research_card_count': len(cards), 'accepted_provision_count': 0,
                        'text_received_count': sum(card['id'] in by_statute for card in cards),
                        'verified': False, 'cards': [{**card, 'status': 'incomplete_history',
                            'source_found': bool(card['source_ids']), 'quote_checked': False,
                            'period_supported': False, 'verified': False,
                            'text_received': card['id'] in by_statute,
                            'statutory_extract': by_statute.get(card['id']),
                            'supporting_evidence': by_card.get(card['id'], [])} for card in cards]})
    used = {sid for module in modules for card in module['cards'] for sid in card['source_ids']}
    sources = [source for source in data['sources'] if source['id'] in used]
    return {'spec_version': data['spec_version'], 'as_of': date,
            'registry_checked_on': data['checked_on'], 'checked_on': current_check_date(),
            'modules': modules, 'sources': sources, 'verified': False,
            'statutory_corpus_summary': {key: value for key, value in statutory.items() if key != 'entries'},
            'evidence_summary': {'capture_count': len(evidence['entries']),
                                 'checked_quote_count': sum(len(e['quotes']) for e in evidence['entries']),
                                 'original_publication_count': 0, 'errors': evidence['errors'], 'verified': False},
            'limitation': 'Это очередь исследования и пробелы, не подтверждение действия норм на дату ситуации. '
                          'Ни один модуль ещё не прошёл юридическую приёмку.'}


def contract_coverage(profile, as_of):
    modules = ['LAW-05'] if profile == 'employment' else ['LAW-02', 'LAW-03']
    if profile == 'consumer':
        modules.append('LAW-04')
    result = coverage_report(modules, as_of)
    # Keep prompts bounded: full cards are available through /legal/coverage.
    result['modules'] = [{key: value for key, value in module.items() if key != 'cards'}
                         for module in result['modules']]
    result.pop('sources')
    return result

