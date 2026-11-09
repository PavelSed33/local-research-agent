"""Frozen holdouts and paired human-score comparisons; never automatic promotion."""
import hashlib
import json
import uuid
from pathlib import Path

from reviewed_evidence import ReviewedEvidence, normalized
from workspace_store import WorkspaceError, canonical, now, text

KIND = 'control_set'
MAX_SETS = 50


def control_plan():
    try:
        raw = (Path(__file__).parent / 'fixtures/quality_control_plan.json').read_bytes()
        if len(raw) > 20000:
            raise ValueError('plan too large')
        plan = json.loads(raw)
        control, training = plan['control_case_ids'], plan['training_case_ids']
        if (plan['schema_version'] != 1 or plan['lawyer_reviewed'] is not False
                or plan['approved_legal_answers'] != 0 or len(control) != 10 or len(training) != 15
                or len(set(control + training)) != 25
                or set(control + training) != {f'SERV-P{i:02}' for i in range(1, 26)}):
            raise ValueError('invalid control partition')
        return plan
    except (OSError, ValueError, KeyError, TypeError):
        raise WorkspaceError(503, 'control_plan_unavailable', 'План контрольных задач недоступен; память не используется.') from None


def holdouts(store, db, principal, matter):
    rows = list(db.execute('SELECT id FROM artifacts WHERE matter_id=? AND kind=? ORDER BY id LIMIT ?',
                           (matter, KIND, MAX_SETS + 1)))
    if len(rows) > MAX_SETS:
        raise WorkspaceError(413, 'control_set_limit', 'Слишком много контрольных наборов; обучение остановлено без усечения исключений.')
    ids, questions = set(), set()
    evidence = ReviewedEvidence(store)
    for row in rows:
        record = evidence._artifact(db, principal, row['id'])
        for member in record['payload']['members']:
            ids.add(member['benchmark_id'])
            questions.add(member['normalized_question'])
    planned = control_plan()['control_case_ids']
    tasks = db.execute('SELECT payload,sha256,artifact_id FROM benchmark_tasks WHERE matter_id=? AND case_id IN (' + ','.join('?' for _ in planned) + ')', (matter, *planned))
    for task in tasks:
        if hashlib.sha256(task['payload'].encode()).hexdigest() != task['sha256']:
            raise WorkspaceError(503, 'control_task_integrity', 'Контрольное задание изменилось.')
        questions.add(normalized(json.loads(task['payload'])['facts']))
        if task['artifact_id']:
            ids.add(task['artifact_id'])
    return ids, questions


