#!/usr/bin/env python3
"""HTTP API для юридического ИИ-помощника по законодательству РФ.

Эндпоинты:
  GET  /health — проверка работоспособности и готовности конфигурации;
  POST /ask    — правовой вопрос → {answer, sources, checked_on, disclaimer};
  POST /review — текст договора → {summary, risks, missing_clauses, disclaimer}.

Запуск: python api.py
Настройки через переменные окружения:
  API_HOST, API_PORT, API_OUTPUT_DIR, API_TOKEN (необязательный Bearer-токен),
  плюс LLM_* и TAVILY_* из agent.py.

Сервер использует только стандартную библиотеку Python. Юридическая логика
и обязательная проверка поправок полностью остаются в agent.py.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent import LEGAL_DISCLAIMER, AgentError, load_dotenv, run
from review import handle_review

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
MAX_BODY_BYTES = 2 * 1024 * 1024
SERVICE_NAME = "legal-research-agent"
API_VERSION = "1.0"


class RequestValidationError(ValueError):
    """Ошибка во входных данных запроса."""


def parse_int_field(payload: dict[str, Any], field: str, default: int, minimum: int, maximum: int) -> int:
    value = payload.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RequestValidationError(f"Поле «{field}» должно быть целым числом от {minimum} до {maximum}.")
    if not minimum <= value <= maximum:
        raise RequestValidationError(f"Поле «{field}» должно быть от {minimum} до {maximum}.")
    return value


def handle_ask(payload: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Выполняет полный цикл агента и возвращает структурированный ответ."""
    question = payload.get("question", "")
    if not isinstance(question, str) or not question.strip():
        raise RequestValidationError("Поле «question» обязательно и не должно быть пустым.")
    query_count = parse_int_field(payload, "queries", 3, 1, 5)
    source_limit = parse_int_field(payload, "sources", 6, 1, 10)
    demo = bool(payload.get("demo", False))

    # Отдельная папка на запрос: имена файлов внутри не пересекаются.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    request_dir = output_dir / f"запрос-{stamp}-{uuid.uuid4().hex[:8]}"
    report_path, journal_path = run(
        topic=question.strip(),
        query_count=query_count,
        source_limit=source_limit,
        output_dir=request_dir,
        demo=demo,
    )
    answer = report_path.read_text(encoding="utf-8")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    return {
        "answer": answer,
        "sources": [
            {"title": source["title"], "url": source["url"]}
            for source in journal["sources"]
        ],
        "checked_on": journal["checked_on"],
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": journal["demo"],
        "report_file": str(report_path),
        "journal_file": str(journal_path),
    }


def health_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": API_VERSION,
        "endpoints": ["GET /health", "POST /ask", "POST /review"],
        "llm_configured": bool(os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL")),
        "tavily_configured": bool(os.getenv("TAVILY_API_KEY")),
        "demo_available": True,
    }


class LegalApiHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVICE_NAME}/{API_VERSION}"
    protocol_version = "HTTP/1.1"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_payload(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or 0)
        except ValueError:
            raise RequestValidationError("Некорректный заголовок Content-Length.") from None
        if length <= 0:
            raise RequestValidationError("Тело запроса пустое: ожидается JSON.")
        if length > MAX_BODY_BYTES:
            raise RequestValidationError(f"Тело запроса больше {MAX_BODY_BYTES} байт.")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RequestValidationError("Тело запроса должно быть корректным JSON.") from None
        if not isinstance(payload, dict):
            raise RequestValidationError("Ожидается JSON-объект.")
        return payload

    def _authorized(self) -> bool:
        token = getattr(self.server, "api_token", "")
        if not token:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {token}"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, health_payload())
        else:
            self._send_json(404, {"error": "Неизвестный путь. Доступны: GET /health, POST /ask, POST /review."})

    def do_POST(self) -> None:
        if self.path not in ("/ask", "/review"):
            self._send_json(404, {"error": "Неизвестный путь. Доступны: GET /health, POST /ask, POST /review."})
            return
        if not self._authorized():
            # Соединение закрываем: непрочитанное тело запроса не должно
            # восприниматься как начало следующего запроса.
            self.close_connection = True
            self._send_json(401, {"error": "Требуется заголовок Authorization: Bearer <API_TOKEN>."})
            return
        try:
            payload = self._read_payload()
            if self.path == "/ask":
                result = handle_ask(payload, Path(self.server.output_dir))
            else:
                result = handle_review(payload)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except AgentError as exc:
            self._send_json(502, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — последняя линия защиты сервиса
            self._send_json(500, {"error": f"Внутренняя ошибка сервиса: {exc}"})
        else:
            self._send_json(200, result)


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    output_dir: Path | str = Path("результаты"),
    api_token: str = "",
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), LegalApiHandler)
    server.output_dir = Path(output_dir)
    server.api_token = api_token.strip()
    return server


def main() -> int:
    load_dotenv()
    host = os.getenv("API_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    try:
        port = int(os.getenv("API_PORT", str(DEFAULT_PORT)))
    except ValueError:
        print("Ошибка: API_PORT должен быть числом.", file=sys.stderr)
        return 1
    output_dir = Path(os.getenv("API_OUTPUT_DIR", "результаты"))
    api_token = os.getenv("API_TOKEN", "")
    server = create_server(host, port, output_dir, api_token)
    auth_note = "включена (API_TOKEN)" if server.api_token else "отключена (задайте API_TOKEN)"
    print(f"HTTP API юридического помощника: http://{host}:{port}")
    print(f"Эндпоинты: GET /health, POST /ask, POST /review. Авторизация: {auth_note}.")
    print(f"Результаты сохраняются в: {server.output_dir.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
