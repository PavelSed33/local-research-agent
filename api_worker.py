"""Private stdin/stdout protocol for the HTTP API's isolated worker."""
import contextlib
import json
from pathlib import Path
import sys


def execute(envelope):
    from check import handle_check
    from draft import handle_draft
    from duty import handle_duty
    from export import handle_export
    from penalty import handle_penalty
    from protocol import handle_protocol
    from scoring import handle_scoring
    from timeline import handle_timeline
    from triage import handle_triage
    from local_extract import handle_local_extract
    from legal_skills import handle_skill, SkillRegistryError
    from document_tools import parse_file, redact, export_docx
    from docx_redline import redline_docx
    from matter_research import answer as matter_answer
    from api import (handle_ask, handle_review, handle_legal_review,
                     handle_case_law, handle_extract, handle_diff, AgentError)
    payload = envelope['payload']
    path = envelope['path']
    handlers = {'/review': handle_review, '/review/legal': handle_legal_review,
                '/case-law/check': handle_case_law, '/extract': handle_extract, '/diff': handle_diff,
                '/legal/skills/run': handle_skill, '/extract/local': handle_local_extract,
                '/documents/parse': parse_file, '/documents/redact': redact,
                '/documents/export': export_docx, '/documents/redline': redline_docx, '/matter/answer': matter_answer,
                '/check': handle_check, '/draft': handle_draft, '/duty': handle_duty,
                '/export': handle_export, '/penalty': handle_penalty, '/protocol': handle_protocol,
                '/scoring': handle_scoring, '/timeline': handle_timeline, '/triage': handle_triage}
    try:
        # Provider/debug prints must never corrupt the protocol or expose document text.
        with contextlib.redirect_stdout(sys.stderr):
            if path == '/ask':
                result = handle_ask(payload, Path(envelope['output_dir']),
                                    session_context_override=envelope.get('context', ''))
            else:
                result = handlers[path](payload)
        return {'status': 200, 'result': result}
    except SkillRegistryError:
        return {'status': 503, 'code': 'skill_registry_unavailable', 'error': 'Реестр навыков недоступен.'}
    except ValueError as exc:
        return {'status': 400, 'code': 'invalid_request', 'error': str(exc)}
    except AgentError as exc:
        return {'status': 502, 'code': 'provider_error', 'error': str(exc)}
    except Exception:
        return {'status': 500, 'code': 'worker_failed', 'error': 'Внутренняя ошибка сервиса.'}


if __name__ == '__main__':
    envelope = json.load(sys.stdin)
    print(json.dumps(execute(envelope), ensure_ascii=False))