class ControlEvaluation:
    def __init__(self, store):
        self.store, self.evidence = store, ReviewedEvidence(store)

    def _benchmark(self, db, principal, matter, identity):
        row = self.evidence._artifact(db, principal, identity)
        if row['matter_id'] != matter or row['kind'] != 'benchmark' or row['review_status'] != 'approved':
            raise WorkspaceError(409, 'control_benchmark_required', 'Контрольный набор требует одобренные эталоны этого дела.')
        for ref in row['payload']['citations']:
            document = self.store._document(db, principal, ref['document_id'])
            if (document['matter_id'] != matter or document['sha256'] != ref['document_sha256']
                    or document['text'][ref['start']:ref['end']] != ref['quote']):
                raise WorkspaceError(503, 'control_source_integrity', 'Источник контрольного эталона изменился.')
        return row

    def create(self, principal, matter, title, identities):
        title = text(title, 'title', 200)
        if (not isinstance(identities, list) or not 1 <= len(identities) <= 100
                or any(not isinstance(value, str) or not 1 <= len(value) <= 64 for value in identities)
                or len(set(identities)) != len(identities)):
            raise ValueError('benchmark_ids: от 1 до 100 различных идентификаторов одобренных эталонов.')
        with self.store.connect(True) as db:
            self.store._access(db, principal, matter, ('owner', 'editor'))
            if db.execute('SELECT COUNT(*) FROM artifacts WHERE matter_id=? AND kind=?', (matter, KIND)).fetchone()[0] >= MAX_SETS:
                raise WorkspaceError(413, 'control_set_limit', 'До 50 неизменяемых контрольных наборов в деле.')
            members = []
            for identity in identities:
                row = self._benchmark(db, principal, matter, identity)
                members.append({'benchmark_id': identity, 'artifact_sha256': row['sha256'],
                    'review_sha256': hashlib.sha256(canonical(row['review']).encode()).hexdigest(),
                    'normalized_question': normalized(row['payload']['question'])})
            identity, raw = uuid.uuid4().hex, canonical({'title': title, 'members': members})
            if len(raw.encode()) > 256 * 1024:
                raise WorkspaceError(413, 'control_set_size', 'Контрольный набор больше 256 КиБ; состав не обрезался.')
            digest = hashlib.sha256(raw.encode()).hexdigest()
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)', (identity, matter, KIND, raw, digest, principal, now()))
            self.store._audit(db, principal, 'control_set_created', identity, matter)
        return {'set_id': identity, 'sha256': digest, 'benchmark_count': len(members), 'membership_frozen': True,
                'automatically_excluded_from_learning': True, 'legal_acceptance': False}

    def listing(self, principal, matter):
        with self.store.connect() as db:
            self.store._access(db, principal, matter)
            rows = list(db.execute('SELECT id FROM artifacts WHERE matter_id=? AND kind=? ORDER BY created_on,id LIMIT ?', (matter, KIND, MAX_SETS + 1)))
            if len(rows) > MAX_SETS:
                raise WorkspaceError(413, 'control_set_limit', 'Слишком много контрольных наборов.')
            sets = []
            for row in rows:
                record = self.evidence._artifact(db, principal, row['id'])
                sets.append({'set_id': record['id'], 'title': record['payload']['title'],
                             'benchmark_count': len(record['payload']['members']), 'sha256': record['sha256']})
        return {'sets': sets, 'legal_acceptance': False}

    def register_same_answer(self, principal, matter, identity, evaluation_id, label, declared):
        """An explicit human attribution for identical outputs; no invented second score."""
        identity, evaluation_id = text(identity, 'set_id', 64), text(evaluation_id, 'evaluation_id', 64)
        label = text(label, 'run_label', 80)
        if declared is not True:
            raise ValueError('Требуется same_answer_declared=true: версия вернула дословно тот же ответ.')
        with self.store.connect(True) as db:
            self.store._access(db, principal, matter)
            if self.store._user(db, principal)['role'] != 'reviewer':
                raise WorkspaceError(403, 'reviewer_required', 'Нужен пользователь с ролью эксперта.')
            control = self.evidence._artifact(db, principal, identity)
            if control['kind'] != KIND or control['matter_id'] != matter:
                raise WorkspaceError(403, 'control_access_denied', 'Нет доступа к контрольному набору.')
            row = db.execute('SELECT * FROM evaluations WHERE id=? AND reviewer_id=?', (evaluation_id, principal)).fetchone()
            if row is None or row['created_on'] < control['created_on']:
                raise ValueError('Нужна собственная оценка, записанная после создания контрольного набора.')
            member = next((value for value in control['payload']['members'] if value['benchmark_id'] == row['artifact_id']), None)
            if member is None:
                raise ValueError('Оценка не относится к контрольному набору.')
            benchmark = self._benchmark(db, principal, matter, row['artifact_id'])
            if (hashlib.sha256(canonical(benchmark['review']).encode()).hexdigest() != member['review_sha256']
                    or json.loads(row['scores']).get('benchmark_review_sha256') != member['review_sha256']
                    or hashlib.sha256(row['response_text'].encode()).hexdigest() != row['response_sha256']):
                raise WorkspaceError(409, 'control_review_changed', 'Оценка или эталон изменились.')
            aliases = self._aliases(db, principal, matter)
            if len(aliases) >= 1000:
                raise WorkspaceError(413, 'control_alias_limit', 'До 1000 привязок одинаковых ответов в деле.')
            if any(value['set_id'] == identity and value['evaluation_id'] == evaluation_id and value['run_label'] == label for value in aliases):
                raise WorkspaceError(409, 'duplicate_control_run', 'Эта привязка уже записана.')
            raw = canonical({'set_id': identity, 'evaluation_id': evaluation_id, 'run_label': label,
                             'evaluation_sha256': hashlib.sha256(canonical(dict(row)).encode()).hexdigest(),
                             'same_answer_declared': True})
            run_id = uuid.uuid4().hex
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)', (run_id, matter, 'control_run', raw,
                       hashlib.sha256(raw.encode()).hexdigest(), principal, now()))
            self.store._audit(db, principal, 'control_same_answer_registered', run_id, matter)
        return {'run_id': run_id, 'response_sha256': row['response_sha256'], 'run_provenance_verified': False, 'legal_acceptance': False}

    def _aliases(self, db, principal, matter):
        rows = list(db.execute("SELECT id FROM artifacts WHERE matter_id=? AND kind='control_run' LIMIT 1001", (matter,)))
        if len(rows) > 1000:
            raise WorkspaceError(413, 'control_alias_limit', 'Слишком много привязок одинаковых ответов.')
        return [self.evidence._artifact(db, principal, row['id'])['payload'] for row in rows]

    def compare(self, principal, matter, identity, baseline, candidate):
        identity = text(identity, 'set_id', 64)
        baseline, candidate = text(baseline, 'baseline_label', 80), text(candidate, 'candidate_label', 80)
        if baseline == candidate:
            raise ValueError('Метки сравниваемых версий должны различаться.')
        with self.store.connect(True) as db:
            self.store._access(db, principal, matter)
            control = self.evidence._artifact(db, principal, identity)
            if control['matter_id'] != matter or control['kind'] != KIND:
                raise WorkspaceError(403, 'control_access_denied', 'Нет доступа к контрольному набору.')
            aliases = self._aliases(db, principal, matter)
            mapped_aliases = {}
            for alias in aliases:
                if alias['set_id'] != identity or alias['run_label'] not in {baseline, candidate}:
                    continue
                row = db.execute('SELECT * FROM evaluations WHERE id=?', (alias['evaluation_id'],)).fetchone()
                if row is None or hashlib.sha256(canonical(dict(row)).encode()).hexdigest() != alias['evaluation_sha256']:
                    raise WorkspaceError(409, 'control_score_changed', 'Оценка одинакового ответа изменилась.')
                mapped_aliases.setdefault(row['artifact_id'], []).append((row, alias['run_label']))
            results, regression, improvement, incomplete = [], False, False, False
            for member in control['payload']['members']:
                benchmark = self._benchmark(db, principal, matter, member['benchmark_id'])
                if (benchmark['sha256'] != member['artifact_sha256']
                        or hashlib.sha256(canonical(benchmark['review']).encode()).hexdigest() != member['review_sha256']):
                    raise WorkspaceError(409, 'control_review_changed', 'Эталон или его одобрение изменились. Создайте новый контрольный набор.')
                groups = {baseline: {}, candidate: {}}
                rows = list(db.execute('SELECT * FROM evaluations WHERE artifact_id=? ORDER BY id LIMIT 201', (member['benchmark_id'],)))
                if len(rows) > 200:
                    raise WorkspaceError(413, 'control_score_limit', 'До 200 оценок на контрольный эталон; сравнение не усекалось.')
                attributed = [(row, None) for row in rows]
                attributed.extend(mapped_aliases.get(member['benchmark_id'], []))
                seen = set()
                for row, attributed_label in attributed:
                    score = json.loads(row['scores'])
                    label = attributed_label or score.get('run_label')
                    if label not in groups or score.get('benchmark_review_sha256') != member['review_sha256']:
                        continue
                    if row['created_on'] < control['created_on'] or (row['id'], label) in seen:
                        continue
                    seen.add((row['id'], label))
                    if hashlib.sha256(row['response_text'].encode()).hexdigest() != row['response_sha256']:
                        raise WorkspaceError(503, 'control_score_integrity', 'Нарушена целостность оценённого ответа.')
                    risk_count = len(benchmark['payload']['required_risks'])
                    if (type(score.get('correctness')) is not int or not 0 <= score['correctness'] <= 4
                            or type(score.get('unsupported_claim_count')) is not int or not 0 <= score['unsupported_claim_count'] <= 1000
                            or score.get('required_risk_count') != risk_count or not isinstance(score.get('missed_risks'), list)
                            or any(type(index) is not int or not 0 <= index < risk_count for index in score['missed_risks'])
                            or len(set(score['missed_risks'])) != len(score['missed_risks'])):
                        raise WorkspaceError(503, 'control_score_integrity', 'Некорректные показатели экспертной оценки.')
                    groups[label].setdefault(row['reviewer_id'], []).append((row['id'], row['response_sha256'], score))
                reviewers = set(groups[baseline])
                complete = bool(reviewers) and reviewers == set(groups[candidate]) and all(
                    len(values) == 1 for group in groups.values() for values in group.values())
                entry = {'benchmark_id': member['benchmark_id'], 'status': 'paired' if complete else 'missing_or_ambiguous_scores'}
                if not complete:
                    incomplete = True; results.append(entry); continue
                pairs = []
                for reviewer in sorted(reviewers):
                    left, right = groups[baseline][reviewer][0], groups[candidate][reviewer][0]
                    def values(item):
                        score = item[2]
                        return [score['correctness'], len(score['missed_risks']), score['unsupported_claim_count']]
                    a, b = values(left), values(right)
                    new_missed = sorted(set(right[2]['missed_risks']) - set(left[2]['missed_risks']))
                    worse = b[0] < a[0] or b[1] > a[1] or b[2] > a[2] or bool(new_missed)
                    better = b[0] > a[0] or b[1] < a[1] or b[2] < a[2]
                    regression |= worse; improvement |= better
                    pairs.append({'reviewer_id': reviewer, 'baseline_evaluation_id': left[0], 'candidate_evaluation_id': right[0],
                        'baseline_response_sha256': left[1], 'candidate_response_sha256': right[1],
                        'baseline': a, 'candidate': b, 'new_missed_risk_indices': new_missed, 'regression': worse})
                entry.update(pairs=pairs, regression=any(pair['regression'] for pair in pairs))
                for label, key in ((baseline, 'baseline'), (candidate, 'candidate')):
                    entry[key] = {name: sum(pair[key][i] for pair in pairs) / len(pairs)
                                  for i, name in enumerate(('correctness', 'missed_risks', 'unsupported_claims'))}
                results.append(entry)
            paired = [row for row in results if row['status'] == 'paired']
            gate = 'regression' if regression else 'incomplete' if incomplete else 'no_regression'
            averages = {key: {name: sum(row[key][name] for row in paired) / len(paired) if paired else None
                             for name in ('correctness', 'missed_risks', 'unsupported_claims')} for key in ('baseline', 'candidate')}
            self.store._audit(db, principal, 'control_versions_compared', identity, matter)
        return {'set_id': identity, 'set_sha256': control['sha256'], 'baseline_label': baseline, 'candidate_label': candidate,
                'task_count': len(results), 'paired_task_count': len(paired), 'tasks': results, 'averages': averages,
                'gate': gate, 'regression_detected': regression, 'improvement_observed': improvement and gate == 'no_regression',
                'eligible_for_expert_review': gate == 'no_regression', 'adoption_approved': False,
                'run_provenance_verified': False, 'quality_improvement_proven': False, 'legal_acceptance': False,
                'limitation': 'Оценки введены людьми; связь ответов с реальными запусками и квалификация экспертов автоматически не подтверждены. Каждая задача имеет равный вес. Любое ухудшение отдельной пары блокирует предложение принять изменение; отсутствие регрессии не означает юридической приёмки.'}
