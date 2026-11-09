"""Prepare existing synthetic cases for real human answers and independent review."""
import hashlib
import json
from pathlib import Path
import re
import uuid

from reviewed_evidence import ReviewedEvidence, day
from workspace_store import WorkspaceError, canonical, now, text

ROOT = Path(__file__).resolve().parent


class BenchmarkTemplateError(ValueError):
    pass


def load_templates(path=ROOT / 'fixtures/legal_benchmarks.json'):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('too large')
        data = json.loads(raw)
        if (not isinstance(data, dict) or data.get('lawyer_reviewed') is not False
                or data.get('verified') is not False or not isinstance(data.get('cases'), list)
                or not 1 <= len(data['cases']) <= 200):
            raise ValueError('invalid draft cases')
        result = {}
        for row in data['cases']:
            if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                    or not re.fullmatch(r'LAW-\d{2}-B\d{2}', row['id']) or row['id'] in result
                    or row.get('synthetic') is not True or row.get('approved_legal_answer') is not None
                    or row['review']['approved'] is not False):
                raise ValueError('invalid draft case')
            for field in ('facts', 'expected_behavior', 'forbidden_conclusion', 'module_id'):
                text(row[field], field, 10000)
            day(row['as_of'], 'as_of')
            if not isinstance(row['source_card_ids'], list) or any(not isinstance(value, str) for value in row['source_card_ids']):
                raise ValueError('invalid source references')
            result[row['id']] = row
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        raise BenchmarkTemplateError('Черновые задания для экспертной проверки недоступны.') from exc


class BenchmarkTasks:
    def __init__(self, store):
        self.store, self.evidence = store, ReviewedEvidence(store)

    def import_tasks(self, principal, matter, case_ids=None):
        templates = load_templates()
        if case_ids is None:
            case_ids = list(templates)
        if (not isinstance(case_ids, list) or not 1 <= len(case_ids) <= 200
                or any(not isinstance(value, str) or value not in templates for value in case_ids)
                or len(set(case_ids)) != len(case_ids)):
            raise ValueError('Требуется неповторяющийся список известных case_ids.')
        created, reused, changed = 0, 0, 0
        with self.store.connect(True) as db:
            self.store._access(db, principal, matter, ('owner', 'editor'))
            for case_id in case_ids:
                raw = canonical(templates[case_id]); digest = hashlib.sha256(raw.encode()).hexdigest()
                existing = db.execute('SELECT sha256 FROM benchmark_tasks WHERE matter_id=? AND case_id=?', (matter, case_id)).fetchone()
                if existing:
                    reused += 1
                    changed += existing['sha256'] != digest
                    continue
                db.execute('INSERT INTO benchmark_tasks VALUES(?,?,?,?,?,?,?,NULL)',
                           (uuid.uuid4().hex, matter, case_id, raw, digest, principal, now()))
                created += 1
            self.store._audit(db, principal, 'benchmark_tasks_imported', matter, matter)
        return {'created': created, 'reused': reused, 'template_changes_detected': changed,
                'answers_generated': 0, 'reviews_generated': 0, 'legal_acceptance': False}

    def _task(self, db, principal, identity, *, write=False):
        identity = text(identity, 'task_id', 64)
        row = db.execute('SELECT * FROM benchmark_tasks WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise WorkspaceError(403, 'matter_access_denied', 'Нет доступа к заданию.')
        self.store._access(db, principal, row['matter_id'], ('owner', 'editor') if write else ('owner', 'editor', 'viewer'))
        if hashlib.sha256(row['payload'].encode()).hexdigest() != row['sha256']:
            raise WorkspaceError(503, 'benchmark_task_integrity', 'Нарушена целостность задания.')
        return {**dict(row), 'payload': json.loads(row['payload'])}

    def list_tasks(self, principal, matter):
        with self.store.connect() as db:
            self.store._access(db, principal, matter)
            result = []
            for identity in db.execute('SELECT id FROM benchmark_tasks WHERE matter_id=? ORDER BY case_id LIMIT 200', (matter,)):
                row = self._task(db, principal, identity['id'])
                artifact = self.evidence._artifact(db, principal, row['artifact_id']) if row['artifact_id'] else None
                result.append({'task_id': row['id'], 'case_id': row['case_id'],
                               'facts': row['payload']['facts'], 'as_of': row['payload']['as_of'],
                               'artifact_id': row['artifact_id'], 'synthetic': True,
                               'status': 'needs_expert_answer' if artifact is None else 'review_recorded' if artifact['review_status'] == 'approved' else 'answer_draft_created',
                               'review_status': artifact['review_status'] if artifact else 'not_submitted'})
        return {'tasks': result, 'total': len(result),
                'needs_expert_answer': sum(row['status'] == 'needs_expert_answer' for row in result),
                'reviews_recorded': sum(row['status'] == 'review_recorded' for row in result),
                'legal_acceptance': False, 'reviewer_qualification_verified': False}

    def read(self, principal, identity):
        with self.store.connect(True) as db:
            row = self._task(db, principal, identity)
            artifact = self.evidence._artifact(db, principal, row['artifact_id']) if row['artifact_id'] else None
            self.store._audit(db, principal, 'benchmark_task_read', row['id'], row['matter_id'])
        return {'task_id': row['id'], 'source_case': row['payload'], 'template_sha256': row['sha256'],
                'current_artifact_id': row['artifact_id'], 'current_draft': artifact,
                'legal_acceptance': False, 'answer_generation_performed': False}

    def save_draft(self, principal, identity, payload, previous_artifact_id=None):
        with self.store.connect(True) as db:
            task = self._task(db, principal, identity, write=True)
            if task['artifact_id'] != previous_artifact_id:
                raise WorkspaceError(409, 'benchmark_task_changed', 'Другой черновик уже сохранён. Откройте задание заново.')
            if not isinstance(payload, dict) or set(payload) != {'expected_answer', 'required_risks', 'forbidden_claims', 'citations'}:
                raise ValueError('Требуются ожидаемый ответ, риски, запрещённые выводы и цитаты.')
            value = self.evidence._benchmark(db, principal, task['matter_id'],
                {**payload, 'question': task['payload']['facts'], 'as_of': task['payload']['as_of']})
            raw = canonical(value); digest = hashlib.sha256(raw.encode()).hexdigest(); artifact_id = uuid.uuid4().hex
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',
                       (artifact_id, task['matter_id'], 'benchmark', raw, digest, principal, now()))
            db.execute('UPDATE benchmark_tasks SET artifact_id=? WHERE id=?', (artifact_id, task['id']))
            self.store._audit(db, principal, 'benchmark_answer_drafted', artifact_id, task['matter_id'])
        return {'task_id': task['id'], 'artifact_id': artifact_id, 'sha256': digest,
                'review_status': 'draft', 'independent_review_required': True, 'legal_acceptance': False}
