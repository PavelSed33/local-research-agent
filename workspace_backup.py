"""Local operator backups and restore-to-new-file only; never an HTTP download route."""
from contextlib import closing
from datetime import datetime
from functools import lru_cache, wraps
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time
import zipfile
import zlib

from doctext import document_fingerprint
from workspace_store import WorkspaceStore, canonical, now

MAX_DB_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024
TIMEOUT_SECONDS = 30
TABLES = ('users','tokens','matters','members','documents','artifacts','decision_revisions',
          'benchmark_tasks','originals','reviews','evaluations','audit','chunks')


class BackupError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('Операция резервного копирования не завершена: ' + code)


def _operation(function):
    @wraps(function)
    def wrapped(*args,**kwargs):
        try:
            return function(*args,**kwargs)
        except BackupError:
            raise
        except (sqlite3.Error,OSError,ValueError,TypeError,RecursionError,zipfile.BadZipFile,zlib.error,EOFError,RuntimeError,NotImplementedError):
            raise BackupError('operation_failed') from None
    return wrapped


def _digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _source(path):
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BackupError('invalid_source_file')
    return path.resolve()


def _private_parent(path):
    path = Path(path).absolute()
    missing, parent = [], path.parent
    while not parent.exists():
        missing.append(parent); parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise BackupError('private_directory_required')
    if os.name == 'posix' and (stat.S_IMODE(parent.stat().st_mode) & 0o077 or parent.stat().st_uid != os.geteuid()):
        raise BackupError('private_directory_required')
    if path.name.endswith(('-wal','-shm','-journal')) or path.exists() or path.is_symlink() or any(Path(str(path) + suffix).exists() or Path(str(path) + suffix).is_symlink() for suffix in ('-wal','-shm','-journal')):
        raise BackupError('destination_exists')
    return parent.resolve() / path.name


def _connect(path, *, writable=False):
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if writable else '?mode=ro'), uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA trusted_schema=OFF')
    db.execute('PRAGMA foreign_keys=ON')
    if not writable:
        db.execute('PRAGMA query_only=ON')
    return db


