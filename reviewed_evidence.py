"""Immutable evidence dossiers and authenticated human review, never auto-certification."""
from datetime import date
import hashlib
import json
import re
from urllib.parse import urlsplit
import uuid

from workspace_store import WorkspaceError, canonical, now, text


def day(value, name):
    if not isinstance(value, str):
        raise ValueError(f'{name}: требуется дата YYYY-MM-DD.')
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f'{name}: требуется реальная дата YYYY-MM-DD.') from None
    if result.isoformat() != value:
        raise ValueError(f'{name}: требуется дата YYYY-MM-DD.')
    return value


class ReviewedEvidence:
    def __init__(self, store):
        self.store = store

    def _quote(self, db, principal, matter, citation):
        if not isinstance(citation, dict) or set(citation) != {'document_id', 'quote'}:
            raise ValueError('Цитата требует document_id и quote.')
        document = self.store._document(db, principal, citation['document_id'])
        if document['matter_id'] != matter:
            raise ValueError('Источник относится к другому делу.')
        quote = text(citation['quote'], 'quote', 10000, 10)
        start = document['text'].find(quote)
        if start < 0:
            raise ValueError('Цитата отсутствует в документе.')
        return {**citation, 'start': start, 'end': start + len(quote),
                'document_sha256': document['sha256'], 'quote_checked': True}

    def _artifact(self, db, principal, identity):
        row = db.execute('SELECT * FROM artifacts WHERE id=?', (identity,)).fetchone()
        if not row:
            raise WorkspaceError(403, 'matter_access_denied', 'Нет доступа к материалу.')
        self.store._access(db, principal, row['matter_id'])
        result = dict(row)
        if hashlib.sha256(result['payload'].encode()).hexdigest() != result['sha256']:
            raise WorkspaceError(503, 'evidence_integrity', 'Нарушена целостность материала.')
        result['payload'] = json.loads(result['payload'])
        latest = db.execute('SELECT decision,reviewer_id,created_on,artifact_sha256 FROM reviews WHERE artifact_id=? ORDER BY id DESC LIMIT 1', (identity,)).fetchone()
        result['review'] = dict(latest) if latest else None
        result['review_status'] = latest['decision'] if latest and latest['artifact_sha256'] == result['sha256'] else 'draft'
        result.update(legal_acceptance=False, reviewer_qualification_verified=False)
        return result

    def add(self, principal, matter, kind, payload):
        if not isinstance(kind, str) or kind not in {'edition', 'decision', 'benchmark'} or not isinstance(payload, dict):
            raise ValueError('Допустимы edition, decision, benchmark и объект payload.')
        with self.store.connect(True) as db:
            self.store._access(db, principal, matter, ('owner', 'editor'))
            if kind == 'edition':
                value = self._edition(db, principal, matter, payload)
            elif kind == 'decision':
                value = self._decision(db, principal, matter, payload)
            else:
                value = self._benchmark(db, principal, matter, payload)
            identity, raw = uuid.uuid4().hex, canonical(value)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)', (identity, matter, kind, raw, digest, principal, now()))
            self.store._audit(db, principal, kind + '_added', identity, matter)
        return {'artifact_id': identity, 'sha256': digest, 'review_status': 'draft', 'legal_acceptance': False}

    def _edition(self, db, principal, matter, payload):
        required = {'act_id', 'provision', 'document_id', 'valid_from', 'valid_to', 'period_evidence', 'full_text_declared'}
        if set(payload) != required or payload['full_text_declared'] is not True:
            raise ValueError('Редакция требует реквизиты, интервал, источники периода и full_text_declared=true.')
        value = dict(payload)
        value['act_id'] = text(value['act_id'], 'act_id', 100)
        value['provision'] = text(value['provision'], 'provision', 100)
        begin = day(value['valid_from'], 'valid_from')
        end = day(value['valid_to'], 'valid_to') if value['valid_to'] is not None else None
        if end and end <= begin:
            raise ValueError('Период должен быть непустым; valid_to — исключённая верхняя граница.')
        document = self.store._document(db, principal, value['document_id'])
        if document['matter_id'] != matter:
            raise ValueError('Документ относится к другому делу.')
        citations = value['period_evidence']
        if not isinstance(citations, list) or not 1 <= len(citations) <= 20:
            raise ValueError('Требуются цитаты источников периода действия.')
        value['period_evidence'] = [self._quote(db, principal, matter, citation) for citation in citations]
        proof = '\n'.join(row['quote'] for row in value['period_evidence'])
        for period_day in (begin, end):
            if period_day and period_day not in proof and date.fromisoformat(period_day).strftime('%d.%m.%Y') not in proof:
                raise ValueError('Дата границы периода отсутствует в представленных цитатах.')
        value.update(document_sha256=document['sha256'], source_url=document['source_url'],
                     captured_on=document['captured_on'], completeness_verified=False)
        return value

    def _decision(self, db, principal, matter, payload):
        if set(payload) != {'case_number', 'court', 'decision_date', 'instance', 'identity', 'outcome', 'positions', 'links', 'full_text_declared'}:
            raise ValueError('Неполные или неизвестные поля судебного акта.')
        value = dict(payload)
        for key in ('case_number', 'court', 'instance'):
            value[key] = text(value[key], key)
        value['decision_date'] = day(value['decision_date'], 'decision_date')
        if value['decision_date'] > date.today().isoformat() or value['full_text_declared'] is not True:
            raise ValueError('Требуется полученный полный текст акта без будущей даты.')
        identity = self._quote(db, principal, matter, value['identity'])
        quote = identity['quote']
        if (value['case_number'] not in quote or value['court'] not in quote or value['instance'].casefold() not in quote.casefold()
                or (value['decision_date'] not in quote and date.fromisoformat(value['decision_date']).strftime('%d.%m.%Y') not in quote)):
            raise ValueError('Реквизиты суда, дела и даты должны присутствовать в общей цитате.')
        value['identity'], value['outcome'] = identity, self._quote(db, principal, matter, value['outcome'])
        if value['outcome']['document_id'] != identity['document_id']:
            raise ValueError('Результат должен цитировать сам судебный акт.')
        for field in ('positions', 'links'):
            if not isinstance(value[field], list) or len(value[field]) > 20:
                raise ValueError('Слишком много позиций или связей.')
        positions = []
        for position in value['positions']:
            if (not isinstance(position, dict) or set(position) != {'side', 'citation', 'applicability_note'}
                    or not isinstance(position['side'], str) or position['side'] not in {'supporting', 'opposing', 'distinguishing'}):
                raise ValueError('Некорректная позиция.')
            citation = self._quote(db, principal, matter, position['citation'])
            if citation['document_id'] != identity['document_id']:
                raise ValueError('Позиция должна цитировать сам судебный акт.')
            positions.append({'side': position['side'], 'citation': citation,
                              'applicability_note': text(position['applicability_note'], 'applicability_note', 3000),
                              'semantic_comparability_verified': False})
        value['positions'] = positions
        links = []
        for link in value['links']:
            if (not isinstance(link, dict) or set(link) != {'target_id', 'relation', 'citation'}
                    or not isinstance(link['relation'], str) or link['relation'] not in {'appeal', 'upheld', 'reversed', 'remanded'}):
                raise ValueError('Некорректная связь инстанций.')
            target = self._artifact(db, principal, link['target_id'])
            if (target['kind'] != 'decision' or target['matter_id'] != matter
                    or target['payload']['case_number'] != value['case_number']
                    or target['payload']['decision_date'] > value['decision_date']):
                raise ValueError('Связь должна относиться к более раннему акту того же дела.')
            citation = self._quote(db, principal, matter, link['citation'])
            target_date = target['payload']['decision_date']
            if (citation['document_id'] != identity['document_id'] or value['case_number'] not in citation['quote']
                    or (target_date not in citation['quote'] and date.fromisoformat(target_date).strftime('%d.%m.%Y') not in citation['quote'])):
                raise ValueError('Связь должна цитировать упоминание исходного акта в последующем.')
            links.append({**link, 'citation': citation, 'literal_connection_checked': True, 'legal_effect_verified': False})
        value['links'] = links
        return value

    def _benchmark(self, db, principal, matter, payload):
        if set(payload) != {'question', 'as_of', 'expected_answer', 'required_risks', 'forbidden_claims', 'citations'}:
            raise ValueError('Эталон требует вопрос, дату, ответ, риски, запрещённые выводы и цитаты.')
        value = dict(payload)
        for key in ('question', 'expected_answer'):
            value[key] = text(value[key], key, 30000)
        value['as_of'] = day(value['as_of'], 'as_of')
        for key in ('required_risks', 'forbidden_claims'):
            if not isinstance(value[key], list) or len(value[key]) > 50:
                raise ValueError('Список рисков/выводов — до 50 строк.')
            value[key] = [text(item, key, 1000) for item in value[key]]
            if len(set(value[key])) != len(value[key]):
                raise ValueError('Повторяющиеся риски/выводы.')
        if not isinstance(value['citations'], list) or not 1 <= len(value['citations']) <= 20:
            raise ValueError('Эталон требует цитаты источников.')
        value['citations'] = [self._quote(db, principal, matter, row) for row in value['citations']]
        return value

    def get(self, principal, identity):
        with self.store.connect(True) as db:
            row = self._artifact(db, principal, identity)
            self.store._audit(db, principal, 'evidence_read', identity, row['matter_id'])
            return row

    def queue(self, principal, matter):
        result = []
        with self.store.connect() as db:
            self.store._access(db, principal, matter)
            for identity in db.execute('SELECT id FROM artifacts WHERE matter_id=? ORDER BY created_on LIMIT 1000', (matter,)):
                row = self._artifact(db, principal, identity[0])
                result.append({'artifact_id': row['id'], 'kind': row['kind'], 'sha256': row['sha256'],
                              'created_by': row['created_by'], 'review_status': row['review_status'],
                              'label': (row['payload'].get('question') or row['payload'].get('case_number') or
                                        row['payload'].get('act_id', '') + ' / ' + row['payload'].get('provision', ''))[:200]})
        return {'artifacts': result, 'legal_acceptance': False}

    def review(self, principal, identity, decision, note, attestations):
        if not isinstance(decision, str) or decision not in {'approved', 'rejected', 'revoked'}:
            raise ValueError('Допустимы approved, rejected, revoked.')
        note = text(note, 'note', 5000)
        if not isinstance(attestations, dict) or any(type(value) is not bool for value in attestations.values()):
            raise ValueError('Некорректные заверения проверяющего.')
        required = {'source_authenticity', 'completeness', 'temporal_applicability', 'legal_reasoning'}
        if set(attestations) != required or (decision == 'approved' and not all(attestations.values())):
            raise ValueError('Для одобрения требуется явно подтвердить четыре аспекта проверки.')
        with self.store.connect(True) as db:
            reviewer = self.store._user(db, principal)
            row = self._artifact(db, principal, identity)
            if reviewer['role'] != 'reviewer' or row['created_by'] == principal:
                raise WorkspaceError(403, 'independent_reviewer_required', 'Нужен другой пользователь с ролью reviewer.')
            if decision == 'approved' and row['kind'] == 'edition':
                document = self.store._document(db, principal, row['payload']['document_id'])
                host = urlsplit(document['source_url']).hostname or ''
                if host not in {'pravo.gov.ru', 'publication.pravo.gov.ru', 'actual.pravo.gov.ru', 'vsrf.ru', 'www.vsrf.ru'}:
                    raise ValueError('Для одобрения редакции требуется ссылка на первичный официальный источник.')
            db.execute('INSERT INTO reviews(artifact_id,artifact_sha256,decision,reviewer_id,note,attestations,created_on) VALUES(?,?,?,?,?,?,?)',
                       (identity, row['sha256'], decision, principal, note, canonical(attestations), now()))
            self.store._audit(db, principal, 'evidence_' + decision, identity, row['matter_id'])
        return {'artifact_id': identity, 'review_status': decision, 'reviewer_id': principal,
                'review_recorded': True, 'reviewer_qualification_verified': False, 'legal_acceptance': False}

    def select_edition(self, principal, matter, act_id, provision, as_of):
        as_of = day(as_of, 'as_of')
        act_id, provision = text(act_id, 'act_id', 100), text(provision, 'provision', 100)
        with self.store.connect(True) as db:
            self.store._access(db, principal, matter)
            identities = [row[0] for row in db.execute("SELECT id FROM artifacts WHERE matter_id=? AND kind='edition'", (matter,))]
            matches = []
            for identity in identities:
                row = self._artifact(db, principal, identity)
                payload = row['payload']
                if (row['review_status'] == 'approved' and payload['act_id'] == act_id and payload['provision'] == provision
                        and payload['valid_from'] <= as_of and (payload['valid_to'] is None or as_of < payload['valid_to'])):
                    matches.append(row)
            self.store._audit(db, principal, 'edition_selected', matter, matter)
        return {'status': 'review_recorded' if len(matches) == 1 else 'ambiguous' if matches else 'no_reviewed_edition',
                'edition': matches[0] if len(matches) == 1 else None, 'matching_count': len(matches),
                'as_of': as_of, 'legal_acceptance': False, 'source_authenticity_automatically_verified': False}

    def timeline(self, principal, matter, case_number, as_of):
        as_of = day(as_of, 'as_of')
        case_number = text(case_number, 'case_number')
        with self.store.connect(True) as db:
            self.store._access(db, principal, matter)
            rows = [self._artifact(db, principal, row[0]) for row in db.execute("SELECT id FROM artifacts WHERE matter_id=? AND kind='decision'", (matter,))]
            matching = [row for row in rows if row['payload']['case_number'] == case_number]
            selected = sorted((row for row in matching if row['payload']['decision_date'] <= as_of), key=lambda row: row['payload']['decision_date'])
            self.store._audit(db, principal, 'case_timeline', matter, matter)
        return {'decisions': selected, 'later_decisions_excluded': len(matching) - len(selected),
                'complete_history': False, 'legal_acceptance': False, 'as_of': as_of}

    def score(self, principal, identity, answer, correctness, missed_risks, unsupported_claim_count, note):
        answer = text(answer, 'answer', 200000)
        note = text(note, 'note', 5000)
        if type(correctness) is not int or not 0 <= correctness <= 4 or type(unsupported_claim_count) is not int or not 0 <= unsupported_claim_count <= 1000:
            raise ValueError('correctness: 0–4; unsupported_claim_count: 0–1000, целые числа.')
        with self.store.connect(True) as db:
            row = self._artifact(db, principal, identity)
            if self.store._user(db, principal)['role'] != 'reviewer' or row['kind'] != 'benchmark' or row['review_status'] != 'approved':
                raise WorkspaceError(403, 'reviewed_benchmark_required', 'Нужны reviewer и одобренный эталон.')
            required = row['payload']['required_risks']
            if (not isinstance(missed_risks, list) or any(type(index) is not int or not 0 <= index < len(required) for index in missed_risks)
                    or len(set(missed_risks)) != len(missed_risks)):
                raise ValueError('missed_risks — неповторяющиеся индексы рисков эталона.')
            scores = {'correctness': correctness, 'missed_risks': missed_risks,
                      'required_risk_count': len(required), 'unsupported_claim_count': unsupported_claim_count, 'note': note}
            evaluation_id = uuid.uuid4().hex
            response_hash = hashlib.sha256(answer.encode()).hexdigest()
            if db.execute('SELECT 1 FROM evaluations WHERE artifact_id=? AND response_sha256=? AND reviewer_id=?', (identity, response_hash, principal)).fetchone():
                raise WorkspaceError(409, 'duplicate_evaluation', 'Этот эксперт уже оценил данный ответ по этому эталону.')
            db.execute('INSERT INTO evaluations VALUES(?,?,?,?,?,?,?)', (evaluation_id, identity,
                       response_hash, answer, canonical(scores), principal, now()))
            self.store._audit(db, principal, 'answer_scored', evaluation_id, row['matter_id'])
        return {'evaluation_id': evaluation_id, 'scores': scores, 'human_scoring_recorded': True, 'legal_acceptance': False}

    def metrics(self, principal, matter):
        excluded = 0
        with self.store.connect() as db:
            self.store._access(db, principal, matter)
            scores = []
            for row in db.execute('SELECT e.scores,a.id FROM evaluations e JOIN artifacts a ON e.artifact_id=a.id WHERE a.matter_id=?', (matter,)):
                if self._artifact(db, principal, row['id'])['review_status'] == 'approved':
                    scores.append(json.loads(row['scores']))
                else:
                    excluded += 1
        required = sum(row['required_risk_count'] for row in scores)
        return {'human_evaluations': len(scores),
                'excluded_due_to_review_change': excluded,
                'mean_correctness_0_to_4': sum(row['correctness'] for row in scores) / len(scores) if scores else None,
                'missed_required_risk_fraction': sum(len(row['missed_risks']) for row in scores) / required if required else None,
                'unsupported_claim_count': sum(row['unsupported_claim_count'] for row in scores),
                'status': 'human_scores_recorded' if scores else 'no_human_evaluations', 'legal_acceptance': False}
