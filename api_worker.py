"""Isolated execution of selected HTTP endpoints.

The parent process owns authentication, deadlines and cancellation.
"""
import contextlib
import json
import sys


def execute(envelope):
    from agent import AgentError
    from review import handle_review
    from legal_research import handle_legal_research
    from statute_corpus import handle_corpus
    from case_law import handle_case_law
    from privacy_review import handle_privacy_check
    from legal_coverage import coverage_report
    from legal_review import handle_legal_review
    from extract import handle_extract
    from redline import handle_diff
    from local_extract import handle_local_extract
    from legal_skills import handle_skill, SkillRegistryError
    from api import handle_ask
    from pathlib import Path

    def handle_coverage(payload):
        allowed = {'module_ids', 'card_ids', 'as_of'}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError('Допустимы только module_ids, card_ids и as_of.')
        return coverage_report(payload.get('module_ids'), payload.get('as_of'),
                               card_ids=payload.get('card_ids'))

    handlers = {
        '/review': handle_review,
        '/legal/research': handle_legal_research,
        '/legal/corpus': handle_corpus,
        '/case-law/check': handle_case_law,
        '/privacy/check': handle_privacy_check,
        '/legal/coverage': handle_coverage,
        '/review/legal': handle_legal_review,
        '/extract': handle_extract,
        '/diff': handle_diff,
        '/extract/local': handle_local_extract,
        '/legal/skills/run': handle_skill,
    }
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if envelope['path'] == '/documents/redline':
                from docx_redline import redline_docx
                result = redline_docx(envelope['payload'])
            elif envelope['path'] == '/workspace/ask':
                prepared = envelope['payload']
                sources = prepared['sources']
                findings = []
                for source in sources:
                    quote = source['text'][:min(240, len(source['text']))]
                    findings.append({'summary': 'Фрагмент документа (демонстрационный режим, без юридической оценки).',
                                     'citations': [{'document_id': source['document_id'],
                                                    'start': source['start'],
                                                    'end': source['start'] + len(quote),
                                                    'quote': quote}]})
                result = {'sources': sources, 'findings': findings,
                          'answer': 'Демонстрационный поиск по материалам дела; выводы требуют проверки юристом.',
                          'legal_acceptance': False, 'demo': True}
            elif envelope['path'] == '/ask':
                result = handle_ask(envelope['payload'], Path(envelope['output_dir']), persist_results=envelope.get('persist_results', False),
                                    session_context_override=envelope.get('context', ''))
            else:
                result = handlers[envelope['path']](envelope['payload'])
        return {'status': 200, 'result': result}
    except SkillRegistryError:
        return {'status': 503, 'code': 'skill_registry_unavailable', 'error': 'Реестр навыков недоступен.'}
    except (ValueError, TypeError) as exc:
        return {'status': 400, 'code': 'invalid_request', 'error': str(exc)}
    except AgentError:
        return {'status': 502, 'code': 'provider_error', 'error': 'Ошибка поставщика данных.'}
    except Exception:
        return {'status': 500, 'code': 'worker_failed', 'error': 'Внутренняя ошибка сервиса.'}


if __name__ == '__main__':
    envelope = json.load(sys.stdin)
    print(json.dumps(execute(envelope), ensure_ascii=False))
