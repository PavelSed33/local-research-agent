"""Offline technical evaluation of supplied answer snapshots, never legal acceptance."""
import hashlib
import json
from pathlib import Path
import re

from agent import Source, validate_as_of, validate_report
from legal_review import validate_legal_bases
from review import validate_review

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 8 * 1024 * 1024
ID = re.compile(r'^[A-Za-z0-9_.:-]{1,80}$')
TRUST_FLAGS = {'verified', 'lawyer_reviewed', 'period_supported', 'legal_acceptance'}


def read_dataset(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('dataset_too_large')
    return json.loads(raw)


def _promoted_flags(value):
    stack, count, promoted = [(value, 0)], 0, 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > 20 or count > 20000:
            raise ValueError('response_structure_limit')
        if isinstance(node, dict):
            promoted += sum(key in TRUST_FLAGS and val is not False for key, val in node.items())
            stack.extend((val, depth + 1) for val in node.values() if isinstance(val, (dict, list)))
        elif isinstance(node, list):
            stack.extend((val, depth + 1) for val in node if isinstance(val, (dict, list)))
    return promoted


def _sources(case, response):
    declared = response.get('sources', [])
    snapshots = case.get('source_materials', response.get('source_materials', []))
    if not isinstance(declared, list) or len(declared) > 16 or not isinstance(snapshots, list) or len(snapshots) > 16:
        return [], 'failed', 'invalid_source_structure'
    if len(snapshots) != len(declared):
        return [], 'blocked', 'source_materials_missing'
    result = []
    for index, (source, snapshot) in enumerate(zip(declared, snapshots), 1):
        if not isinstance(source, dict) or not isinstance(snapshot, dict):
            return [], 'failed', 'invalid_source_structure'
        if (type(snapshot.get('id')) is not int or snapshot['id'] != index
                or not isinstance(source.get('url'), str) or not source['url']
                or snapshot.get('url') != source['url']
                or ('id' in source and (type(source['id']) is not int or source['id'] != index))):
            return [], 'failed', 'source_identity_mismatch'
        text, digest = snapshot.get('text'), snapshot.get('sha256')
        if not isinstance(text, str) or not text.strip() or len(text) > 200000:
            return [], 'blocked', 'source_text_missing_or_unsupported'
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            return [], 'blocked', 'source_fingerprint_missing'
        if hashlib.sha256(text.encode('utf-8')).hexdigest() != digest:
            return [], 'failed', 'source_fingerprint_mismatch'
        title = snapshot.get('title', '')
        if not isinstance(title, str) or len(title) > 1000 or len(source['url']) > 2000:
            return [], 'failed', 'invalid_source_structure'
        if 'title' in source and source['title'] != title:
            return [], 'failed', 'source_identity_mismatch'
        result.append(Source(title, source['url'], text, 'supplied snapshot'))
    return result, 'passed', 'source_material_integrity'


def _literal_checks(case, response):
    """Explicit synthetic expectations; not semantic contradiction detection."""
    rules = case.get('literal_expectations')
    if rules is None:
        return []
    if not isinstance(rules, dict) or set(rules) - {'required', 'forbidden', 'exclusive_groups'}:
        raise ValueError('invalid_literal_expectations')
    def phrases(value):
        if (not isinstance(value, list) or len(value) > 30
                or any(not isinstance(item, str) or not 1 <= len(item.strip()) <= 500 for item in value)):
            raise ValueError('invalid_literal_phrases')
        return [' '.join(item.casefold().split()) for item in value]
    required, forbidden = phrases(rules.get('required', [])), phrases(rules.get('forbidden', []))
    groups = rules.get('exclusive_groups', [])
    if not isinstance(groups, list) or len(groups) > 10:
        raise ValueError('invalid_exclusive_groups')
    groups = [phrases(group) for group in groups]
    if any(len(group) < 2 or len(set(group)) != len(group) for group in groups):
        raise ValueError('invalid_exclusive_groups')
    # Check generated conclusions only, never match instructions in copied sources/documents.
    values = [response.get('answer', ''), response.get('summary', '')]
    for row in response.get('risks', []) if isinstance(response.get('risks', []), list) else []:
        if isinstance(row, dict):
            values.extend(row.get(key, '') for key in ('comment', 'recommendation'))
    text = ' '.join(' '.join(value.casefold().split()) for value in values if isinstance(value, str))
    missing = sum(item not in text for item in required)
    unsupported = sum(item in text for item in forbidden)
    contradictions = sum(sum(item in text for item in group) > 1 for group in groups)
    return [{'code': code, 'status': 'failed' if count else 'passed', 'issue_count': count}
            for code, count in [('required_literal_conclusions', missing),
                                ('forbidden_literal_conclusions', unsupported),
                                ('mutually_exclusive_literal_conclusions', contradictions)]]


def evaluate_responses(dataset, benchmarks_path=ROOT / 'fixtures/legal_benchmarks.json'):
    if not isinstance(dataset, dict) or type(dataset.get('schema_version')) is not int or dataset['schema_version'] != 1:
        raise ValueError('unsupported_dataset_schema')
    cases = dataset.get('cases')
    if not isinstance(cases, list) or not 1 <= len(cases) <= 200:
        raise ValueError('dataset_requires_1_to_200_cases')
    benchmarks = {row['id']: row for row in read_dataset(benchmarks_path)['cases']}
    seen, results = set(), []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get('id'), str) or not ID.fullmatch(case['id']) or case['id'] in seen:
            raise ValueError('invalid_or_duplicate_case_id')
        seen.add(case['id'])
        kind = case.get('kind')
        if not isinstance(kind, str) or kind not in {'ask', 'review', 'legal_review'}:
            raise ValueError('unsupported_response_kind')
        benchmark_id = case.get('benchmark_id')
        if benchmark_id is not None and (not isinstance(benchmark_id, str) or benchmark_id not in benchmarks):
            raise ValueError('unknown_benchmark_id')
        benchmark = benchmarks.get(benchmark_id)
        checks = []
        def check(code, status, issue_count=0):
            checks.append({'code': code, 'status': status, 'issue_count': issue_count})
        response = case.get('response')
        if not isinstance(response, dict):
            check('response_object_required', 'failed', 1)
        else:
            try:
                promotions = _promoted_flags(response)
                check('no_trust_promotion', 'failed' if promotions else 'passed', promotions)
                check('verification_state', 'passed' if response.get('verified') is False and response.get('lawyer_reviewed') is False else 'blocked')
                check('non_demo_response', 'passed' if response.get('demo') is False else 'blocked')
                checks.extend(_literal_checks(case, response))
                if kind != 'ask':
                    check('completed_response', 'passed' if response.get('status') == 'draft' else 'blocked')
                expected_date = case.get('as_of', benchmark['as_of'] if benchmark else None)
                if benchmark and expected_date != benchmark['as_of']:
                    check('benchmark_date_consistency', 'failed', 1)
                if expected_date is not None:
                    if not isinstance(expected_date, str) or validate_as_of(expected_date) != expected_date:
                        raise ValueError('invalid_expected_date')
                    check('requested_date_preserved', 'passed' if response.get('as_of') == expected_date else 'failed')
                sources, source_status, source_code = _sources(case, response) if kind != 'review' else ([], 'passed', 'sources_not_required_for_document_quotes')
                check(source_code, source_status)
                if kind == 'ask':
                    answer = response.get('answer')
                    if not isinstance(answer, str) or not 1 <= len(answer) <= 200000:
                        check('answer_text_required', 'failed', 1)
                    elif source_status == 'passed':
                        issues = validate_report(answer, sources)
                        check('report_structure_and_citation_identity', 'failed' if issues else 'passed', len(issues))
                else:
                    document = case.get('document')
                    if not isinstance(document, str) or not 20 <= len(document) <= 200000:
                        check('original_document_required', 'blocked')
                    elif kind == 'review' or source_status == 'passed':
                        if not isinstance(response.get('risks'), list) or len(response['risks']) > 100:
                            check('bounded_risk_list_required', 'failed', 1)
                        else:
                            issues = (validate_legal_bases(response, document, sources) if kind == 'legal_review'
                                      else validate_review(response, document))
                            check('document_and_legal_quote_fidelity' if kind == 'legal_review' else 'document_quote_fidelity',
                                  'failed' if issues else 'passed', len(issues))
                            if kind == 'review' and any(risk.get('legal_basis') for risk in response['risks'] if isinstance(risk, dict)):
                                check('legal_bases_require_legal_review_kind', 'blocked')
                            if kind == 'legal_review':
                                absent = sum(not risk.get('legal_basis') for risk in response['risks'] if isinstance(risk, dict))
                                check('legal_basis_for_reported_risks', 'blocked' if absent else 'passed', absent)
            except (ValueError, TypeError, KeyError, AttributeError):
                # No excerpts, URLs, model content, exception bodies or document text in reports.
                check('unsupported_or_malformed_response', 'failed', 1)
        status = 'failed' if any(row['status'] == 'failed' for row in checks) else 'blocked' if any(row['status'] == 'blocked' for row in checks) else 'passed'
        results.append({'id': case['id'], 'kind': kind, 'technical_status': status, 'checks': checks,
                        'benchmark_id': benchmark_id,
                        'benchmark_status': 'declared_reviewed' if benchmark and benchmark['review']['approved'] and benchmark.get('approved_legal_answer') else 'draft' if benchmark else 'not_linked',
                        'manual_legal_review_required': True, 'legal_acceptance': False})
    counts = {state: sum(row['technical_status'] == state for row in results) for state in ('passed', 'failed', 'blocked')}
    return {'scope': 'offline_supplied_response_snapshot_checks', 'total': len(results),
            'dataset_synthetic': dataset.get('synthetic') is True,
            'technical_counts': counts, 'technical_status': 'failed' if counts['failed'] else 'blocked' if counts['blocked'] else 'passed',
            'model_execution_verified': False, 'source_authenticity_verified': False,
            'legal_acceptance': False, 'lawyer_reviewed': False,
            'legal_evaluation_status': 'requires_independent_human_review',
            'results': results}


def evaluate_control_suite(dataset):
    """Pass only if every synthetic response receives its declared verdict."""
    if not isinstance(dataset, dict) or dataset.get('synthetic') is not True:
        raise ValueError('control_suite_must_be_synthetic')
    report = evaluate_responses(dataset)
    comparisons = []
    for case, result in zip(dataset['cases'], report['results']):
        expected = case.get('expected_technical_status')
        if not isinstance(expected, str) or expected not in {'passed', 'failed', 'blocked'}:
            raise ValueError('control_suite_requires_expected_status')
        comparisons.append({'id': case['id'], 'expected': expected,
                            'actual': result['technical_status'], 'matched': expected == result['technical_status']})
    return {'scope': 'synthetic_offline_control_suite',
            'technical_status': 'passed' if all(row['matched'] for row in comparisons) else 'failed',
            'total': len(comparisons), 'matched': sum(row['matched'] for row in comparisons),
            'verdict_counts': report['technical_counts'], 'results': comparisons,
            'model_execution_verified': False, 'source_authenticity_verified': False,
            'legal_acceptance': False, 'lawyer_reviewed': False}
