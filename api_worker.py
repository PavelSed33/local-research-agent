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

    handlers = {
        '/review': handle_review,
        '/extract': handle_extract,
        '/diff': handle_diff,
    }
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = handlers[envelope['path']](envelope['payload'])
        return {'status': 200, 'result': result}
    except (ValueError, TypeError) as exc:
        return {'status': 400, 'code': 'invalid_request', 'error': str(exc)}
    except AgentError:
        return {'status': 502, 'code': 'provider_error', 'error': 'Ошибка поставщика данных.'}
    except Exception:
        return {'status': 500, 'code': 'worker_failed', 'error': 'Внутренняя ошибка сервиса.'}


if __name__ == '__main__':
    envelope = json.load(sys.stdin)
    print(json.dumps(execute(envelope), ensure_ascii=False))
