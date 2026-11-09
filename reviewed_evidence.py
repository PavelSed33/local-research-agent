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


def normalized(value):
    return ' '.join(value.casefold().split())


def date_quoted(value, quote):
    stamp = date.fromisoformat(value)
    months = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
              'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')
    forms = (value, stamp.strftime('%d.%m.%Y'),
             f'{stamp.day} {months[stamp.month - 1]} {stamp.year}')
    return any(re.search(r'(?<!\d)' + re.escape(form) + r'(?!\d)', normalized(quote)) for form in forms)


class ReviewedEvidence:
    def __init__(self, store):
        self.store = store

    def _quote(self, db, principal, matter, citation):
        if not isinstance(citation, dict) or set(citation) != {'document_id', 'quote'}:
            raise ValueError('Цитата требует document_id и quote.')
        document = self.store._document(db, principal, text(citation['document_id'], 'document_id', 64))
        if document['matter_id'] != matter:
            raise ValueError('Источник относится к другому делу.')
        quote = text(citation['quote'], 'quote', 10000, 10)
        start = document['text'].find(quote)
        if start < 0:
            raise ValueError('Цитата отсутствует в документе.')
        return {**citation, 'quote': quote, 'start': start, 'end': start + len(quote),
                'document_sha256': document['sha256'], 'quote_checked': True}

    def _artifact(self, db, principal, identity):
        identity = text(identity, 'artifact_id', 64)
        row = db.execute('SELECT * FROM artifacts WHERE id=?', (identity,)).fetchone()
        if not row:
            raise WorkspaceError(403, 'matter_access_denied', 'Нет доступа к материалу.')
        self.store._access(db, principal, row['matter_id'])
        result = dict(row)
        if hashlib.sha256(result['payload'].encode()).hexdigest() != result['sha256']:
            raise WorkspaceError(503, 'evidence_integrity', 'Нарушена целостность материала.')
        result['payload'] = json.loads(result['payload'])
        latest = db.execute('SELECT id,decision,reviewer_id,created_on,artifact_sha256 FROM reviews WHERE artifact_id=? ORDER BY id DESC LIMIT 1', (identity,)).fetchone()
        result['review'] = dict(latest) if latest else None
        result['review_status'] = latest['decision'] if latest and latest['artifact_sha256'] == result['sha256'] else 'draft'
        if result['kind'] == 'decision':
            revision = db.execute('SELECT root_artifact_id,previous_artifact_id FROM decision_revisions WHERE artifact_id=?', (identity,)).fetchone()
            successor = db.execute('SELECT artifact_id FROM decision_revisions WHERE previous_artifact_id=?', (identity,)).fetchone()
            result['version'] = {'root_artifact_id': revision['root_artifact_id'] if revision else identity,
                                 'previous_artifact_id': revision['previous_artifact_id'] if revision else None,
                                 'superseded_by': successor['artifact_id'] if successor else None}
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
        if not required <= payload.keys() or set(payload) - required - {'provision_quote'} or payload['full_text_declared'] is not True:
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
            if period_day and not date_quoted(period_day, proof):
                raise ValueError('Дата границы периода отсутствует в представленных цитатах.')
        value.update(document_sha256=document['sha256'], source_url=document['source_url'],
                     captured_on=document['captured_on'], completeness_verified=False)
        if 'provision_quote' in value:
            quote = text(value.pop('provision_quote'), 'provision_quote', 10000, 10)
            start = document['text'].find(quote)
            if start < 0 or document['text'].find(quote, start + 1) >= 0:
                raise ValueError('Текст нормы должен встречаться в документе ровно один раз; расширьте цитату для однозначной привязки.')
            value['provision_evidence'] = self._quote(db, principal, matter,
                {'document_id': document['id'], 'quote': quote})
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
        if (any(normalized(value[key]) not in normalized(quote) for key in ('case_number', 'court', 'instance'))
                or not date_quoted(value['decision_date'], quote)):
            raise ValueError('Реквизиты суда, дела и даты должны присутствовать в общей цитате.')
        value['identity'], value['outcome'] = identity, self._quote(db, principal, matter, value['outcome'])
        if value['outcome']['document_id'] != identity['document_id']:
            raise ValueError('Результат должен цитировать сам судебный акт.')
        for field in ('positions', 'links'):
            if not isinstance(value[field], list) or len(value[field]) > 20:
                raise ValueError('Слишком много позиций или связей.')
        positions = []
        seen_positions = set()
        for position in value['positions']:
            if (not isinstance(position, dict) or set(position) != {'side', 'citation', 'applicability_note'}
                    or not isinstance(position['side'], str) or position['side'] not in {'supporting', 'opposing', 'distinguishing'}):
                raise ValueError('Некорректная позиция.')
            citation = self._quote(db, principal, matter, position['citation'])
            if citation['document_id'] != identity['document_id']:
                raise ValueError('Позиция должна цитировать сам судебный акт.')
            note = text(position['applicability_note'], 'applicability_note', 3000)
            signature = (position['side'], citation['quote'], note)
            if signature in seen_positions:
                raise ValueError('Повторяющаяся позиция.')
            seen_positions.add(signature)
            positions.append({'side': position['side'], 'citation': citation,
                              'applicability_note': note,
                              'semantic_comparability_verified': False})
        value['positions'] = positions
        links = []
        seen_links = set()
        for link in value['links']:
            if (not isinstance(link, dict) or set(link) != {'target_id', 'relation', 'citation'}
                    or not isinstance(link['relation'], str) or link['relation'] not in {'appeal', 'upheld', 'reversed', 'remanded'}):
                raise ValueError('Некорректная связь инстанций.')
            target = self._artifact(db, principal, link['target_id'])
            if (target['kind'] != 'decision' or target.get('version', {}).get('superseded_by') or target['matter_id'] != matter
                    or target['payload']['identity']['document_id'] == identity['document_id']
                    or target['payload']['case_number'] != value['case_number']
                    or target['payload']['decision_date'] > value['decision_date']):
                raise ValueError('Связь должна относиться к более раннему акту того же дела.')
            citation = self._quote(db, principal, matter, link['citation'])
            target_date = target['payload']['decision_date']
            if (citation['document_id'] != identity['document_id'] or value['case_number'] not in citation['quote']
                    or not date_quoted(target_date, citation['quote'])):
                raise ValueError('Связь должна цитировать упоминание исходного акта в последующем.')
            signature = (target['id'], link['relation'])
            if signature in seen_links:
                raise ValueError('Повторяющаяся связь.')
            seen_links.add(signature)
            links.append({**link, 'citation': citation, 'literal_connection_checked': True, 'legal_effect_verified': False})
        value['links'] = links
        return value

    def revise_decision(self, principal, identity, positions, links):
        """Append an unreviewed version; the quoted act's identity cannot be changed."""
        with self.store.connect(True) as db:
            previous = self._artifact(db, principal, identity)
            self.store._access(db, principal, previous['matter_id'], ('owner', 'editor'))
            if previous['kind'] != 'decision':
                raise ValueError('Новая версия поддерживается только для судебной карточки.')
            if previous['version']['superseded_by']:
                raise WorkspaceError(409, 'decision_version_changed', 'Карточка уже дополнена. Откройте её последнюю версию.')
            payload = {key: previous['payload'][key] for key in ('case_number', 'court', 'decision_date', 'instance', 'full_text_declared')}
            for key in ('identity', 'outcome'):
                payload[key] = {field: previous['payload'][key][field] for field in ('document_id', 'quote')}
            payload.update(positions=positions, links=links)
            value = self._decision(db, principal, previous['matter_id'], payload)
            if value == previous['payload']:
                raise ValueError('Позиции и связи не изменились.')
            raw = canonical(value); digest = hashlib.sha256(raw.encode()).hexdigest(); new_id = uuid.uuid4().hex
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',
                       (new_id, previous['matter_id'], 'decision', raw, digest, principal, now()))
            db.execute('INSERT INTO decision_revisions VALUES(?,?,?)',
                       (new_id, identity, previous['version']['root_artifact_id']))
            self.store._audit(db, principal, 'decision_revised', new_id, previous['matter_id'])
        return {'artifact_id': new_id, 'previous_artifact_id': identity, 'sha256': digest,
                'review_status': 'draft', 'independent_review_required': True, 'legal_acceptance': False}

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
            if row['kind'] == 'analysis_report':
                raise WorkspaceError(403, 'analysis_report_route_required', 'Сохранённый отчёт доступен через повторную проверку контекста.')
            self.store._audit(db, principal, 'evidence_read', identity, row['matter_id'])
            return row

    def queue(self, principal, matter):
        result = []
        with self.store.connect() as db:
            self.store._access(db, principal, matter)
            for identity in db.execute("SELECT a.id FROM artifacts a WHERE a.matter_id=? AND a.kind IN ('edition','decision','benchmark') AND NOT EXISTS (SELECT 1 FROM decision_revisions r WHERE r.previous_artifact_id=a.id) ORDER BY a.created_on LIMIT 1000", (matter,)):
                row = self._artifact(db, principal, identity[0])
                result.append({'artifact_id': row['id'], 'kind': row['kind'], 'sha256': row['sha256'],
                              'created_by': row['created_by'], 'review_status': row['review_status'],
                              'version': row.get('version'),
                              'decision': {key: row['payload'][key] for key in ('case_number', 'court', 'decision_date', 'instance')} if row['kind'] == 'decision' else None,
                              'edition': {key: row['payload'][key] for key in ('act_id', 'provision', 'valid_from', 'valid_to')} if row['kind'] == 'edition' else None,
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
            if row['kind'] == 'analysis_report':
                raise WorkspaceError(403, 'analysis_report_review_denied', 'Снимок отчёта не является материалом юридической приёмки.')
            if reviewer['role'] != 'reviewer' or row['created_by'] == principal:
                raise WorkspaceError(403, 'independent_reviewer_required', 'Нужен другой пользователь с ролью reviewer.')
            if decision == 'approved' and row['kind'] == 'decision' and row['version']['superseded_by']:
                raise WorkspaceError(409, 'decision_version_changed', 'Для одобрения откройте последнюю версию карточки.')
            if decision == 'approved' and row['kind'] == 'decision':
                for link in row['payload']['links']:
                    target = self._artifact(db, principal, link['target_id'])
                    if target['version']['superseded_by']:
                        raise WorkspaceError(409, 'decision_target_changed', 'Связь ссылается на прежнюю версию другого акта. Обновите карточку и проверьте связь.')
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
            selected = self._select_in_db(db, principal, matter, act_id, provision, as_of)
            self.store._audit(db, principal, 'edition_selected', matter, matter)
        return {**selected,
                'as_of': as_of, 'legal_acceptance': False, 'source_authenticity_automatically_verified': False}

    def _select_in_db(self, db, principal, matter, act_id, provision, as_of):
        match_count, selected = 0, None
        for identity in db.execute("SELECT id FROM artifacts WHERE matter_id=? AND kind='edition'", (matter,)):
            row = self._artifact(db, principal, identity[0])
            payload = row['payload']
            if (row['review_status'] == 'approved' and payload['act_id'] == act_id and payload['provision'] == provision
                    and payload['valid_from'] <= as_of and (payload['valid_to'] is None or as_of < payload['valid_to'])):
                match_count += 1
                selected = row if match_count == 1 else None
        return {'status': 'review_recorded' if match_count == 1 else 'ambiguous' if match_count else 'no_reviewed_edition',
                'edition': selected, 'matching_count': match_count}

    def assert_selection(self, db, principal, matter, as_of, expected):
        for reference in expected:
            selection = self._select_in_db(db, principal, matter, reference['act_id'], reference['provision'], as_of)
            current = selection['edition']
            if (current is None or current['id'] != reference['artifact_id'] or current['sha256'] != reference['sha256']
                    or hashlib.sha256(canonical(current['review']).encode()).hexdigest() != reference['review_sha256']):
                raise WorkspaceError(409, 'edition_review_changed', 'Редакция или её проверка изменились во время анализа. Выполните запрос заново.')

    def timeline(self, principal, matter, case_number, as_of):
        as_of = day(as_of, 'as_of')
        case_number = text(case_number, 'case_number')
        with self.store.connect(True) as db:
            self.store._access(db, principal, matter)
            rows = [self._artifact(db, principal, row[0]) for row in db.execute("SELECT id FROM artifacts WHERE matter_id=? AND kind='decision'", (matter,))]
            matching = [row for row in rows if row['payload']['case_number'] == case_number]
            current = [row for row in matching if not row['version']['superseded_by']]
            selected = sorted((row for row in current if row['payload']['decision_date'] <= as_of), key=lambda row: row['payload']['decision_date'])
            relationships = []
            for row in selected:
                for link in row['payload']['links']:
                    target = self._artifact(db, principal, link['target_id'])
                    relationships.append({'from_id': row['id'], 'target_id': target['id'], 'relation': link['relation'],
                        'citation': link['citation'], 'target_superseded_by': target['version']['superseded_by'],
                        'source_review_status': row['review_status'], 'target_review_status': target['review_status'],
                        'legal_effect_verified': False, 'requires_expert_review': True})
            self.store._audit(db, principal, 'case_timeline', matter, matter)
        return {'decisions': selected, 'relationships': relationships, 'later_decisions_excluded': len(current) - len(selected),
                'superseded_versions_excluded': len(matching) - len(current),
                'complete_history': False, 'legal_acceptance': False, 'as_of': as_of}

    def score(self, principal, identity, answer, correctness, missed_risks, unsupported_claim_count, note, run_label='untagged'):
        answer = text(answer, 'answer', 200000)
        note = text(note, 'note', 5000)
        run_label = text(run_label, 'run_label', 80)
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
                      'required_risk_count': len(required), 'unsupported_claim_count': unsupported_claim_count, 'note': note,
                      'run_label': run_label, 'benchmark_review_sha256': hashlib.sha256(canonical(row['review']).encode()).hexdigest()}
            evaluation_id = uuid.uuid4().hex
            response_hash = hashlib.sha256(answer.encode()).hexdigest()
            if db.execute('SELECT 1 FROM evaluations WHERE artifact_id=? AND response_sha256=? AND reviewer_id=?', (identity, response_hash, principal)).fetchone():
                raise WorkspaceError(409, 'duplicate_evaluation', 'Этот эксперт уже оценил данный ответ по этому эталону.')
            db.execute('INSERT INTO evaluations VALUES(?,?,?,?,?,?,?)', (evaluation_id, identity,
                       response_hash, answer, canonical(scores), principal, now()))
            self.store._audit(db, principal, 'answer_scored', evaluation_id, row['matter_id'])
        return {'evaluation_id': evaluation_id, 'scores': scores, 'response_sha256': response_hash,
                'human_scoring_recorded': True, 'legal_acceptance': False}

    def metrics(self, principal, matter):
        excluded, unbound = 0, 0
        with self.store.connect() as db:
            self.store._access(db, principal, matter)
            rows = []
            for row in db.execute('SELECT e.scores,e.response_sha256,a.id FROM evaluations e JOIN artifacts a ON e.artifact_id=a.id WHERE a.matter_id=?', (matter,)):
                artifact = self._artifact(db, principal, row['id'])
                score = json.loads(row['scores'])
                binding = score.get('benchmark_review_sha256')
                if not binding:
                    unbound += 1
                elif artifact['review_status'] != 'approved' or binding != hashlib.sha256(canonical(artifact['review']).encode()).hexdigest():
                    excluded += 1
                else:
                    rows.append({**score, 'artifact_id': row['id'], 'response_sha256': row['response_sha256']})
        def summarize(values):
            required = sum(row['required_risk_count'] for row in values)
            return {'human_evaluations': len(values), 'distinct_benchmarks': len({row['artifact_id'] for row in values}),
                    'distinct_responses': len({row['response_sha256'] for row in values}),
                    'mean_correctness_0_to_4': sum(row['correctness'] for row in values) / len(values) if values else None,
                    'missed_required_risk_fraction': sum(len(row['missed_risks']) for row in values) / required if required else None,
                    'unsupported_claim_count': sum(row['unsupported_claim_count'] for row in values)}
        labels = sorted({row.get('run_label', 'untagged') for row in rows})
        groups = {label: [row for row in rows if row.get('run_label', 'untagged') == label] for label in labels}
        shared = set.intersection(*[{row['artifact_id'] for row in groups[label]} for label in labels]) if len(labels) > 1 else set()
        return {**summarize(rows), 'excluded_due_to_review_change': excluded,
                'excluded_without_review_binding': unbound,
                'by_run_label': [{'run_label': label, **summarize(groups[label])} for label in labels],
                'shared_benchmark_count': len(shared),
                'shared_benchmark_comparison': [{'run_label': label, **summarize([row for row in groups[label] if row['artifact_id'] in shared])} for label in labels] if shared else [],
                'status': 'human_scores_recorded' if rows else 'no_human_evaluations',
                'reviewer_qualification_verified': False, 'legal_acceptance': False,
                'limitation': 'Оценки вводит человек; привязка ответа к реальному запуску модели и квалификация экспертов автоматически не подтверждены. Метрики усредняются по оценкам, а не по уникальным делам. Сравнивайте версии на общих эталонах; разные наборы задач не доказывают превосходства версии.'}
