"""List local data errors and unconfirmed legal evidence without network access."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data_audit import audit_repository

if __name__ == '__main__':
    try:
        report = audit_repository()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print(json.dumps({'status': 'failed', 'code': 'local_data_unavailable', 'legal_acceptance': False}))
        raise SystemExit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if report['counts']['error'] else 0)
