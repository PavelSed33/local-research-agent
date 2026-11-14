"""Offline data relationships and evidence gaps; no automatic legal acceptance."""
from collections import defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path

from statute_corpus import corpus_report

ROOT = Path(__file__).resolve().parent


def audit_data(registry, workflows, benchmarks, corpus, *, checked_on):
    today = date.fromisoformat(checked_on)
    findings = []
    def issue(code, identity, severity='error'):
        findings.append({'code': code, 'id': identity, 'severity': severity})
    maps = {}
    for name in ('modules', 'acts', 'cards', 'sources'):
        rows = registry[name]
        maps[name] = {row['id']: row for row in rows}
        if len(maps[name]) != len(rows):
            issue('duplicate_' + name, name)
    modules, acts, cards, sources = (maps[key] for key in ('modules', 'acts', 'cards', 'sources'))
    for act in acts.values():
        if act['module_id'] not in modules or not set(act['source_ids']) <= sources.keys():
            issue('invalid_act_references', act['id'])
        if len(set(act['provisions'])) != len(act['provisions']):
            issue('duplicate_act_provisions', act['id'])
        actual = {row['provision'] for row in cards.values() if row['act_id'] == act['id']}
        if actual != set(act['provisions']):
            issue('act_card_coverage_mismatch', act['id'])
        if not act.get('original_publication_received'):
            issue('original_publication_missing', act['id'], 'gap')
        if not act.get('history_complete'):
            issue('amendment_history_incomplete', act['id'], 'gap')
    for card in cards.values():
        identity = card['id']
        act = acts.get(card['act_id'])
        if (act is None or card['module_id'] != act['module_id']
                or card['provision'] not in act['provisions'] or not set(card['source_ids']) <= sources.keys()):
            issue('invalid_card_references', identity)
        dates = {}
        for key in ('effective_from', 'effective_to', 'edition_on'):
            value = card.get(key)
            if value is not None:
                try:
                    dates[key] = date.fromisoformat(value)
                except (TypeError, ValueError):
                    issue('invalid_' + key, identity)
        if 'effective_from' in dates and 'effective_to' in dates and dates['effective_to'] < dates['effective_from']:
            issue('reversed_effective_interval', identity)
        if not card.get('legal_review', {}).get('approved'):
            issue('independent_legal_review_missing', identity, 'gap')
    for name, rows, field in [('workflows', workflows['workflows'], 'card_ids'),
                               ('benchmarks', benchmarks['cases'], 'source_card_ids')]:
        if len({row['id'] for row in rows}) != len(rows):
            issue('duplicate_' + name, name)
        for row in rows:
            if not set(row[field]) <= cards.keys():
                issue('unknown_card_reference', row['id'])
    entries = corpus.get('entries', [])
    received = {row['card_id'] for row in entries}
    if len(received) != len(entries):
        issue('duplicate_corpus_cards', 'corpus')
    # A deliberately removed, quarantined corpus is an acknowledged evidence
    # gap, not corruption. Partial imports and unexpected missing copies remain
    # structural errors so broken capture pipelines cannot appear healthy.
    quarantined_empty = (
        not entries and not corpus.get('errors')
        and any(row.get('status') == 'quarantined_and_removed'
                for row in corpus.get('refresh_gaps', []))
    )
    for identity in sorted(cards.keys() - received):
        issue('missing_corpus_extract', identity, 'gap' if quarantined_empty else 'error')
    if corpus.get('errors'):
        issue('corpus_integrity_failed', 'corpus')
    texts = defaultdict(list)
    for row in entries:
        identity = row['card_id']
        if identity not in cards:
            issue('unknown_corpus_card', identity)
        try:
            if date.fromisoformat(row['captured_on']) > today:
                issue('capture_after_audit_date', identity)
        except (TypeError, ValueError):
            issue('invalid_capture_date', identity)
        if not row.get('period_supported'):
            issue('period_applicability_unconfirmed', identity, 'gap')
        if isinstance(row.get('text'), str):
            normalized = ' '.join(row['text'].split())
            texts[hashlib.sha256(normalized.encode()).hexdigest()].append(identity)
    for identities in texts.values():
        if len(identities) > 1:
            for identity in identities:
                issue('duplicate_normalized_extract', identity, 'warning')
    counts = {severity: sum(row['severity'] == severity for row in findings) for severity in ('error', 'warning', 'gap')}
    return {'scope': 'offline_relationships_integrity_and_evidence_gaps', 'checked_on': checked_on,
            'status': 'failed' if counts['error'] else 'passed_with_gaps' if counts['gap'] or counts['warning'] else 'passed',
            'counts': counts, 'findings': findings,
            'coverage': {name: len(rows) for name, rows in maps.items()},
            'legal_acceptance': False, 'external_requests_attempted': False}


def audit_repository(root=ROOT, checked_on=None):
    root = Path(root)
    def read(name):
        return json.loads((root / 'fixtures' / name).read_text(encoding='utf-8'))
    return audit_data(read('legal_coverage.json'), read('legal_workflows.json'), read('legal_benchmarks.json'),
                      corpus_report(include_text=True, corpus_dir=root / 'fixtures/statute_corpus'),
                      checked_on=checked_on or date.today().isoformat())
