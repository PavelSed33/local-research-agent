"""Private stdin/stdout protocol for the HTTP API's isolated worker."""
import contextlib
import json
from pathlib import Path
import sys


def execute(envelope):
    from local_extract import handle_local_extract
    from legal_skills import handle_skill
    from api import (handle_ask, handle_review, handle_legal_review,
                     handle_case_law, handle_extract, handle_diff, AgentError)
    payload = envelope['payload']
    handlers = {'/review': handle_review, '/review/legal': handle_legal_review,
                '/case-law/check': handle_case_law, '/extract': handle_extract, '/diff': handle_diff,
                '/legal/skills/run': handle_skill, '/extract/local': handle_local_extract}
    try:
        # Provider/debug prints must never corrupt the protocol or expose document text.
        with contextlib.redirect_stdout(sys.stderr):
            if envelope['path'] == '/ask':
                result = handle_ask(payload, Path(envelope['output_dir']),
                                    session_context_override=envelope.get('context', ''))
            else:
                result = handlers[envelope['path']](payload)
        return {'status': 200, 'result': result}
    except ValueError as exc:
        return {'status': 400, 'code': 'invalid_request', 'error': str(exc)}
    except AgentError as exc:
        return {'status': 502, 'code': 'provider_error', 'error': str(exc)}
    except Exception:
        return {'status': 500, 'code': 'worker_failed', 'error': 'Внутренняя ошибка сервиса.'}


if __name__ == '__main__':
    envelope = json.load(sys.stdin)
    print(json.dumps(execute(envelope), ensure_ascii=False))
