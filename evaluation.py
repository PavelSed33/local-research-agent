"""Run synthetic acceptance scenarios; this is not a legal quality score."""
import argparse
import json
from pathlib import Path

from agent import Source
from case_law import validate_decisions
from legal_history import select_provision_period
from legal_review import applicability, validate_legal_bases


def evaluate(path: Path) -> dict:
    dataset = json.loads(path.read_text(encoding='utf-8'))
    results = []
    seen = set()
    for case in dataset['cases']:
        identity = case['id']
        if identity in seen:
            raise ValueError('Duplicate scenario ID')
        seen.add(identity)
        data = case['input']
        try:
            if case['kind'] == 'period':
                value = select_provision_period(data['timeline'], data['as_of'])
                actual = {'status': value['status'], 'valid_from': (value['period'] or {}).get('valid_from')}
            elif case['kind'] == 'scope':
                actual = {'status': applicability(data['profile'], data['facts'])['status']}
            elif case['kind'] == 'court':
                accepted, _ = validate_decisions(data['records'], [Source(**s) for s in data['sources']])
                actual = {'accepted': len(accepted)}
            elif case['kind'] == 'legal_quote':
                issues = validate_legal_bases(data['result'], data['document'], [Source(**s) for s in data['sources']])
                actual = {'valid': not issues}
            else:
                raise ValueError('Unknown scenario type')
        except (ValueError, TypeError, KeyError) as exc:
            actual = {'error': type(exc).__name__}
        results.append({'id': identity, 'passed': actual == case['expected'],
                        'expected': case['expected'], 'actual': actual})
    if not results:
        raise ValueError('Empty evaluation dataset')
    return {'scope': 'synthetic_structural_acceptance', 'legal_acceptance': False,
            'lawyer_reviewed': dataset.get('lawyer_reviewed') is True,
            'passed': sum(item['passed'] for item in results), 'total': len(results),
            'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=Path(__file__).parent / 'fixtures' / 'evaluation_cases.json')
    report = evaluate(parser.parse_args().dataset)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['passed'] == report['total'] else 1)
