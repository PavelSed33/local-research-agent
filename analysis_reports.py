"""Server-owned pilot report snapshots; byte integrity is not legal approval."""
import hashlib
import json
import re
import uuid

from reviewed_evidence import ReviewedEvidence, day
from workspace_store import WorkspaceError, canonical, now, text

KIND = 'analysis_report'
MAX_MARKDOWN_BYTES = 512 * 1024
HASH = re.compile(r'[a-f0-9]{64}')


def integrity_error():
    return WorkspaceError(503, 'analysis_report_integrity', 'Сохранённый отчёт не прошёл проверку целостности.')


class AnalysisReports:
    def __init__(self, store):
        self.store = store

    def _check_context(self, db, principal, snapshot):
        matter = snapshot['matter_id']
        self.store._access(db, principal, matter)
        for ref in snapshot['documents']:
            document = self.store._document(db, principal, ref['document_id'])
            if document['matter_id'] != matter or document['sha256'] != ref['sha256']:
                raise WorkspaceError(409, 'analysis_source_changed', 'Источник отчёта изменился. Сформируйте анализ заново.')
        ReviewedEvidence(self.store).assert_selection(db, principal, matter, snapshot['as_of'], snapshot['editions'])

    def save(self, db, principal, prepared, result):
        """Called only by the server's completed worker transaction, never by a client route."""
        markdown = result.get('markdown')
        if not isinstance(markdown, str) or not 1 <= len(markdown.encode('utf-8')) <= MAX_MARKDOWN_BYTES:
            raise integrity_error()
        digest = hashlib.sha256(markdown.encode('utf-8')).hexdigest()
        if result.get('markdown_sha256') != digest:
            raise integrity_error()
        documents = {}
        for source in prepared['sources']:
            identity = source['document_id']
            if identity in documents and documents[identity]['sha256'] != source['document_sha256']:
                raise integrity_error()
            documents[identity] = {'document_id': identity, 'sha256': source['document_sha256']}
        snapshot = {'schema_version': 1, 'matter_id': prepared['matter_id'],
                    'as_of': day(prepared['as_of'], 'as_of'),
                    'documents': list(documents.values()), 'editions': prepared['selected_editions'],
                    'markdown': markdown, 'markdown_sha256': digest,
                    'filename': 'services-pilot-' + prepared['as_of'] + '.md'}
        self._check_context(db, principal, snapshot)
        raw = canonical(snapshot)
        identity = uuid.uuid4().hex
        snapshot_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',
                   (identity, snapshot['matter_id'], KIND, raw, snapshot_hash, principal, now()))
        self.store._audit(db, principal, 'analysis_report_saved', identity, snapshot['matter_id'])
        return {**result, 'report_id': identity, 'report_snapshot_sha256': snapshot_hash, 'filename': snapshot['filename'],
                'report_saved': True, 'report_authenticity_verified': True, 'legal_acceptance': False}

    def read(self, principal, matter, identity, expected_hash):
        matter = text(matter, 'matter_id', 64)
        identity = text(identity, 'report_id', 64)
        if not isinstance(expected_hash, str) or not HASH.fullmatch(expected_hash):
            raise ValueError('markdown_sha256: требуется SHA-256 сохранённого отчёта.')
        with self.store.connect(True) as db:
            self.store._access(db, principal, matter)
            row = db.execute('SELECT * FROM artifacts WHERE id=? AND matter_id=? AND kind=?',
                             (identity, matter, KIND)).fetchone()
            if row is None:
                raise WorkspaceError(403, 'analysis_report_access_denied', 'Нет доступа к сохранённому отчёту.')
            if hashlib.sha256(row['payload'].encode('utf-8')).hexdigest() != row['sha256']:
                raise integrity_error()
            try:
                snapshot = json.loads(row['payload'])
                markdown = snapshot['markdown']
                if (snapshot['schema_version'] != 1 or snapshot['matter_id'] != matter
                        or not isinstance(markdown, str) or not 1 <= len(markdown.encode('utf-8')) <= MAX_MARKDOWN_BYTES
                        or hashlib.sha256(markdown.encode('utf-8')).hexdigest() != snapshot['markdown_sha256']
                        or snapshot['filename'] != 'services-pilot-' + day(snapshot['as_of'], 'as_of') + '.md'):
                    raise integrity_error()
            except (KeyError, TypeError, ValueError):
                raise integrity_error() from None
            if snapshot['markdown_sha256'] != expected_hash:
                raise WorkspaceError(409, 'analysis_report_changed', 'Отпечаток отчёта не совпадает. Сформируйте анализ заново.')
            try:
                self._check_context(db, principal, snapshot)
            except (KeyError, TypeError, ValueError):
                raise integrity_error() from None
            self.store._audit(db, principal, 'analysis_report_downloaded', identity, matter)
            return {'report_id': identity, 'matter_id': matter, 'as_of': snapshot['as_of'],
                    'markdown': markdown, 'markdown_sha256': snapshot['markdown_sha256'],
                    'filename': snapshot['filename'], 'report_snapshot_sha256': row['sha256'],
                    'context_current': True, 'report_authenticity_verified': True,
                    'legal_acceptance': False, 'digital_signature': False}
