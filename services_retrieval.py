"""Bounded local retrieval with related search terms, not legal equivalence or embeddings."""
import re

# Terms broaden document discovery. They must never decide classification or liability.
GROUPS = (
    (r'\b(?:цен\w*|стоимост\w*|тариф\w*)\b', ('цена', 'стоимость', 'тариф')),
    (r'\b(?:расторг\w*|расторж\w*|отказ\w*|прекращ\w*|прекрат\w*)\b', ('расторжение', 'отказ', 'прекращение')),
    (r'\b(?:предоплат\w*|аванс\w*)\b', ('предоплата', 'аванс')),
    (r'\b(?:неусто\w*|штраф\w*|пеня|пени|пеней)\b', ('неустойка', 'штраф', 'пени')),
    (r'\b(?:уведом\w*|извещ\w*)\b', ('уведомление', 'извещение')),
    (r'\b(?:при[её]м\w*|акт|акта|актом|акты|актов)\b', ('приёмка', 'приемка', 'акт')),
    (r'\b(?:плат[её]ж\w*|оплат\w*|расч[её]т\w*)\b', ('платеж', 'платёж', 'оплата', 'расчет', 'расчёт')),
    (r'\b(?:дедлайн\w*|срок\w*)\b', ('срок', 'период')),
)


def related_terms(query):
    terms = []
    for pattern, values in GROUPS:
        if re.search(pattern, query.casefold()):
            for term in values:
                if term not in terms:
                    terms.append(term)
    return terms[:12]


def retrieve(store, principal, matter, question, as_of):
    """Fuse at most two bounded FTS searches, then prefer distinct documents."""
    terms = related_terms(question)
    queries = [(question, 2)] + [(' '.join(terms), 1)] if terms else [(question, 2)]
    candidates, scores, future = {}, {}, set()
    cap_reached = False
    for query, weight in queries:
        result = store.search(principal, matter, query, 12, diverse_documents=True)
        cap_reached |= result['candidate_limit_reached']
        for rank, row in enumerate(result['matches'], 1):
            key = (row['document_id'], row['start'])
            date = row.get('source_context', {}).get('decision_date')
            if date and date > as_of:
                future.add(key)
                continue
            candidates[key] = row
            scores[key] = scores.get(key, 0) + weight / (60 + rank)
    ranked = sorted(candidates, key=lambda key: (-scores[key], key))
    unique, duplicate, seen = [], [], set()
    for key in ranked:
        row = candidates[key]
        (duplicate if row['document_id'] in seen else unique).append(row)
        seen.add(row['document_id'])
    matches = (unique + duplicate)[:8]
    for index, row in enumerate(matches, 1):
        row['source_id'] = index
    return {'matches': matches, 'future_candidates_excluded': len(future),
            'retrieval': {'mode': 'local_fts5_related_terms_rank_fusion', 'semantic_search': False,
                          'related_search_terms': terms, 'distinct_documents': len({row['document_id'] for row in matches}),
                          'candidate_limit_reached': cap_reached, 'complete_search': False,
                          'limitation': 'Связанные поисковые термины не означают юридическую равнозначность понятий. Поиск ограничен сохранёнными документами и кандидатами.'}}