def _schema(db):
    return {(row['type'],row['name']): re.sub(r'\s+', ' ', row['sql']).strip()
            for row in db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' AND sql IS NOT NULL")}


@lru_cache(maxsize=1)
def _expected_schema():
    with tempfile.TemporaryDirectory(prefix='workspace-backup-schema-') as directory:
        store = WorkspaceStore(Path(directory) / 'template.db')
        with store.connect() as db:
            return _schema(db)


@_operation
def inspect_database(path):
    """Read-only content/schema verification. Does not initialize or repair the source."""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    db = _connect(_source(path))
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        if db.execute('PRAGMA user_version').fetchone()[0] != 1 or _schema(db) != _expected_schema():
            raise BackupError('schema_mismatch')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise BackupError('sqlite_integrity')
        counts = {name: db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in TABLES}
        if db.execute("SELECT 1 FROM matters m LEFT JOIN members x ON x.matter_id=m.id AND x.user_id=m.owner_id AND x.role='owner' WHERE x.user_id IS NULL LIMIT 1").fetchone():
            raise BackupError('matter_owner_membership')
        for document in db.execute('SELECT * FROM documents'):
            if document_fingerprint(document['text']) != document['sha256']:
                raise BackupError('document_hash')
            chunks = list(db.execute('SELECT start,text,matter_id FROM chunks WHERE document_id=? ORDER BY start', (document['id'],)))
            expected = list(range(0,len(document['text']),1000))
            if (len(chunks) != len(expected) or any(int(row['start']) != offset or row['matter_id'] != document['matter_id']
                    or row['text'] != document['text'][offset:offset+1200] for offset,row in zip(expected,chunks))):
                raise BackupError('search_index')
            pages = json.loads(document['pages']); previous = 0
            for index,page in enumerate(pages,1):
                if (set(page) != {'page','start','end'} or any(type(page[key]) is not int for key in page)
                        or page['page'] != index or not previous <= page['start'] < page['end'] <= len(document['text'])
                        or document['text'][previous:page['start']].strip()):
                    raise BackupError('document_page_map')
                previous = page['end']
            if pages and previous != len(document['text']):
                raise BackupError('document_page_map')
        if db.execute('SELECT 1 FROM chunks c LEFT JOIN documents d ON d.id=c.document_id WHERE d.id IS NULL LIMIT 1').fetchone():
            raise BackupError('orphan_search_chunk')
        for original in db.execute('SELECT sha256,bytes FROM originals'):
            if hashlib.sha256(original['bytes']).hexdigest() != original['sha256']:
                raise BackupError('original_hash')

        def verify_quotes(value, matter):
            if isinstance(value, dict):
                if 'document_id' in value and 'quote' in value:
                    document = db.execute('SELECT text,sha256,matter_id FROM documents WHERE id=?', (value['document_id'],)).fetchone()
                    start,end = value.get('start'),value.get('end')
                    if (document is None or document['matter_id'] != matter or value.get('document_sha256') != document['sha256']
                            or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(document['text'])
                            or document['text'][start:end] != value['quote']):
                        raise BackupError('evidence_quote')
                for child in value.values():
                    verify_quotes(child,matter)
            elif isinstance(value,list):
                for child in value:
                    verify_quotes(child,matter)
        for artifact in db.execute('SELECT * FROM artifacts'):
            if hashlib.sha256(artifact['payload'].encode()).hexdigest() != artifact['sha256']:
                raise BackupError('artifact_hash')
            verify_quotes(json.loads(artifact['payload']),artifact['matter_id'])
        for task in db.execute('SELECT * FROM benchmark_tasks'):
            if hashlib.sha256(task['payload'].encode()).hexdigest() != task['sha256']:
                raise BackupError('task_hash')
            if task['artifact_id']:
                artifact = db.execute('SELECT matter_id,kind FROM artifacts WHERE id=?',(task['artifact_id'],)).fetchone()
                if artifact is None or artifact['matter_id'] != task['matter_id'] or artifact['kind'] != 'benchmark':
                    raise BackupError('task_artifact')
        for review in db.execute('SELECT r.artifact_sha256,a.sha256 FROM reviews r JOIN artifacts a ON a.id=r.artifact_id'):
            if review['artifact_sha256'] != review['sha256']:
                raise BackupError('review_binding')
        for score in db.execute('SELECT response_text,response_sha256 FROM evaluations'):
            if hashlib.sha256(score['response_text'].encode()).hexdigest() != score['response_sha256']:
                raise BackupError('evaluation_hash')
        for revision in db.execute('SELECT * FROM decision_revisions'):
            chain_artifacts = [db.execute('SELECT matter_id,kind,payload FROM artifacts WHERE id=?',(identity,)).fetchone()
                               for identity in (revision['artifact_id'],revision['previous_artifact_id'],revision['root_artifact_id'])]
            if any(row is None or row['kind'] != 'decision' for row in chain_artifacts):
                raise BackupError('revision_artifact')
            signatures = {(row['matter_id'],json.loads(row['payload'])['case_number'],json.loads(row['payload'])['identity']['document_id']) for row in chain_artifacts}
            if len(signatures) != 1:
                raise BackupError('revision_identity')
            seen, current = set(), revision['artifact_id']
            while True:
                if current in seen:
                    raise BackupError('revision_cycle')
                seen.add(current)
                if time.monotonic() > deadline:
                    raise BackupError('verification_timeout')
                row = db.execute('SELECT previous_artifact_id,root_artifact_id FROM decision_revisions WHERE artifact_id=?',(current,)).fetchone()
                if row is None:
                    if current != revision['root_artifact_id']:
                        raise BackupError('revision_root')
                    break
                current = row['previous_artifact_id']
        previous = '0' * 64
        for event in db.execute('SELECT * FROM audit ORDER BY id'):
            fields = [event[key] for key in ('user_id','matter_id','action','resource_id','created_on','previous_hash')]
            if event['previous_hash'] != previous or hashlib.sha256(canonical(fields).encode()).hexdigest() != event['event_hash']:
                raise BackupError('audit_chain')
            previous = event['event_hash']
        if time.monotonic() > deadline:
            raise BackupError('verification_timeout')
        return {'schema_version':1,'counts':counts,'audit_head':previous,'integrity_checked':True}
    except BackupError:
        raise
    except (sqlite3.Error,ValueError,TypeError,KeyError,AttributeError,RecursionError):
        raise BackupError('database_verification_failed') from None
    finally:
        db.close()


def _copy_database(source, destination):
    deadline = time.monotonic() + TIMEOUT_SECONDS
    def progress(status,remaining,total):
        if time.monotonic() > deadline or total * page_size > MAX_DB_BYTES:
            raise BackupError('backup_limit_or_timeout')
    with closing(_connect(source)) as left, closing(sqlite3.connect(destination)) as right:
        page_size = left.execute('PRAGMA page_size').fetchone()[0]
        left.backup(right,pages=128,progress=progress,sleep=0.05)
        right.execute('PRAGMA journal_mode=DELETE')


def _publish(staged, target):
    with staged.open('rb') as stream:
        os.fsync(stream.fileno())
    try:
        os.link(staged,target)
    except FileExistsError:
        raise BackupError('destination_exists') from None
    try:
        if os.name == 'posix':
            descriptor = os.open(target.parent,os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except Exception:
        # Remove only the link this operation just created, never a replacement.
        if target.exists() and os.path.samefile(staged,target):
            target.unlink()
        raise


@_operation
def create_backup(source, output):
    source, output = _source(source), _private_parent(output)
    with tempfile.TemporaryDirectory(prefix='.backup-',dir=output.parent) as directory:
        snapshot = Path(directory)/'workspace.sqlite3'; snapshot.touch(mode=0o600)
        _copy_database(source,snapshot)
        if snapshot.stat().st_size > MAX_DB_BYTES:
            raise BackupError('backup_size')
        verification = inspect_database(snapshot)
        manifest = {'format_version':1,'created_on':now(),'db_sha256':_digest(snapshot),
            'db_bytes':snapshot.stat().st_size,**verification,'encrypted':False}
        packed = Path(directory)/'snapshot.zip'; packed.touch(mode=0o600)
        with zipfile.ZipFile(packed,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot,'workspace.sqlite3')
            archive.writestr('manifest.json',canonical(manifest))
        if packed.stat().st_size > MAX_ARCHIVE_BYTES:
            raise BackupError('backup_size')
        _publish(packed,output)
        return {'status':'created','snapshot_sha256':_digest(output),'db_sha256':manifest['db_sha256'],
                'counts':verification['counts'],'audit_head':verification['audit_head'],
                'encrypted':False,'contains_confidential_data':True,'legal_acceptance':False}


def _unpack(archive_path, destination, expected_sha256=None):
    archive_path = _source(archive_path)
    # Pin the input bytes in the private staging directory before trusting its digest.
    private_archive = destination.parent / 'source.zip'
    private_archive.touch(mode=0o600,exist_ok=False)
    with archive_path.open('rb') as left, private_archive.open('wb') as right:
        total = 0
        for block in iter(lambda:left.read(1024*1024),b''):
            total += len(block)
            if total > MAX_ARCHIVE_BYTES:
                raise BackupError('backup_size')
            right.write(block)
    actual = _digest(private_archive)
    if expected_sha256 is not None and (not isinstance(expected_sha256,str) or not re.fullmatch('[0-9a-f]{64}',expected_sha256) or actual != expected_sha256):
        raise BackupError('snapshot_hash')
    try:
        with zipfile.ZipFile(private_archive) as archive:
            entries = archive.infolist()
            if (len(entries) != 2 or {row.filename for row in entries} != {'workspace.sqlite3','manifest.json'}
                    or any(row.flag_bits & 1 or row.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED) for row in entries)
                    or archive.getinfo('workspace.sqlite3').file_size > MAX_DB_BYTES
                    or archive.getinfo('manifest.json').file_size > 65536):
                raise BackupError('backup_archive')
            manifest = json.loads(archive.read('manifest.json'))
            if (not isinstance(manifest,dict) or set(manifest) != {'format_version','created_on','db_sha256','db_bytes','schema_version','counts','audit_head','integrity_checked','encrypted'}
                    or type(manifest.get('format_version')) is not int or manifest['format_version'] != 1
                    or manifest.get('integrity_checked') is not True or manifest.get('encrypted') is not False
                    or type(manifest.get('schema_version')) is not int or type(manifest.get('db_bytes')) is not int
                    or not isinstance(manifest.get('created_on'),str) or len(manifest['created_on']) > 100):
                raise BackupError('backup_manifest')
            if datetime.fromisoformat(manifest['created_on']).tzinfo is None:
                raise BackupError('backup_manifest')
            with archive.open('workspace.sqlite3') as left,destination.open('wb') as right:
                total = 0
                for block in iter(lambda:left.read(1024*1024),b''):
                    total += len(block)
                    if total > MAX_DB_BYTES:
                        raise BackupError('backup_size')
                    right.write(block)
            if destination.stat().st_size != manifest.get('db_bytes') or _digest(destination) != manifest.get('db_sha256'):
                raise BackupError('database_hash')
            verified = inspect_database(destination)
            if any(manifest.get(key) != value for key,value in verified.items()):
                raise BackupError('backup_manifest_binding')
            return manifest,actual
    except BackupError:
        raise
    except (zipfile.BadZipFile,sqlite3.Error,ValueError,KeyError,RuntimeError,OSError,EOFError):
        raise BackupError('backup_verification_failed') from None


@_operation
def verify_backup(source, expected_sha256=None):
    with tempfile.TemporaryDirectory(prefix='workspace-backup-check-') as directory:
        target = Path(directory)/'workspace.sqlite3'; target.touch(mode=0o600)
        manifest,digest = _unpack(source,target,expected_sha256)
        return {'status':'verified','snapshot_sha256':digest,'counts':manifest['counts'],
                'audit_head':manifest['audit_head'],'encrypted':False,'legal_acceptance':False}


@_operation
def restore_backup(source, destination, expected_sha256):
    if expected_sha256 is None:
        raise BackupError('expected_snapshot_hash_required')
    destination = _private_parent(destination)
    with tempfile.TemporaryDirectory(prefix='.restore-',dir=destination.parent) as directory:
        staged = Path(directory)/'workspace.sqlite3'; staged.touch(mode=0o600)
        manifest,digest = _unpack(source,staged,expected_sha256)
        with closing(_connect(staged,writable=True)) as db, db:
            active_tokens = db.execute('SELECT COUNT(*) FROM tokens WHERE active=1').fetchone()[0]
            db.execute('UPDATE tokens SET active=0')
            fields = ['local-restore',None,'workspace_restored_tokens_revoked',manifest['db_sha256'],now(),manifest['audit_head']]
            event_hash = hashlib.sha256(canonical(fields).encode()).hexdigest()
            db.execute('INSERT INTO audit(user_id,matter_id,action,resource_id,created_on,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?)',(*fields,event_hash))
        verified = inspect_database(staged)
        _publish(staged,destination)
        return {'status':'restored_to_new_file','snapshot_sha256':digest,'restored_db_sha256':_digest(destination),
                'tokens_disabled':active_tokens,'requires_new_admin_token':True,'accounts_and_acl_require_review':True,
                'counts':verified['counts'],'audit_head':verified['audit_head'],'encrypted':False,'legal_acceptance':False}
