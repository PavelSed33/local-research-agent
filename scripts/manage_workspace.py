"""Bootstrap a local workspace administrator; no default or shared passwords."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workspace_store import WorkspaceStore, WorkspaceError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'verify-audit'])
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--name', default='Администратор')
    args = parser.parse_args()
    try:
        store = WorkspaceStore(args.db)
        if args.command == 'init':
            print(json.dumps(store.create_user(args.name, 'admin'), ensure_ascii=False))
        else:
            valid = store.verify_audit()
            print(json.dumps({'audit_chain_valid': valid, 'root_operator_can_modify_database': True}))
            return 0 if valid else 1
    except (WorkspaceError, ValueError, OSError):
        print(json.dumps({'status': 'failed', 'error': 'Настройка уже выполнена либо файл недоступен.'}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
