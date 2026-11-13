"""Isolated execution of selected HTTP endpoints.

The parent process owns authentication, deadlines and cancellation.
"""
import contextlib
import json
import sys


def execute(envelope):
    from agent import AgentError
    from review import handle_review
    from extract import handle_extract
    from redline import handle_diff
    from local_extract import handle_local_extract
    from legal_skills import handle_skill, SkillRegistryError
    from api import handle_ask
    from pathlib import Path

    handlers = {
        '/review': handle_review,
        '/review/legal': handle_review,
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
