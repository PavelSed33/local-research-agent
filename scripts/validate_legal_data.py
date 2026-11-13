"""Validate data relationships and provenance; never certify legal correctness."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from statute_corpus import corpus_report
from legal_evidence import evidence_report
from privacy_review import privacy_options
from legal_skills import load_skills


def validate():
    skills = load_skills()
    registry = json.loads((ROOT / 'fixtures/legal_coverage.json').read_text(encoding='utf-8'))
    workflows = json.loads((ROOT / 'fixtures/legal_workflows.json').read_text(encoding='utf-8'))
    benchmarks = json.loads((ROOT / 'fixtures/legal_benchmarks.json').read_text(encoding='utf-8'))
    modules = {m['id']: m for m in registry['modules']}
    acts = {a['id']: a for a in registry['acts']}
    cards = {c['id']: c for c in registry['cards']}
    sources = {s['id']: s for s in registry['sources']}
    if len(cards) != len(registry['cards']) or len(acts) != len(registry['acts']):
        raise ValueError('Duplicate card or act')
    for card in cards.values():
        act = acts[card['act_id']]
        if (card['module_id'] != act['module_id'] or card['act'] != act['title']
                or card['provision'] not in act['provisions'] or not set(card['source_ids']) <= sources.keys()):
            raise ValueError('Card/act/source mismatch: ' + card['id'])
        if card['legal_review']['approved'] or card['effective_from'] or card['effective_to']:
            raise ValueError('Research card cannot assert accepted law or unsupported effective dates')
    for module in modules.values():
        if not module['planned_provisions'] or module['acceptance']['approved']:
            raise ValueError('Empty or falsely accepted module')
        if not any(c['module_id'] == module['id'] for c in cards.values()):
            raise ValueError('No cards for module')
    ids = set()
    for workflow in workflows['workflows']:
        if workflow['id'] in ids or not set(workflow['card_ids']) <= cards.keys():
            raise ValueError('Invalid workflow references')
        ids.add(workflow['id'])
        if not workflow['questions'] or not workflow['caution'] or workflow['verified']:
            raise ValueError('Workflow lacks research limits')
    case_ids = set()
    scenario_ids = ids | {'privacy:' + name for name in privacy_options()['scenarios']}
    counts = dict.fromkeys(modules, 0)
    for case in benchmarks['cases']:
        if (case['id'] in case_ids or case['scenario'] not in scenario_ids or case['module_id'] not in counts
                or not set(case['source_card_ids']) <= cards.keys() or not case['forbidden_conclusion']
                or case['approved_legal_answer'] is not None or case['review']['approved']):
            raise ValueError('Invalid/unapproved draft benchmark')
        case_ids.add(case['id'])
        counts[case['module_id']] += 1
    if any(count < 10 for count in counts.values()):
        raise ValueError('At least ten draft cases per module required')
    corpus = corpus_report()
    evidence = evidence_report()
    if corpus['errors'] or evidence['errors']:
        raise ValueError('Invalid source corpus')
    received = {entry['card_id'] for entry in corpus['entries']}
    missing = sorted(cards.keys() - received)
    unexpected = sorted(received - cards.keys())
    if missing or unexpected:
        raise ValueError(
            'Statutory corpus coverage incomplete: '
            f'{len(missing)} missing text extracts, {len(unexpected)} unexpected card IDs; '
            f'missing IDs: {", ".join(missing[:30]) or "none"}'
            f'{" ..." if len(missing) > 30 else ""}; '
            f'unexpected IDs: {", ".join(unexpected[:30]) or "none"}'
            f'{" ..." if len(unexpected) > 30 else ""}. '
            'Do not restore quarantined or unlicensed source material to satisfy this check.'
        )
    return {'pinned_legal_skills': len(skills), 'modules': len(modules), 'acts': len(acts), 'research_cards': len(cards),
            'statutory_extracts': len(received), 'workflows': len(ids),
            'draft_synthetic_cases': len(case_ids), 'legal_acceptance': False}


if __name__ == '__main__':
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
