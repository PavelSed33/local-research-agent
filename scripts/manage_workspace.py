"""Local workspace administration, verified backups and restore to new files."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workspace_store import WorkspaceStore, WorkspaceError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'verify-audit', 'backup', 'verify-backup', 'restore', 'recover-admin-token', 'recover-user-token'])
    parser.add_argument('--db', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--user-id')
    parser.add_argument('--name', default='Администратор')
    args = parser.parse_args()
    try:
        from workspace_backup import create_backup, verify_backup, restore_backup, inspect_database, BackupError
        if args.command in ('backup','verify-backup','restore'):
            if args.command == 'backup':
                if args.db is None or args.output is None:
                    parser.error('backup требует --db и --output')
                result = create_backup(args.db,args.output)
            elif args.command == 'verify-backup':
                if args.input is None:
                    parser.error('verify-backup требует --input')
                result = verify_backup(args.input,args.expected_sha256)
            else:
                if args.input is None or args.db is None or args.expected_sha256 is None:
                    parser.error('restore требует --input, новый --db и --expected-sha256')
                result = restore_backup(args.input,args.db,args.expected_sha256)
            print(json.dumps(result,ensure_ascii=False))
            return 0
        if args.db is None:
            parser.error('требуется --db')
        if args.command == 'verify-audit':
            inspect_database(args.db)
            print(json.dumps({'audit_chain_valid':True,'database_integrity_checked':True,'root_operator_can_modify_database':True}))
            return 0
        if args.command in ('recover-admin-token','recover-user-token'):
            inspect_database(args.db)
        store = WorkspaceStore(args.db)
        if args.command == 'init':
            print(json.dumps(store.create_user(args.name, 'admin'), ensure_ascii=False))
        elif args.command in ('recover-admin-token','recover-user-token'):
            with store.connect() as db:
                user = store._user(db,args.user_id)
                if args.command == 'recover-admin-token' and user['role'] != 'admin':
                    raise WorkspaceError(403,'admin_required','Нужна существующая активная учётная запись администратора.')
            print(json.dumps(store.rotate_token(args.user_id,local_recovery=True),ensure_ascii=False))
    except BackupError as exc:
        print(json.dumps({'status':'failed','code':exc.code}),file=sys.stderr)
        return 1
    except (WorkspaceError, ValueError, OSError):
        print(json.dumps({'status': 'failed', 'error': 'Настройка уже выполнена либо файл недоступен.'}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
