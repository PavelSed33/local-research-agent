#!/usr/bin/env python3
"""HTTP API для юридического ИИ-помощника по законодательству РФ.

Эндпоинты:
  GET  /health       — проверка работоспособности и готовности конфигурации;
  GET  /openapi.json — машинное описание API (OpenAPI 3.0);
  POST /ask          — правовой вопрос → {answer, sources, checked_on, disclaimer};
  POST /review       — текст договора → {summary, risks, missing_clauses, disclaimer};
  POST /extract      — текст договора → {parties, amounts, dates, penalties, term, jurisdiction};
  POST /diff         — две редакции договора → {summary, changes, diff}.

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
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent import LEGAL_DISCLAIMER, AgentError, load_dotenv, run
from extract import handle_extract
from redline import handle_diff
from review import handle_review

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
MAX_BODY_BYTES = 8 * 1024 * 1024
SERVICE_NAME = "legal-research-agent"
API_VERSION = "1.2"
SESSION_TTL_SECONDS = 3600
SESSION_MAX_ENTRIES = 10
SESSION_ANSWER_CHARS = 500
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class SessionStore:
    """Краткосрочная память сессий в оперативной памяти (без БД, с TTL)."""

    def __init__(self, ttl_seconds: int = SESSION_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _purge_expired(self, now: float) -> None:
        expired = [sid for sid, data in self._sessions.items() if now - data["updated"] > self.ttl_seconds]
        for sid in expired:
            del self._sessions[sid]

    def context(self, session_id: str) -> str:
        """Возвращает текстовый контекст предыдущих вопросов сессии."""
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            entries = list(self._sessions.get(session_id, {}).get("entries", []))
        lines = [f"Вопрос: {item['question']}\nКраткий ответ: {item['answer']}" for item in entries]
        return "\n---\n".join(lines)

    def remember(self, session_id: str, question: str, answer: str) -> None:
        """Запоминает пару вопрос/ответ (ответ усечённо) в сессии."""
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            session = self._sessions.setdefault(session_id, {"updated": now, "entries": []})
            session["updated"] = now
            session["entries"].append({"question": question.strip(), "answer": answer[:SESSION_ANSWER_CHARS]})
            del session["entries"][:-SESSION_MAX_ENTRIES]


class RequestValidationError(ValueError):
    """Ошибка во входных данных запроса."""


def parse_int_field(payload: dict[str, Any], field: str, default: int, minimum: int, maximum: int) -> int:
    value = payload.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RequestValidationError(f"Поле «{field}» должно быть целым числом от {minimum} до {maximum}.")
    if not minimum <= value <= maximum:
        raise RequestValidationError(f"Поле «{field}» должно быть от {minimum} до {maximum}.")
    return value


def handle_ask(payload: dict[str, Any], output_dir: Path, sessions: SessionStore | None = None) -> dict[str, Any]:
    """Выполняет полный цикл агента и возвращает структурированный ответ."""
    question = payload.get("question", "")
    if not isinstance(question, str) or not question.strip():
        raise RequestValidationError("Поле «question» обязательно и не должно быть пустым.")
    session_id = payload.get("session_id")
    if session_id is not None and (not isinstance(session_id, str) or not SESSION_ID_PATTERN.match(session_id)):
        raise RequestValidationError("Поле «session_id» должно быть строкой из 1–64 символов: латиница, цифры, дефис.")
    query_count = parse_int_field(payload, "queries", 3, 1, 5)
    source_limit = parse_int_field(payload, "sources", 6, 1, 10)
    demo = bool(payload.get("demo", False))
    context = sessions.context(session_id) if sessions and session_id else ""

    # Отдельная папка на запрос: имена файлов внутри не пересекаются.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    request_dir = output_dir / f"запрос-{stamp}-{uuid.uuid4().hex[:8]}"
    report_path, journal_path = run(
        topic=question.strip(),
        query_count=query_count,
        source_limit=source_limit,
        output_dir=request_dir,
        demo=demo,
        session_context=context,
    )
    answer = report_path.read_text(encoding="utf-8")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if sessions and session_id:
        sessions.remember(session_id, question, answer)
    return {
        "answer": answer,
        "sources": [
            {"title": source["title"], "url": source["url"]}
            for source in journal["sources"]
        ],
        "checked_on": journal["checked_on"],
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": journal["demo"],
        "session_id": session_id,
        "report_file": str(report_path),
        "journal_file": str(journal_path),
    }


def health_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": API_VERSION,
        "endpoints": ["GET /health", "GET /openapi.json", "POST /ask", "POST /review", "POST /extract", "POST /diff"],
        "llm_configured": bool(os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL")),
        "tavily_configured": bool(os.getenv("TAVILY_API_KEY")),
        "demo_available": True,
    }


def openapi_spec() -> dict[str, Any]:
    """Машинное описание API (OpenAPI 3.0) для интеграции с оркестраторами."""
    document_property = {
        "document": {"type": "string", "description": "Полный текст договора (UTF-8)"},
        "document_base64": {"type": "string", "description": "Договор в формате DOCX, закодированный в base64"},
        "demo": {"type": "boolean", "default": False, "description": "Демонстрационный режим без внешних запросов"},
    }
    return {
        "openapi": "3.0.3",
        "info": {
            "title": SERVICE_NAME,
            "version": API_VERSION,
            "description": "Локальный юридический ИИ-помощник по законодательству РФ.",
        },
        "paths": {
            "/health": {
                "get": {
                    "summary": "Проверка работоспособности и готовности конфигурации",
                    "responses": {"200": {"description": "Сервис работает"}},
                }
            },
            "/ask": {
                "post": {
                    "summary": "Правовой вопрос → ответ со ссылками на источники",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["question"],
                                    "properties": {
                                        "question": {"type": "string"},
                                        "queries": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
                                        "sources": {"type": "integer", "minimum": 1, "maximum": 10, "default": 6},
                                        "session_id": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,64}$"},
                                        "demo": {"type": "boolean", "default": False},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Ответ со ссылками на источники"},
                        "400": {"description": "Ошибка валидации запроса"},
                        "401": {"description": "Требуется Bearer-токен"},
                        "502": {"description": "Ошибка внешнего сервиса (LLM/поиск)"},
                    },
                }
            },
            "/review": {
                "post": {
                    "summary": "Проверка договора → риски с дословными цитатами",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object", "properties": document_property}}},
                    },
                    "responses": {
                        "200": {"description": "Результат проверки (status: draft)"},
                        "400": {"description": "Ошибка валидации запроса"},
                        "401": {"description": "Требуется Bearer-токен"},
                        "502": {"description": "Ошибка внешнего сервиса (LLM)"},
                    },
                }
            },
            "/extract": {
                "post": {
                    "summary": "Извлечение реквизитов договора → структурированный JSON",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object", "properties": document_property}}},
                    },
                    "responses": {
                        "200": {"description": "Извлечённые сущности (status: draft)"},
                        "400": {"description": "Ошибка валидации запроса"},
                        "401": {"description": "Требуется Bearer-токен"},
                        "502": {"description": "Ошибка внешнего сервиса (LLM)"},
                    },
                }
            },
            "/diff": {
                "post": {
                    "summary": "Сравнение двух редакций договора (детерминированно, без LLM)",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "document_a": {"type": "string", "description": "Старая редакция (текст)"},
                                        "document_a_base64": {"type": "string", "description": "Старая редакция (DOCX в base64)"},
                                        "document_b": {"type": "string", "description": "Новая редакция (текст)"},
                                        "document_b_base64": {"type": "string", "description": "Новая редакция (DOCX в base64)"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Список изменений и unified diff (status: draft)"},
                        "400": {"description": "Ошибка валидации запроса"},
                        "401": {"description": "Требуется Bearer-токен"},
                    },
                }
            },
        },
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
        elif self.path == "/openapi.json":
            self._send_json(200, openapi_spec())
        else:
            self._send_json(404, {"error": "Неизвестный путь. Доступны: GET /health, GET /openapi.json, POST /ask, POST /review, POST /extract, POST /diff."})

    def do_POST(self) -> None:
        if self.path not in ("/ask", "/review", "/extract", "/diff"):
            self._send_json(404, {"error": "Неизвестный путь. Доступны: GET /health, GET /openapi.json, POST /ask, POST /review, POST /extract, POST /diff."})
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
                result = handle_ask(payload, Path(self.server.output_dir), self.server.sessions)
            elif self.path == "/review":
                result = handle_review(payload)
            elif self.path == "/extract":
                result = handle_extract(payload)
            else:
                result = handle_diff(payload)
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
    server.sessions = SessionStore()
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
    print(f"Эндпоинты: GET /health, GET /openapi.json, POST /ask, POST /review, POST /extract, POST /diff. Авторизация: {auth_note}.")
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
