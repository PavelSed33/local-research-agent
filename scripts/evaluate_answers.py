"""Evaluate saved answer bundles offline; exit 0 means technical checks only."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from response_quality import evaluate_responses, evaluate_control_suite, read_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path)
    parser.add_argument('--control-suite', action='store_true', help='Проверить ожидаемые вердикты синтетических контрольных примеров.')
    args = parser.parse_args()
    try:
        evaluate = evaluate_control_suite if args.control_suite else evaluate_responses
        report = evaluate(read_dataset(args.dataset))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print(json.dumps({'technical_status': 'invalid_input', 'legal_acceptance': False,
                          'reason': 'dataset_unreadable_or_invalid'}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['technical_status'] == 'passed' else 1 if report['technical_status'] == 'failed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
