"""Opt-in, matter/date-scoped learning from reviewed examples; no weight training."""
import hashlib
import json
import re

from reviewed_evidence import ReviewedEvidence, day, normalized
from workspace_store import WorkspaceError, canonical, text

MAX_CANDIDATES = 100
MAX_EXAMPLES = 3
MAX_EXAMPLE_CHARS = 10000


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def tokens(value):
    return set(re.findall(r'[\w]{3,}', value.casefold()))


class LearningMemory:
    def __init__(self, store):
        self.store, self.evidence = store, ReviewedEvidence(store)

    def _example(self, db, principal, identity, as_of, held_out=None):
        artifact = self.evidence._artifact(db, principal, identity)
        p = artifact['payload']
        from control_evaluation import holdouts
        held_out = holdouts(self.store, db, principal, artifact['matter_id']) if held_out is None else held_out
        if identity in held_out[0] or normalized(p.get('question', '')) in held_out[1]:
            return None
        if artifact['kind'] != 'benchmark' or artifact['review_status'] != 'approved' or p['as_of'] != as_of:
            return None
        review_hash = digest(artifact['review'])
        evaluations = []
        for row in db.execute('SELECT id,scores FROM evaluations WHERE artifact_id=? ORDER BY id LIMIT 101', (identity,)):
            score = json.loads(row['scores'])
            if score.get('benchmark_review_sha256') != review_hash:
                continue
            if score['correctness'] < 4 or score['missed_risks'] or score['unsupported_claim_count']:
                evaluated = db.execute('SELECT * FROM evaluations WHERE id=?', (row['id'],)).fetchone()
                if hashlib.sha256(evaluated['response_text'].encode()).hexdigest() != evaluated['response_sha256']:
                    raise WorkspaceError(503, 'learning_integrity', 'Оценённый ответ не прошёл проверку целостности.')
                evaluations.append(evaluated)
                break
        if not evaluations:
            return None
        for citation in p['citations']:
            document = self.store._document(db, principal, citation['document_id'])
            if (document['matter_id'] != artifact['matter_id'] or document['sha256'] != citation['document_sha256']
                    or document['text'][citation['start']:citation['end']] != citation['quote']):
                raise WorkspaceError(503, 'learning_integrity', 'Источник обучающего примера изменился.')
        evaluated = dict(evaluations[0]); score = json.loads(evaluated['scores'])
        example = {'benchmark_id': identity, 'question': p['question'], 'as_of': as_of,
                   'corrected_answer': p['expected_answer'], 'required_risks': p['required_risks'],
                   'forbidden_claims': p['forbidden_claims'], 'citations': p['citations'],
                   'observed_errors': {'correctness_0_to_4': score['correctness'],
                       'missed_risk_count': len(score['missed_risks']), 'unsupported_claim_count': score['unsupported_claim_count']},
                   'legal_acceptance': False}
        if len(canonical(example)) > MAX_EXAMPLE_CHARS:
            return None
        reference = {'benchmark_id': identity, 'artifact_sha256': artifact['sha256'],
                     'review_sha256': review_hash, 'evaluation_id': evaluated['id'],
                     'evaluation_sha256': digest(evaluated)}
        return example, reference

    def select(self, principal, matter, question, as_of, exclude=None):
        question, as_of = text(question, 'question', 2000), day(as_of, 'as_of')
        exclude = [] if exclude is None else exclude
        if (not isinstance(exclude, list) or len(exclude) > 100
                or any(not isinstance(value, str) or not 1 <= len(value) <= 64 for value in exclude)
                or len(set(exclude)) != len(exclude)):
            raise ValueError('learning_exclude_artifact_ids: до 100 разных идентификаторов эталонов.')
        with self.store.connect(True) as db:
            self.store._access(db, principal, matter)
            from control_evaluation import holdouts
            held_out = holdouts(self.store, db, principal, matter)
            candidates = list(db.execute("SELECT id FROM artifacts WHERE matter_id=? AND kind='benchmark' ORDER BY created_on DESC,id LIMIT ?", (matter, MAX_CANDIDATES + 1)))
            ranked = []
            for row in candidates[:MAX_CANDIDATES]:
                if row['id'] in exclude or row['id'] in held_out[0]:
                    continue
                artifact = self.evidence._artifact(db, principal, row['id'])
                if normalized(artifact['payload']['question']) in held_out[1]:
                    continue
                # Exact-question exclusion is a guard, not proof against paraphrase leakage.
                if normalized(artifact['payload']['question']) == normalized(question):
                    continue
                overlap = len(tokens(question) & tokens(artifact['payload']['question']))
                if not overlap:
                    continue
                value = self._example(db, principal, row['id'], as_of, held_out)
                if value:
                    ranked.append((overlap, row['id'], value))
            ranked.sort(key=lambda item: (-item[0], item[1]))
            selected = [value for _, _, value in ranked[:MAX_EXAMPLES]]
            self.store._audit(db, principal, 'learning_examples_selected', matter, matter)
        return {'enabled': True, 'mode': 'reviewed_examples', 'examples': [value[0] for value in selected],
                'references': [value[1] for value in selected], 'used_count': len(selected),
                'candidate_limit_reached': len(candidates) > MAX_CANDIDATES,
                'automatic_holdout_count': len(held_out[0]), 'automatic_holdout_question_count': len(held_out[1]),
                'weights_updated': False, 'legal_acceptance': False,
                'limitation': 'Опыт ограничен этим делом и той же датой. Сохранённые контрольные наборы и назначенные контрольные задания исключаются автоматически. Для остальных используйте learning_exclude_artifact_ids. Сходство слов не доказывает применимость; точное сравнение не выявляет все перефразирования.'}

    def assert_current(self, db, principal, matter, as_of, references):
        self.store._access(db, principal, matter)
        for reference in references:
            artifact = self.evidence._artifact(db, principal, reference['benchmark_id'])
            if artifact['matter_id'] != matter:
                raise WorkspaceError(403, 'matter_access_denied', 'Опыт другого дела не допускается.')
            current = self._example(db, principal, reference['benchmark_id'], as_of)
            if current is None or current[1] != reference:
                raise WorkspaceError(409, 'learning_review_changed', 'Обучающий пример или его проверка изменились. Повторите анализ.')
