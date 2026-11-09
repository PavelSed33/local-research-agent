"""Closed-input dispatcher for the optional per-user workspace API."""
import hashlib

from reviewed_evidence import ReviewedEvidence
from workspace_store import WorkspaceError, text

FIELDS = {
    '/workspace/me': set(), '/workspace/users/create': {'name', 'role'},
    '/workspace/users/revoke': {'user_id'}, '/workspace/matters/create': {'title'},
    '/workspace/matters/list': set(), '/workspace/members/set': {'matter_id', 'user_id', 'role'},
    '/workspace/documents/add': {'matter_id', 'title', 'document', 'source_url', 'captured_on', 'pages'},
    '/workspace/documents/list': {'matter_id'}, '/workspace/documents/read': {'document_id'},
    '/workspace/search': {'matter_id', 'query', 'limit'},
    '/workspace/ask': {'matter_id', 'question', 'demo', 'allow_cloud'},
    '/workspace/evidence/add': {'matter_id', 'kind', 'payload'},
    '/workspace/evidence/read': {'artifact_id'},
    '/workspace/evidence/review': {'artifact_id', 'decision', 'note', 'attestations'},
    '/workspace/editions/select': {'matter_id', 'act_id', 'provision', 'as_of'},
    '/workspace/cases/timeline': {'matter_id', 'case_number', 'as_of'},
    '/workspace/evaluations/record': {'artifact_id', 'answer', 'correctness', 'missed_risks', 'unsupported_claim_count', 'note'},
    '/workspace/evaluations/metrics': {'matter_id'}, '/workspace/audit': {'matter_id', 'limit'},
    '/workspace/sources/import': {'matter_id', 'source_id'},
    '/workspace/sources/list': set(),
    '/workspace/tokens/rotate': set(), '/workspace/matters/transfer': {'matter_id', 'user_id'},
    '/workspace/evidence/list': {'matter_id'}, '/workspace/users/list': set(),
    '/workspace/documents/upload': {'matter_id', 'title', 'filename', 'file_base64', 'source_url', 'ocr', 'ocr_language'},
    '/workspace/documents/original': {'document_id'},
}


def validate(path, payload):
    if not isinstance(payload, dict) or set(payload) - FIELDS[path]:
        raise ValueError('Неизвестные поля запроса рабочего пространства.')
    for key in ('matter_id', 'user_id', 'document_id', 'artifact_id'):
        if key in payload:
            text(payload[key], key, 64)


def namespace(principal, external_id):
    return hashlib.sha256((principal + ':' + external_id).encode()).hexdigest()


def dispatch(store, principal, path, payload):
    validate(path, payload)
    evidence = ReviewedEvidence(store)
    p = payload
    if path == '/workspace/me':
        with store.connect() as db:
            return store._user(db, principal)
    if path == '/workspace/sources/list':
        from primary_sources import source_catalog
        with store.connect() as db:
            store._user(db, principal)
        return source_catalog()
    if path == '/workspace/sources/import':
        from primary_sources import load_sources, original_bytes
        source_id = text(p.get('source_id'), 'source_id', 80)
        with store.connect() as db:
            store._access(db, principal, p.get('matter_id'), ('owner', 'editor'))
        sources = [row for row in load_sources() if row['id'] == source_id]
        if len(sources) != 1:
            raise ValueError('Неизвестный source_id.')
        source = sources[0]
        return store.add_document(principal, p.get('matter_id'),
            {'title': source['title'], 'document': source['text'], 'source_url': source['source_url'],
             'captured_on': source['captured_on'], 'pages': source['pages']},
             provenance={'origin': 'pinned_primary_archive', 'source_id': source['id'],
                         'original_sha256': source['original_sha256'], 'text_sha256': source['text_sha256'],
                         'original_bytes_archived': True, 'all_pdf_pages_extracted': True,
                         'current_validity_verified': False, 'legal_completeness_verified': False,
                         'edition_label': source['edition_label'], 'signed_on': source['signed_on'],
                         'history_complete': False, 'amendment_mentions': source['amendment_mentions']},
             original=(source['original_file'], original_bytes(source)), deduplicate_source=True)
    if path == '/workspace/users/create':
        return store.create_user(p.get('name'), p.get('role', 'member'), actor=principal)
    if path == '/workspace/users/list':
        with store.connect() as db:
            if store._user(db, principal)['role'] != 'admin':
                raise WorkspaceError(403, 'admin_required', 'Нужны права администратора.')
            return {'users': [dict(row) for row in db.execute('SELECT id,name,role,active FROM users LIMIT 1000')]}
    if path == '/workspace/users/revoke':
        return store.revoke_user(principal, p.get('user_id'))
    if path == '/workspace/tokens/rotate':
        return store.rotate_token(principal)
    if path == '/workspace/matters/transfer':
        return store.transfer_matter(principal, p.get('matter_id'), p.get('user_id'))
    if path == '/workspace/matters/create':
        return store.create_matter(principal, p.get('title'))
    if path == '/workspace/matters/list':
        return store.list_matters(principal)
    if path == '/workspace/members/set':
        return store.grant(principal, p.get('matter_id'), p.get('user_id'), p.get('role'))
    if path == '/workspace/documents/add':
        return store.add_document(principal, p.get('matter_id'), p)
    if path == '/workspace/documents/list':
        return store.documents(principal, p.get('matter_id'))
    if path == '/workspace/documents/read':
        return store.document(principal, p.get('document_id'))
    if path == '/workspace/documents/original':
        return store.original(principal, p.get('document_id'))
    if path == '/workspace/search':
        return store.search(principal, p.get('matter_id'), p.get('query'), p.get('limit', 8))
    if path == '/workspace/evidence/add':
        return evidence.add(principal, p.get('matter_id'), p.get('kind'), p.get('payload'))
    if path == '/workspace/evidence/read':
        return evidence.get(principal, p.get('artifact_id'))
    if path == '/workspace/evidence/list':
        return evidence.queue(principal, p.get('matter_id'))
    if path == '/workspace/evidence/review':
        return evidence.review(principal, p.get('artifact_id'), p.get('decision'), p.get('note'), p.get('attestations'))
    if path == '/workspace/editions/select':
        return evidence.select_edition(principal, p.get('matter_id'), p.get('act_id'), p.get('provision'), p.get('as_of'))
    if path == '/workspace/cases/timeline':
        return evidence.timeline(principal, p.get('matter_id'), p.get('case_number'), p.get('as_of'))
    if path == '/workspace/evaluations/record':
        return evidence.score(principal, p.get('artifact_id'), p.get('answer'), p.get('correctness'), p.get('missed_risks'), p.get('unsupported_claim_count'), p.get('note'))
    if path == '/workspace/evaluations/metrics':
        return evidence.metrics(principal, p.get('matter_id'))
    if path == '/workspace/audit':
        return store.audit(principal, p.get('matter_id'), p.get('limit', 100))
    raise ValueError('Неизвестное действие.')


def prepare_answer(store, principal, payload):
    validate('/workspace/ask', payload)
    question = text(payload.get('question'), 'question', 2000)
    for key in ('demo', 'allow_cloud'):
        if key in payload and type(payload[key]) is not bool:
            raise ValueError(f'{key}: требуется true или false.')
    context = store.search(principal, payload.get('matter_id'), question)
    return {'matter_id': payload.get('matter_id'), 'question': question,
            'sources': context['matches'], 'demo': payload.get('demo', False),
            'allow_cloud': payload.get('allow_cloud', False)}
