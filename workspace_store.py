"""Opt-in local matters, per-user access and append-only audit. No provider calls."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import uuid
from urllib.parse import urlsplit

from doctext import document_fingerprint, resolve_document


class WorkspaceError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code = status, code
        super().__init__(message)


def text(value, name, maximum=200, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f'{name}: требуется строка длиной {minimum}–{maximum}.')
    return value.strip()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def now():
    return datetime.now(timezone.utc).isoformat()


class WorkspaceStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, mode=0o700)
        # Restrict the DB before SQLite opens it, including the creation race.
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self.connect() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
                raise ValueError('Версия базы рабочего пространства не поддерживается.')
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT NOT NULL,
                  role TEXT NOT NULL CHECK(role IN ('admin','reviewer','member')), active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS tokens(digest TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS matters(id TEXT PRIMARY KEY,title TEXT NOT NULL,owner_id TEXT REFERENCES users(id),created_on TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS members(matter_id TEXT REFERENCES matters(id),user_id TEXT REFERENCES users(id),
                  role TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')),PRIMARY KEY(matter_id,user_id));
                CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,matter_id TEXT REFERENCES matters(id),title TEXT NOT NULL,
                  text TEXT NOT NULL,sha256 TEXT NOT NULL,source_url TEXT NOT NULL,captured_on TEXT NOT NULL,
                  pages TEXT NOT NULL,created_by TEXT REFERENCES users(id),created_on TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(document_id UNINDEXED,matter_id UNINDEXED,
                  start UNINDEXED,text,tokenize='unicode61');
                CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,matter_id TEXT REFERENCES matters(id),kind TEXT NOT NULL,
                  payload TEXT NOT NULL,sha256 TEXT NOT NULL,created_by TEXT REFERENCES users(id),created_on TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS decision_revisions(artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id),
                  previous_artifact_id TEXT UNIQUE NOT NULL REFERENCES artifacts(id),root_artifact_id TEXT NOT NULL REFERENCES artifacts(id));
                CREATE TRIGGER IF NOT EXISTS decision_revisions_no_update BEFORE UPDATE ON decision_revisions BEGIN SELECT RAISE(ABORT,'immutable revision'); END;
                CREATE TRIGGER IF NOT EXISTS decision_revisions_no_delete BEFORE DELETE ON decision_revisions BEGIN SELECT RAISE(ABORT,'append only'); END;
                CREATE TABLE IF NOT EXISTS benchmark_tasks(id TEXT PRIMARY KEY,matter_id TEXT REFERENCES matters(id),
                  case_id TEXT NOT NULL,payload TEXT NOT NULL,sha256 TEXT NOT NULL,created_by TEXT REFERENCES users(id),
                  created_on TEXT NOT NULL,artifact_id TEXT REFERENCES artifacts(id),UNIQUE(matter_id,case_id));
                CREATE TRIGGER IF NOT EXISTS benchmark_task_immutable BEFORE UPDATE OF matter_id,case_id,payload,sha256,created_by,created_on ON benchmark_tasks BEGIN SELECT RAISE(ABORT,'immutable task'); END;
                CREATE TABLE IF NOT EXISTS originals(document_id TEXT PRIMARY KEY REFERENCES documents(id),filename TEXT NOT NULL,
                  sha256 TEXT NOT NULL,bytes BLOB NOT NULL);
                CREATE TRIGGER IF NOT EXISTS originals_no_update BEFORE UPDATE ON originals BEGIN SELECT RAISE(ABORT,'immutable original'); END;
                CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,artifact_id TEXT REFERENCES artifacts(id),
                  artifact_sha256 TEXT NOT NULL,decision TEXT NOT NULL,reviewer_id TEXT REFERENCES users(id),
                  note TEXT NOT NULL,attestations TEXT NOT NULL,created_on TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evaluations(id TEXT PRIMARY KEY,artifact_id TEXT REFERENCES artifacts(id),
                  response_sha256 TEXT NOT NULL,response_text TEXT NOT NULL,scores TEXT NOT NULL,reviewer_id TEXT REFERENCES users(id),created_on TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS evaluation_unique ON evaluations(artifact_id,response_sha256,reviewer_id);
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,
                  matter_id TEXT,action TEXT NOT NULL,resource_id TEXT NOT NULL,created_on TEXT NOT NULL,previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'append only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'append only'); END;
                CREATE TRIGGER IF NOT EXISTS reviews_no_update BEFORE UPDATE ON reviews BEGIN SELECT RAISE(ABORT,'append only'); END;
                CREATE TRIGGER IF NOT EXISTS reviews_no_delete BEFORE DELETE ON reviews BEGIN SELECT RAISE(ABORT,'append only'); END;
                CREATE TRIGGER IF NOT EXISTS artifacts_no_update BEFORE UPDATE ON artifacts BEGIN SELECT RAISE(ABORT,'immutable artifact'); END;
                CREATE TRIGGER IF NOT EXISTS documents_no_update BEFORE UPDATE ON documents BEGIN SELECT RAISE(ABORT,'immutable document'); END;
                PRAGMA user_version=1;
            ''')
            if 'provenance' not in {row['name'] for row in db.execute('PRAGMA table_info(documents)')}:
                db.execute("ALTER TABLE documents ADD COLUMN provenance TEXT NOT NULL DEFAULT '{}'")

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _audit(self, db, user, action, resource='', matter=None):
        previous = db.execute('SELECT event_hash FROM audit ORDER BY id DESC LIMIT 1').fetchone()
        fields = [user, matter, action, resource, now(), previous[0] if previous else '0' * 64]
        digest = hashlib.sha256(canonical(fields).encode()).hexdigest()
        db.execute('INSERT INTO audit(user_id,matter_id,action,resource_id,created_on,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?)', (*fields, digest))

    def _user(self, db, principal):
        row = db.execute('SELECT id,name,role FROM users WHERE id=? AND active=1', (principal,)).fetchone()
        if not row:
            raise WorkspaceError(401, 'invalid_user', 'Учётная запись недоступна.')
        return dict(row)

    def _access(self, db, principal, matter, roles=('owner', 'editor', 'viewer')):
        self._user(db, principal)
        row = db.execute('SELECT role FROM members WHERE user_id=? AND matter_id=?', (principal, matter)).fetchone()
        if not row or row['role'] not in roles:
            # Same error for missing and inaccessible resources.
            raise WorkspaceError(403, 'matter_access_denied', 'Нет доступа к делу или действию.')
        return row['role']

    def create_user(self, name, role='member', *, actor=None):
        name = text(name, 'name', 100)
        if not isinstance(role, str) or role not in {'admin', 'reviewer', 'member'}:
            raise ValueError('Недопустимая роль пользователя.')
        with self.connect(True) as db:
            if actor is None:
                if db.execute('SELECT count(*) FROM users').fetchone()[0] or role != 'admin':
                    raise WorkspaceError(403, 'bootstrap_disabled', 'Первоначальная настройка уже выполнена.')
            elif self._user(db, actor)['role'] != 'admin':
                raise WorkspaceError(403, 'admin_required', 'Нужны права администратора.')
            identity, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
            db.execute('INSERT INTO users(id,name,role) VALUES(?,?,?)', (identity, name, role))
            db.execute('INSERT INTO tokens(digest,user_id) VALUES(?,?)', (hashlib.sha256(token.encode()).hexdigest(), identity))
            self._audit(db, actor or identity, 'user_created', identity)
        return {'user_id': identity, 'name': name, 'role': role, 'token': token, 'token_shown_once': True}

    def authenticate(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 512:
            return None
        with self.connect() as db:
            row = db.execute('SELECT u.id,u.name,u.role FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.digest=? AND t.active=1 AND u.active=1',
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return dict(row) if row else None

    def revoke_user(self, actor, user_id):
        with self.connect(True) as db:
            if self._user(db, actor)['role'] != 'admin' or actor == user_id:
                raise WorkspaceError(403, 'admin_required', 'Нельзя отключить себя; нужны права администратора.')
            if db.execute('SELECT count(*) FROM matters WHERE owner_id=?', (user_id,)).fetchone()[0]:
                raise ValueError('Сначала передайте принадлежащие пользователю дела другому участнику.')
            if db.execute('UPDATE users SET active=0 WHERE id=?', (user_id,)).rowcount != 1:
                raise ValueError('Пользователь не найден.')
            db.execute('UPDATE tokens SET active=0 WHERE user_id=?', (user_id,))
            self._audit(db, actor, 'user_revoked', user_id)
        return {'revoked': True}

    def rotate_token(self, principal):
        token = secrets.token_urlsafe(32)
        with self.connect(True) as db:
            self._user(db, principal)
            db.execute('UPDATE tokens SET active=0 WHERE user_id=?', (principal,))
            db.execute('INSERT INTO tokens(digest,user_id) VALUES(?,?)', (hashlib.sha256(token.encode()).hexdigest(), principal))
            self._audit(db, principal, 'token_rotated', principal)
        return {'token': token, 'token_shown_once': True, 'old_tokens_revoked': True}

    def transfer_matter(self, principal, matter, new_owner):
        with self.connect(True) as db:
            self._access(db, principal, matter, ('owner',))
            self._access(db, new_owner, matter)
            if principal == new_owner:
                raise ValueError('Вы уже владелец дела.')
            db.execute('UPDATE matters SET owner_id=? WHERE id=?', (new_owner, matter))
            db.execute("UPDATE members SET role='editor' WHERE matter_id=? AND user_id=?", (matter, principal))
            db.execute("UPDATE members SET role='owner' WHERE matter_id=? AND user_id=?", (matter, new_owner))
            self._audit(db, principal, 'matter_transferred', new_owner, matter)
        return {'transferred': True, 'new_owner': new_owner}

    def create_matter(self, principal, title):
        title = text(title, 'title')
        identity = uuid.uuid4().hex
        with self.connect(True) as db:
            self._user(db, principal)
            if db.execute('SELECT count(*) FROM matters WHERE owner_id=?', (principal,)).fetchone()[0] >= 100:
                raise ValueError('Лимит: 100 дел на владельца.')
            db.execute('INSERT INTO matters VALUES(?,?,?,?)', (identity, title, principal, now()))
            db.execute('INSERT INTO members VALUES(?,?,?)', (identity, principal, 'owner'))
            self._audit(db, principal, 'matter_created', identity, identity)
        return {'matter_id': identity, 'title': title}

    def list_matters(self, principal):
        with self.connect() as db:
            self._user(db, principal)
            rows = db.execute('SELECT m.id,m.title,x.role FROM matters m JOIN members x ON x.matter_id=m.id WHERE x.user_id=? ORDER BY m.created_on LIMIT 1000', (principal,))
            return {'matters': [dict(row) for row in rows]}

    def grant(self, principal, matter, user_id, role):
        if not isinstance(role, str) or role not in {'editor', 'viewer', 'remove'}:
            raise ValueError('Допустимы editor, viewer или remove.')
        with self.connect(True) as db:
            self._access(db, principal, matter, ('owner',))
            self._user(db, user_id)
            owner = db.execute('SELECT owner_id FROM matters WHERE id=?', (matter,)).fetchone()[0]
            if user_id == owner:
                raise ValueError('Права владельца нельзя менять этим действием.')
            if role == 'remove':
                db.execute('DELETE FROM members WHERE matter_id=? AND user_id=?', (matter, user_id))
            else:
                db.execute('INSERT INTO members VALUES(?,?,?) ON CONFLICT(matter_id,user_id) DO UPDATE SET role=excluded.role', (matter, user_id, role))
            self._audit(db, principal, 'membership_' + role, user_id, matter)
        return {'updated': True}

    def add_document(self, principal, matter, payload, *, provenance=None, original=None, deduplicate_source=False):
        if set(payload) - {'matter_id', 'title', 'document', 'source_url', 'captured_on', 'pages'}:
            raise ValueError('Неизвестное поле документа.')
        value = resolve_document({'document': payload.get('document')})
        if len(value) > 200000:
            raise ValueError('Документ превышает 200000 символов.')
        title = text(payload.get('title'), 'title')
        url = payload.get('source_url', '')
        if not isinstance(url, str) or len(url) > 2000 or (url and not url.startswith('https://')):
            raise ValueError('source_url должен быть HTTPS URL или пустой строкой.')
        if url:
            parsed = urlsplit(url)
            if not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.port not in (None, 443):
                raise ValueError('source_url требует HTTPS без логина, пароля, фрагмента и нестандартного порта.')
        captured = payload.get('captured_on', datetime.now(timezone.utc).date().isoformat())
        from datetime import date
        if not isinstance(captured, str) or date.fromisoformat(captured).isoformat() != captured or captured > date.today().isoformat():
            raise ValueError('Некорректная дата получения документа.')
        pages = payload.get('pages', [])
        if not isinstance(pages, list) or len(pages) > 30:
            raise ValueError('Некорректная карта страниц.')
        previous = 0
        for page_number, page in enumerate(pages, 1):
            if (not isinstance(page, dict) or set(page) != {'page', 'start', 'end'}
                    or any(type(page.get(key)) is not int for key in ('page', 'start', 'end'))
                    or page['page'] != page_number or not previous <= page['start'] < page['end'] <= len(value)
                    or value[previous:page['start']].strip()):
                raise ValueError('Некорректная карта страниц.')
            previous = page['end']
        if pages and (value != payload['document'] or value[previous:].strip()):
            raise ValueError('Карта страниц должна соответствовать всему тексту без изменения внешних пробелов.')
        identity, digest = uuid.uuid4().hex, document_fingerprint(value)
        with self.connect(True) as db:
            self._access(db, principal, matter, ('owner', 'editor'))
            if deduplicate_source:
                if (not provenance or provenance.get('origin') != 'pinned_primary_archive'
                        or original is None or hashlib.sha256(original[1]).hexdigest() != provenance.get('original_sha256')):
                    raise ValueError('Повторный импорт требует проверенные байты первоисточника.')
                for candidate in db.execute('SELECT id FROM documents WHERE matter_id=? AND sha256=?', (matter, digest)):
                    existing = self._document(db, principal, candidate['id'])
                    if (existing['provenance'].get('origin') == 'pinned_primary_archive'
                            and existing['provenance'].get('source_id') == provenance.get('source_id')
                            and existing['provenance'].get('original_sha256') == provenance.get('original_sha256')):
                        archived = db.execute('SELECT bytes FROM originals WHERE document_id=?', (existing['id'],)).fetchone()
                        if archived is None or hashlib.sha256(archived['bytes']).hexdigest() != provenance['original_sha256']:
                            raise WorkspaceError(503, 'original_integrity', 'Нарушена целостность оригинала.')
                        self._audit(db, principal, 'primary_source_reused', existing['id'], matter)
                        return {'document_id': existing['id'], 'sha256': digest, 'text_char_count': len(value),
                                'provenance_verified': False, 'reused': True}
            if db.execute('SELECT count(*) FROM documents WHERE matter_id=?', (matter,)).fetchone()[0] >= 1000:
                raise ValueError('Лимит: 1000 документов в деле.')
            db.execute('INSERT INTO documents(id,matter_id,title,text,sha256,source_url,captured_on,pages,created_by,created_on,provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (identity, matter, title, value, digest, url, captured, canonical(pages), principal, now(),
                        canonical(provenance or {'origin': 'user_supplied_text', 'source_authenticity_automatically_verified': False})))
            if original is not None:
                filename, raw = original
                db.execute('INSERT INTO originals VALUES(?,?,?,?)', (identity, filename, hashlib.sha256(raw).hexdigest(), raw))
            for start in range(0, len(value), 1000):
                db.execute('INSERT INTO chunks(document_id,matter_id,start,text) VALUES(?,?,?,?)', (identity, matter, start, value[start:start + 1200]))
            self._audit(db, principal, 'document_added', identity, matter)
        return {'document_id': identity, 'sha256': digest, 'text_char_count': len(value), 'provenance_verified': False, 'reused': False}

    def original(self, principal, document_id):
        import base64
        with self.connect(True) as db:
            document = self._document(db, principal, document_id)
            row = db.execute('SELECT * FROM originals WHERE document_id=?', (document_id,)).fetchone()
            if row is None:
                raise ValueError('Оригинальный файл не сохранялся для этого документа.')
            if hashlib.sha256(row['bytes']).hexdigest() != row['sha256']:
                raise WorkspaceError(503, 'original_integrity', 'Нарушена целостность оригинала.')
            self._audit(db, principal, 'original_downloaded', document_id, document['matter_id'])
            return {'filename': row['filename'], 'file_base64': base64.b64encode(row['bytes']).decode(), 'sha256': row['sha256']}

    def _document(self, db, principal, document_id):
        row = db.execute('SELECT * FROM documents WHERE id=?', (document_id,)).fetchone()
        if not row:
            raise WorkspaceError(403, 'matter_access_denied', 'Нет доступа к документу.')
        self._access(db, principal, row['matter_id'])
        value = dict(row)
        if document_fingerprint(value['text']) != value['sha256']:
            raise WorkspaceError(503, 'document_integrity', 'Нарушена целостность документа.')
        value['pages'] = json.loads(value['pages'])
        value['provenance'] = json.loads(value['provenance'])
        return value

    def document(self, principal, document_id):
        with self.connect(True) as db:
            row = self._document(db, principal, document_id)
            self._audit(db, principal, 'document_read', document_id, row['matter_id'])
        return row

    def documents(self, principal, matter):
        with self.connect() as db:
            self._access(db, principal, matter)
            rows = db.execute('SELECT d.id,d.title,d.sha256,d.captured_on,d.created_on,EXISTS(SELECT 1 FROM originals o WHERE o.document_id=d.id) AS has_original FROM documents d WHERE d.matter_id=? ORDER BY d.created_on LIMIT 1000', (matter,))
            return {'documents': [{**dict(row), 'has_original': bool(row['has_original'])} for row in rows]}

    def search(self, principal, matter, query, limit=8, *, document_ids=None):
        query = text(query, 'query', 2000)
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError('limit должен быть от 1 до 12.')
        if document_ids is not None and (not isinstance(document_ids, list) or not 1 <= len(document_ids) <= 32
                or any(not isinstance(identity, str) or len(identity) > 64 for identity in document_ids)):
            raise ValueError('Некорректное ограничение списка документов.')
        words = re.findall(r'\w{2,80}', query.casefold())[:16]
        if not words:
            raise ValueError('Запрос должен содержать слова длиной от двух символов.')
        expression = ' OR '.join('"' + word + '"*' for word in words)
        with self.connect(True) as db:
            self._access(db, principal, matter)
            restriction = ' AND document_id IN (' + ','.join('?' for _ in document_ids) + ')' if document_ids else ''
            rows = db.execute('SELECT document_id,start,text,bm25(chunks) AS score FROM chunks WHERE chunks MATCH ? AND matter_id=?' + restriction + ' ORDER BY score LIMIT ?',
                              (expression, matter, *(document_ids or []), limit))
            results = []
            for row in rows:
                if len(row['text'].strip()) < 10:
                    continue
                document = self._document(db, principal, row['document_id'])
                start = int(row['start'])
                if document['text'][start:start + len(row['text'])] != row['text']:
                    raise WorkspaceError(503, 'index_integrity', 'Индекс не соответствует документу.')
                pages = [page['page'] for page in document['pages'] if page['start'] < start + len(row['text']) and page['end'] > start]
                results.append({'source_id': len(results) + 1, 'document_id': document['id'],
                                'title': document['title'], 'text': row['text'], 'start': start,
                                'end': start + len(row['text']), 'pages': pages,
                                'document_sha256': document['sha256'], 'verified': False,
                                'source_context': {'origin': document['provenance'].get('origin', 'unspecified'),
                                    'captured_on': document['captured_on'],
                                    'edition_label': document['provenance'].get('edition_label'),
                                    'signed_on': document['provenance'].get('signed_on'),
                                    'material_type': document['provenance'].get('material_type'),
                                    'case_number': document['provenance'].get('case_number'),
                                    'decision_date': document['provenance'].get('decision_date'),
                                    'current_validity_verified': False,
                                    'history_complete': False}})
            self._audit(db, principal, 'matter_search', matter, matter)
        return {'matter_id': matter, 'matches': results, 'search_mode': 'local_fts5_prefix',
                'semantic_search': False, 'external_requests_attempted': False, 'verified': False}

    def audit(self, principal, matter=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('limit должен быть от 1 до 1000.')
        with self.connect() as db:
            if matter:
                self._access(db, principal, matter, ('owner',))
                rows = db.execute('SELECT * FROM audit WHERE matter_id=? ORDER BY id DESC LIMIT ?', (matter, limit))
            else:
                if self._user(db, principal)['role'] != 'admin':
                    raise WorkspaceError(403, 'admin_required', 'Нужны права администратора.')
                rows = db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT ?', (limit,))
            result = [dict(row) for row in rows]
        return {'events': result, 'append_only': True, 'contains_document_text': False}

    def verify_audit(self):
        previous = '0' * 64
        with self.connect() as db:
            for row in db.execute('SELECT * FROM audit ORDER BY id'):
                fields = [row[key] for key in ('user_id', 'matter_id', 'action', 'resource_id', 'created_on', 'previous_hash')]
                if row['previous_hash'] != previous or hashlib.sha256(canonical(fields).encode()).hexdigest() != row['event_hash']:
                    return False
                previous = row['event_hash']
        return True
