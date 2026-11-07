"""Reconcile a candidate amendment inventory with local unverified archives."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from agent import validate_as_of
from verify_archive import verify_archive


def build_inventory_report(inventory: dict, archive_root: Path) -> dict:
    entries = inventory.get('consolidated_citation_inventory')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Inventory must contain amendment citations')
    keyed = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Invalid inventory record')
        day, number = entry.get('signed_date'), entry.get('act_number')
        if validate_as_of(day) is None or not isinstance(number, str) or not number:
            raise ValueError('Missing act identity')
        key = (day, number)
        if key in keyed:
            raise ValueError('Duplicate dated act in inventory')
        keyed[key] = entry
    archives = {}
    errors = []
    unmatched = []
    if not archive_root.is_dir():
        raise ValueError('Archive directory does not exist')
    for manifest in sorted(archive_root.rglob('manifest.json')):
        try:
            data = json.loads(manifest.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Manifest must be an object')
            key = (data.get('signed_date'), data.get('act_number'))
            if not all(isinstance(value, str) for value in key):
                raise ValueError('Manifest has no act identity')
            result = verify_archive(manifest)
            record = {'manifest': str(manifest), 'integrity_ok': result['integrity_ok'],
                      'errors': result['errors']}
            if key in keyed:
                archives.setdefault(key, []).append(record)
            else:
                unmatched.append(record)
        except (OSError, ValueError, TypeError) as exc:
            errors.append({'manifest': str(manifest), 'error': str(exc)})
    rows = []
    for key, entry in keyed.items():
        found = archives.get(key, [])
        valid = any(item['integrity_ok'] for item in found)
        rows.append({
            'signed_date': key[0], 'act_number': key[1],
            'scope_mapping_status': entry.get('scope_mapping_status', 'unknown'),
            'archive_status': 'candidate_text_unverified' if valid else 'invalid_or_failed_archive' if found else 'missing_archive',
            'archives': found, 'verified': False,
            'required_checks': ['original source authenticity', 'amendment scope',
                                'effective and transitional provisions', 'historical completeness'],
        })
    return {
        'target_law': inventory.get('target_law'), 'acts': rows,
        'summary': {'total_acts': len(rows), **dict(Counter(row['archive_status'] for row in rows))},
        'archive_errors': errors, 'unmatched_archives': unmatched,
        'verified': False, 'coverage_verified': False,
        'limitation': 'Local archive matching only; the inventory and primary evidence remain incomplete. Fixture flags are not changed.',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory', type=Path)
    parser.add_argument('archive_root', type=Path)
    args = parser.parse_args()
    try:
        report = build_inventory_report(json.loads(args.inventory.read_text(encoding='utf-8')), args.archive_root)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # A generated gap report is successful even when evidence is missing.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
