"""Run demo-only acceptance requests against a running local API."""
import argparse
import json
import os
from urllib.parse import urlsplit
import urllib.request
import uuid


def smoke(base_url: str) -> list[str]:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Smoke test is restricted to a local server.')
    token = os.getenv('API_TOKEN', '')
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    passed = []
    req = urllib.request.Request(base_url.rstrip('/') + '/runtime/readiness', headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        ready = json.load(response)
    assert ready['external_services_tested'] is False
    assert ready['legal_acceptance'] is False
    assert any(item['id'] == '/diff' and item['status'] == 'ready_locally' for item in ready['workflows'])
    passed.append('GET /runtime/readiness')
    for path, marker in [('/', b'<!doctype html>'), ('/app.js', b'use strict'), ('/style.css', b':root'),
                         ('/review/options', b'contract_types'),
                         ('/legal/research/options', b'civil-limitation'),
                         ('/legal/skills/options', b'contract-playbook')]:
        with urllib.request.urlopen(base_url.rstrip('/') + path, timeout=10) as response:
            assert marker in response.read(), path
        passed.append('GET ' + path)
    document = 'Поставка товара производится в течение десяти дней после оплаты.'
    cases = [
        ('/legal/skills/run', {'skill_id': 'contract-risk', 'user_position': 'Заказчик',
                             'document': document, 'demo': True}, 'findings'),
        ('/ask', {'question': 'Техническая демонстрация', 'demo': True, 'as_of': '2023-03-01'}, 'answer'),
        ('/review', {'document': document, 'demo': True}, 'risks'),
        ('/review/legal', {'document': document, 'demo': True, 'facts': {'both_businesses': True}}, 'applicability'),
        ('/review/legal', {'document': document, 'contract_type': 'work', 'demo': True,
                           'facts': {'both_businesses': True}}, 'contract_qualification'),
        ('/review/legal', {'document': document, 'contract_type': 'work', 'demo': True,
                           'facts': {'both_businesses': True, 'result_handover': True,
                                     'mixed_contract': False, 'special_regime': False}}, 'contract_qualification'),
        ('/extract', {'document': document, 'demo': True}, 'parties'),
        ('/diff', {'document_a': document, 'document_b': document.replace('десяти', 'пяти')}, 'changes'),
        ('/case-law/check', {'question': 'Тест', 'demo': True}, 'decisions'),
        ('/legal/coverage', {'as_of': '2023-03-01', 'module_ids': ['LAW-01', 'LAW-02']}, 'modules'),
        ('/privacy/check', {'scenario': 'consent'}, 'research_cards'),
        ('/legal/research', {'scenario': 'procedure-costs', 'as_of': '2026-11-08'}, 'research_card_ids'),
        ('/legal/corpus', {'card_ids': ['LAW-02:165.1'], 'include_text': True, 'as_of': '2026-11-08'}, 'entries'),
        ('/sessions/delete', {'session_id': 'smoke-' + uuid.uuid4().hex}, 'deleted'),
    ]
    for path, payload, key in cases:
        request = urllib.request.Request(base_url.rstrip('/') + path,
                                         data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        assert key in result, path
        if path == '/legal/coverage':
            assert result['verified'] is False
            assert not result['evidence_summary']['errors']
        if path == '/privacy/check':
            assert result['status'] == 'needs_facts' and result['verified'] is False
        if path == '/legal/research':
            assert result['status'] == 'needs_facts' and result['verified'] is False
        if path == '/legal/corpus':
            assert result['verified'] is False and not result['errors']
            assert 'Статья 165.1.' in result['entries'][0]['text']
            assert result['entries'][0]['period_supported'] is False
        if path == '/review/legal' and payload.get('contract_type') == 'work':
            expected = 'draft' if payload['facts'].get('result_handover') else 'needs_facts'
            assert result['status'] == expected and result['verified'] is False
        passed.append('POST ' + path)
    return passed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8080')
    print(json.dumps({'passed': smoke(parser.parse_args().base_url),
                      'scope': 'local_demo_and_research_metadata', 'legal_acceptance': False}, ensure_ascii=False, indent=2))

