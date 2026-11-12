#!/usr/bin/env python3
"""HTTP API для юридического ИИ-помощника по законодательству РФ.

Эндпоинты:
  GET  /health       — проверка работоспособности и готовности конфигурации;
  GET  /openapi.json — машинное описание API (OpenAPI 3.0);
  POST /ask          — правовой вопрос → {answer, sources, checked_on, disclaimer};
  POST /review       — текст договора → {summary, risks, missing_clauses, disclaimer};
  POST /extract      — текст договора → {parties, amounts, dates, penalties, term, jurisdiction};
  POST /diff         — две редакции договора → {summary, changes, diff};
  POST /penalty      — расчёт неустойки (договорной или по ст. 395 ГК РФ, кодом, без LLM);
  POST /check        — проверка контрагента по ИНН (ЕГРЮЛ ФНС, признаки риска);
  POST /timeline     — хронология дела из переписки и первички;
  POST /draft        — проект документа (претензия, ответ, допсоглашение, уведомление);
  POST /triage       — маршрутизация обращения: категория, срочность, риск, эндпоинт;
  POST /duty         — расчёт госпошлины (ст. 333.19 и 333.21 НК РФ, кодом, без LLM);
  POST /export       — экспорт Markdown-текста результата в DOCX (base64);
  POST /protocol     — протокол разногласий по двум редакциям (таблица для переговоров);
  POST /scoring      — сводная оценка риска договора 0–100 (веса считает код).

Запуск: python api.py
Настройки через переменные окружения:
  API_HOST, API_PORT, API_OUTPUT_DIR, API_TOKEN (обязателен вне localhost),
  плюс LLM_* и TAVILY_* из agent.py.

Сервер использует только стандартную библиотеку Python. Юридическая логика
и обязательная проверка поправок полностью остаются в agent.py.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent import LEGAL_DISCLAIMER, AgentError, load_dotenv, run, validate_as_of
from check import handle_check
from draft import handle_draft
from duty import handle_duty
from export import handle_export
from extract import handle_extract
from penalty import handle_penalty
from protocol import handle_protocol
from redline import handle_diff
from scoring import handle_scoring
from timeline import handle_timeline
from triage import handle_triage
from review import handle_review
from request_jobs import RequestJobs, JobError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
MAX_BODY_BYTES = 8 * 1024 * 1024
SERVICE_NAME = "legal-research-agent"
API_VERSION = "1.5"
SESSION_TTL_SECONDS = 3600
SESSION_MAX_COUNT = 1000
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

    def exists(self, session_id: str) -> bool:
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            return session_id in self._sessions

    def begin(self, session_id: str) -> tuple[str, str]:
        """Return prior context and a generation token for a safe in-flight update."""
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            if session_id not in self._sessions:
                if len(self._sessions) >= SESSION_MAX_COUNT:
                    oldest = min(self._sessions, key=lambda sid: self._sessions[sid]["updated"])
                    del self._sessions[oldest]
                self._sessions[session_id] = {
                    "updated": now, "entries": [], "identity": secrets.token_hex(16),
                }
            session = self._sessions[session_id]
            session.setdefault("identity", secrets.token_hex(16))
            entries = list(session["entries"])
            identity = session["identity"]
        context = "\n---\n".join(
            f"Вопрос: {item['question']}\nКраткий ответ: {item['answer']}" for item in entries
        )
        return context, identity

    def delete(self, session_id: str) -> bool:
        """Delete a session; any in-flight generation token becomes invalid."""
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            return self._sessions.pop(session_id, None) is not None

    def remember(self, session_id: str, question: str, answer: str, expected_session: str | None = None) -> None:
        """Запоминает пару вопрос/ответ (ответ усечённо) в сессии."""
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            if expected_session is not None:
                current = self._sessions.get(session_id)
                if current is None or current.get("identity") != expected_session:
                    return
            if session_id not in self._sessions and len(self._sessions) >= SESSION_MAX_COUNT:
                oldest = min(self._sessions, key=lambda sid: self._sessions[sid]["updated"])
                del self._sessions[oldest]
            session = self._sessions.setdefault(session_id, {"updated": now, "entries": [], "identity": secrets.token_hex(16)})
            session["updated"] = now
            session["entries"].append({"question": question.strip(), "answer": answer[:SESSION_ANSWER_CHARS]})
            del session["entries"][:-SESSION_MAX_ENTRIES]


class Metrics:
    """Счётчики запросов в оперативной памяти: наблюдаемость без внешних систем."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = time.time()
        self._requests: dict[str, dict[str, Any]] = {}

    def record(self, path: str, status: int, duration_ms: float) -> None:
        """Запоминает запрос: путь, код ответа, длительность."""
        with self._lock:
            entry = self._requests.setdefault(path, {"count": 0, "errors": 0, "total_ms": 0.0})
            entry["count"] += 1
            if status >= 400:
                entry["errors"] += 1
            entry["total_ms"] += duration_ms

    def snapshot(self) -> dict[str, Any]:
        """Текущие показатели: аптайм и статистика по эндпоинтам."""
        with self._lock:
            return {
                "uptime_seconds": int(time.time() - self._started),
                "requests": {
                    path: {
                        "count": entry["count"],
                        "errors": entry["errors"],
                        "avg_ms": round(entry["total_ms"] / entry["count"], 1),
                    }
                    for path, entry in sorted(self._requests.items())
                },
            }


class RequestValidationError(ValueError):
    """Ошибка во входных данных запроса."""


def parse_int_field(payload: dict[str, Any], field: str, default: int, minimum: int, maximum: int) -> int:
    value = payload.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RequestValidationError(f"Поле «{field}» должно быть целым числом от {minimum} до {maximum}.")
    if not minimum <= value <= maximum:
        raise RequestValidationError(f"Поле «{field}» должно быть от {minimum} до {maximum}.")
    return value


def handle_ask(payload: dict[str, Any], output_dir: Path, sessions: SessionStore | None = None, persist_results: bool = False, session_context_override: str = "") -> dict[str, Any]:
    """Выполняет полный цикл агента и возвращает структурированный ответ."""
    question = payload.get("question", "")
    if not isinstance(question, str) or not question.strip():
        raise RequestValidationError("Поле «question» обязательно и не должно быть пустым.")
    if len(question) > 8000:
        raise RequestValidationError("Поле «question» не должно превышать 8000 символов.")
    session_id = payload.get("session_id")
    if session_id is not None and (not isinstance(session_id, str) or not SESSION_ID_PATTERN.match(session_id)):
        raise RequestValidationError("Поле «session_id» должно быть строкой из 1–64 символов: латиница, цифры, дефис.")
    query_count = parse_int_field(payload, "queries", 3, 1, 5)
    source_limit = parse_int_field(payload, "sources", 6, 1, 10)
    if type(payload.get("include_source_materials", False)) is not bool:
        raise RequestValidationError("Поле include_source_materials должно быть true или false.")
    demo = payload.get("demo", False)
    if not isinstance(demo, bool):
        raise RequestValidationError("Поле demo должно быть true или false.")
    try:
        as_of = validate_as_of(payload.get("as_of"))
    except ValueError as exc:
        raise RequestValidationError(str(exc)) from exc
    if sessions and session_id and not sessions.exists(session_id):
        raise RequestValidationError("Неизвестный или истёкший session_id.")
    if sessions and session_id is None:
        session_id = secrets.token_hex(24)
    context, session_identity = sessions.begin(session_id) if sessions and session_id else (session_context_override, None)

    if not isinstance(persist_results, bool):
        raise RequestValidationError("persist_results должен быть логическим значением.")
    # По умолчанию данные дела удаляются сразу после формирования ответа.
    # Явное сохранение допускается только для доверенного локального вызова.
    def execute(request_dir: Path) -> tuple[str, dict[str, Any], Path, Path]:
        report_path, journal_path = run(
            topic=question.strip(),
            query_count=query_count,
            source_limit=source_limit,
            output_dir=request_dir,
            demo=demo,
            session_context=context,
            as_of=as_of,
        )
        return (
            report_path.read_text(encoding="utf-8"),
            json.loads(journal_path.read_text(encoding="utf-8")),
            report_path,
            journal_path,
        )

    if persist_results:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        request_dir = output_dir / f"запрос-{stamp}-{uuid.uuid4().hex[:8]}"
        answer, journal, report_path, journal_path = execute(request_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="legal-agent-ask-") as temporary:
            answer, journal, _, _ = execute(Path(temporary))
        report_path = journal_path = None

    if sessions and session_id:
        sessions.remember(session_id, question, answer, expected_session=session_identity)
    return {
        "answer": answer,
        "sources": [
            {"title": source["title"], "url": source["url"]}
            for source in journal["sources"]
        ],
        "checked_on": journal["checked_on"],
        "as_of": journal["as_of"],
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": journal["demo"],
        "session_id": session_id,
        "results_persisted": persist_results,
        "report_file": str(report_path) if report_path else None,
        "journal_file": str(journal_path) if journal_path else None,
    }


def health_payload(metrics: Metrics | None = None) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": API_VERSION,
        "endpoints": ["GET /health", "GET /openapi.json", "POST /ask", "POST /review", "POST /extract", "POST /diff", "POST /penalty", "POST /check", "POST /timeline", "POST /draft", "POST /triage", "POST /duty", "POST /export", "POST /protocol", "POST /scoring"],
        "llm_configured": bool(os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL")),
        "tavily_configured": bool(os.getenv("TAVILY_API_KEY")),
        "demo_available": True,
        "metrics": metrics.snapshot() if metrics else None,
    }


def openapi_spec(auth_required: bool = False) -> dict[str, Any]:
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
        "components": {"securitySchemes": {
            "ApiToken": {"type": "http", "scheme": "bearer"},
        }},
        "security": [{"ApiToken": []}] if auth_required else [],
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
                                        "session_id": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,64}$", "description": "Необязательный секретный идентификатор существующей сессии. При первом запросе не передавайте: сервер выдаст случайный session_id в ответе. Неизвестный или истёкший ID возвращает HTTP 400."},
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
            "/timeline": {
                "post": {
                    "summary": "Хронология дела из материалов (переписка, первичка)",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["materials"],
                                    "properties": {
                                        "materials": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "required": ["text"],
                                                "properties": {
                                                    "name": {"type": "string", "description": "Имя материала (файла)"},
                                                    "text": {"type": "string", "description": "Текст материала (UTF-8)"},
                                                },
                                            },
                                        },
                                        "demo": {"type": "boolean", "default": False},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Отсортированная цепочка событий с цитатами (status: draft)"},
                        "400": {"description": "Ошибка валидации запроса"},
                        "401": {"description": "Требуется Bearer-токен"},
                        "502": {"description": "Ошибка внешнего сервиса (LLM)"},
                    },
                }
            },
            "/draft": {
                "post": {
                    "summary": "Проект документа: претензия, ответ на претензию, допсоглашение, уведомление",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["type", "facts"],
                                    "properties": {
                                        "type": {"type": "string", "enum": ["claim", "claim_response", "amendment", "notification"]},
                                        "facts": {"type": "string", "description": "Описание ситуации (от 30 символов)"},
                                        "contract": {"type": "string", "description": "Текст договора-основания (необязательно)"},
                                        "demo": {"type": "boolean", "default": False},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Проект документа со статусом draft и плейсхолдерами"},
                        "400": {"description": "Ошибка валидации запроса"},
                        "401": {"description": "Требуется Bearer-токен"},
                        "502": {"description": "Ошибка внешнего сервиса (LLM)"},
                    },
                }
            },
            "/triage": {
                "post": {
                    "summary": "Маршрутизация обращения: категория, срочность, риск, рекомендуемый эндпоинт",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["text"],
                                    "properties": {
                                        "text": {"type": "string", "description": "Текст обращения сотрудника (от 10 символов)"},
                                        "demo": {"type": "boolean", "default": False},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Классификация и маршрут (status: draft)"},
                        "400": {"description": "Ошибка валидации запроса"},
                        "401": {"description": "Требуется Bearer-токен"},
                        "502": {"description": "Ошибка внешнего сервиса (LLM)"},
                    },
                }
            },
            "/duty": {
                "post": {
                    "summary": "Расчёт госпошлины (детерминированно, без LLM): ст. 333.19 и 333.21 НК РФ",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["court"],
                                    "properties": {
                                        "court": {"type": "string", "enum": ["arbitration", "general"], "description": "arbitration — арбитраж (ст. 333.21), general — суды общей юрисдикции (ст. 333.19)"},
                                        "mode": {"type": "string", "enum": ["claim", "court_order", "non_property", "appeal", "cassation"], "default": "claim"},
                                        "amount": {"type": "string", "description": "Цена иска для claim/court_order, например «500 000 руб.»"},
                                        "applicant": {"type": "string", "enum": ["individual", "organization"], "description": "Истец для фиксированных пошлин"},
                                        "filing_date": {"type": "string", "description": "Дата подачи заявления, ДД.ММ.ГГГГ (по умолчанию — сегодня)"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Расчёт пошлины с указанием шкалы (status: draft)"},
                        "400": {"description": "Ошибка валидации запроса"},
                        "401": {"description": "Требуется Bearer-токен"},
                    },
                }
            },
            "/export": {
                "post": {
                    "summary": "Экспорт результата (Markdown) в DOCX — без LLM, детерминированно",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["document"],
                                    "properties": {
                                        "document": {"type": "string", "description": "Текст для экспорта (Markdown: заголовки, списки, таблицы)"},
                                        "title": {"type": "string", "description": "Название — станет именем файла"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "DOCX в base64 с именем файла (status: draft)"},
                        "400": {"description": "Ошибка валидации запроса"},
                        "401": {"description": "Требуется Bearer-токен"},
                    },
                }
            },
            "/protocol": {
                "post": {
                    "summary": "Протокол разногласий: таблица «редакция А / редакция Б / предложение»",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "document_a": {"type": "string", "description": "Редакция А (текст)"},
                                        "document_a_base64": {"type": "string", "description": "Редакция А (DOCX в base64)"},
                                        "document_b": {"type": "string", "description": "Редакция Б (текст)"},
                                        "document_b_base64": {"type": "string", "description": "Редакция Б (DOCX в base64)"},
                                        "party_a": {"type": "string", "default": "Сторона А"},
                                        "party_b": {"type": "string", "default": "Сторона Б"},
                                        "demo": {"type": "boolean", "default": False},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Строки протокола и Markdown-таблица (status: draft)"},
                        "400": {"description": "Ошибка валидации запроса или редакции совпадают"},
                        "401": {"description": "Требуется Bearer-токен"},
                        "502": {"description": "Ошибка внешнего сервиса (LLM)"},
                    },
                }
            },
            "/scoring": {
                "post": {
                    "summary": "Скоринг договора: сводная оценка риска 0–100 поверх проверки /review",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object", "properties": document_property}}},
                    },
                    "responses": {
                        "200": {"description": "Оценка 0–100 с вердиктом и расшифровкой весов (status: draft)"},
                        "400": {"description": "Ошибка валидации запроса"},
                        "401": {"description": "Требуется Bearer-токен"},
                        "502": {"description": "Ошибка внешнего сервиса (LLM)"},
                    },
                }
            },
            "/check": {
                "post": {
                    "summary": "Проверка контрагента по ИНН (ЕГРЮЛ ФНС, признаки риска)",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["inn"],
                                    "properties": {
                                        "inn": {"type": "string", "description": "ИНН контрагента (10 или 12 цифр)"},
                                        "demo": {"type": "boolean", "default": False},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Сведения и признаки риска (status: draft)"},
                        "400": {"description": "Ошибка валидации запроса"},
                        "401": {"description": "Требуется Bearer-токен"},
                        "502": {"description": "Сервис ФНС недоступен"},
                    },
                }
            },
            "/penalty": {
                "post": {
                    "summary": "Расчёт неустойки (детерминированно, без LLM)",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["mode", "amount", "start_date"],
                                    "properties": {
                                        "mode": {"type": "string", "enum": ["contractual", "key_rate"], "description": "contractual — договорная неустойка, key_rate — ст. 395 ГК РФ"},
                                        "amount": {"type": "string", "description": "Сумма долга, например «100 000 руб.»"},
                                        "rate_per_day": {"type": "string", "description": "Ставка в день для contractual, например «0,1»"},
                                        "start_date": {"type": "string", "description": "Первый день просрочки, ДД.ММ.ГГГГ"},
                                        "end_date": {"type": "string", "description": "День уплаты, ДД.ММ.ГГГГ (по умолчанию — сегодня)"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Расчёт с разбивкой по периодам (status: draft)"},
                        "400": {"description": "Ошибка валидации запроса"},
                        "401": {"description": "Требуется Bearer-токен"},
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

    def setup(self) -> None:
        super().setup()
        # Do not let incomplete HTTP bodies occupy a worker indefinitely.
        self.connection.settimeout(30)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        request_id = getattr(self, "_request_id", None)
        if request_id is not None:
            self.send_header("X-Request-ID", request_id)
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _read_payload(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding") is not None:
            raise RequestValidationError("Transfer-Encoding не поддерживается: используйте Content-Length.")
        if len(self.headers.get_all("Content-Length", [])) != 1:
            raise RequestValidationError("Требуется один заголовок Content-Length.")
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
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise RequestValidationError("Тело запроса передано не полностью.")
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RequestValidationError("Тело запроса должно быть корректным JSON.") from None
        if not isinstance(payload, dict):
            raise RequestValidationError("Ожидается JSON-объект.")
        return payload

    def _authorized(self) -> bool:
        token = getattr(self.server, "api_token", "")
        if not token:
            return True
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {token}"
        try:
            return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
        except (UnicodeEncodeError, AttributeError):
            return False

    def do_GET(self) -> None:
        started = time.perf_counter()
        if self.path == "/health":
            self._send_json(200, health_payload(self.server.metrics))
            self.server.metrics.record("GET /health", 200, (time.perf_counter() - started) * 1000)
        elif self.path == "/openapi.json":
            self._send_json(200, openapi_spec(auth_required=bool(self.server.api_token)))
            self.server.metrics.record("GET /openapi.json", 200, (time.perf_counter() - started) * 1000)
        else:
            self._send_json(404, {"error": "Неизвестный путь. Доступны: GET /health, GET /openapi.json, POST /ask, POST /review, POST /extract, POST /diff, POST /penalty, POST /check, POST /timeline, POST /draft, POST /triage, POST /duty, POST /export, POST /protocol, POST /scoring."})

    def do_POST(self) -> None:
        if self.path not in ("/ask", "/review", "/extract", "/diff", "/penalty", "/check", "/timeline", "/draft", "/triage", "/duty", "/export", "/protocol", "/scoring", "/sessions/delete", "/requests/cancel", "/extract/local", "/legal/skills/run"):
            self._send_json(404, {"error": "Неизвестный путь. Доступны: GET /health, GET /openapi.json, POST /ask, POST /review, POST /extract, POST /diff, POST /penalty, POST /check, POST /timeline, POST /draft, POST /triage, POST /duty, POST /export, POST /protocol, POST /scoring."})
            return
        if not self._authorized():
            # Соединение закрываем: непрочитанное тело запроса не должно
            # восприниматься как начало следующего запроса.
            self.close_connection = True
            self._send_json(401, {"error": "Требуется заголовок Authorization: Bearer <API_TOKEN>."})
            self.server.metrics.record(f"POST {self.path}", 401, 0.0)
            return
        started = time.perf_counter()
        try:
            payload = self._read_payload()
            if self.path == "/requests/cancel":
                request_id = payload.get("request_id")
                result = {"cancelled": self.server.jobs.cancel(request_id)}
            elif self.path == "/sessions/delete":
                session_id = payload.get("session_id")
                if not isinstance(session_id, str) or not SESSION_ID_PATTERN.fullmatch(session_id):
                    raise RequestValidationError("Некорректный session_id.")
                result = {"deleted": self.server.sessions.delete(session_id)}
            elif self.path == "/ask":
                # Validate and bind the session in the parent process; workers never
                # receive access to the session store.
                question = payload.get("question")
                if not isinstance(question, str) or not question.strip() or len(question) > 8000:
                    raise RequestValidationError("Некорректное поле question.")
                session_id = payload.get("session_id")
                if session_id is not None:
                    if not isinstance(session_id, str) or not SESSION_ID_PATTERN.fullmatch(session_id):
                        raise RequestValidationError("Некорректный session_id.")
                    if not self.server.sessions.exists(session_id):
                        raise RequestValidationError("Неизвестный или истёкший session_id.")
                else:
                    session_id = secrets.token_hex(24)
                # Validate all cheap fields before allocating a session or worker.
                parse_int_field(payload, "queries", 3, 1, 5)
                parse_int_field(payload, "sources", 6, 1, 10)
                if type(payload.get("demo", False)) is not bool:
                    raise RequestValidationError("Поле demo должно быть true или false.")
                if type(payload.get("include_source_materials", False)) is not bool:
                    raise RequestValidationError("Поле include_source_materials должно быть true или false.")
                try:
                    validate_as_of(payload.get("as_of"))
                except ValueError as exc:
                    raise RequestValidationError(str(exc)) from exc
                request_id = self.headers.get("X-Request-ID") or uuid.uuid4().hex
                if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,64}", request_id):
                    raise RequestValidationError("Некорректный X-Request-ID.")
                context, identity = self.server.sessions.begin(session_id)
                worker_payload = {**payload}
                worker_payload.pop("session_id", None)
                self._request_id = request_id
                def publish(reply):
                    # Commit staged files only after successful, non-cancelled work.
                    # The worker writes exclusively inside its disposable job directory.
                    if getattr(self.server, "persist_results", False):
                        import shutil
                        from pathlib import Path
                        report = Path(reply["report_file"])
                        journal = Path(reply["journal_file"])
                        staging = report.parent
                        if not report.is_file() or not journal.is_file() or journal.parent != staging:
                            raise RuntimeError("Некорректные файлы отчёта рабочего процесса.")
                        destination = self.server.output_dir / staging.name
                        self.server.output_dir.mkdir(parents=True, exist_ok=True)
                        if destination.exists():
                            raise RuntimeError("Каталог отчёта уже существует.")
                        shutil.copytree(staging, destination)
                        reply["report_file"] = str(destination / report.name)
                        reply["journal_file"] = str(destination / journal.name)
                    self.server.sessions.remember(session_id, question, reply["answer"], expected_session=identity)
                    reply["session_id"] = session_id
                    return reply
                result = self.server.jobs.execute(
                    request_id,
                    {"path": "/ask", "payload": worker_payload, "context": context,
                     "persist_results": getattr(self.server, "persist_results", False)},
                    commit=publish,
                )
            elif self.path in ("/review", "/extract", "/diff", "/extract/local", "/legal/skills/run"):
                request_id = self.headers.get("X-Request-ID") or uuid.uuid4().hex
                self._request_id = request_id
                result = self.server.jobs.execute(request_id, {"path": self.path, "payload": payload})
            elif self.path == "/penalty":
                result = handle_penalty(payload)
            elif self.path == "/check":
                result = handle_check(payload)
            elif self.path == "/timeline":
                result = handle_timeline(payload)
            elif self.path == "/draft":
                result = handle_draft(payload)
            elif self.path == "/triage":
                result = handle_triage(payload)
            elif self.path == "/duty":
                result = handle_duty(payload)
            elif self.path == "/export":
                result = handle_export(payload)
            elif self.path == "/protocol":
                result = handle_protocol(payload)
            elif self.path == "/scoring":
                result = handle_scoring(payload)
            else:
                result = handle_diff(payload)
        except JobError as exc:
            self._send_json(exc.status, {"code": exc.code, "error": str(exc)})
            status = exc.status
        except TimeoutError:
            self.close_connection = True
            self._send_json(408, {"error": "Истекло время ожидания данных запроса."})
            status = 408
        except ValueError as exc:
            # Rejecting a body before consuming it must not permit HTTP request smuggling.
            self.close_connection = True
            self._send_json(400, {"error": str(exc)})
            status = 400
        except AgentError as exc:
            self._send_json(502, {"error": str(exc)})
            status = 502
        except Exception as exc:  # noqa: BLE001 — последняя линия защиты сервиса
            self._send_json(500, {"error": "Внутренняя ошибка сервиса. Подробности не раскрываются в HTTP-ответе."})
            status = 500
        else:
            self._send_json(200, result)
            status = 200
        self.server.metrics.record(f"POST {self.path}", status, (time.perf_counter() - started) * 1000)


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    output_dir: Path | str = Path("результаты"),
    api_token: str = "",
    request_timeout_seconds: float = 300,
) -> ThreadingHTTPServer:
    import ipaddress
    try:
        loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback and not api_token.strip():
        raise ValueError("API_TOKEN обязателен при доступе к API за пределами localhost.")
    jobs = RequestJobs(timeout_seconds=request_timeout_seconds)
    class ManagedLegalServer(ThreadingHTTPServer):
        daemon_threads = True

        def server_close(self):
            try:
                self.jobs.close()
            finally:
                super().server_close()

    server = ManagedLegalServer((host, port), LegalApiHandler)
    server.jobs = jobs
    server.output_dir = Path(output_dir)
    server.api_token = api_token.strip()
    server.sessions = SessionStore()
    server.metrics = Metrics()
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
    try:
        server = create_server(host, port, output_dir, api_token)
    except (ValueError, OSError) as exc:
        print(f"Ошибка запуска API: {exc}", file=sys.stderr)
        return 1
    auth_note = "включена (API_TOKEN)" if server.api_token else "отключена (задайте API_TOKEN)"
    print(f"HTTP API юридического помощника: http://{host}:{port}")
    print(f"Эндпоинты: GET /health, GET /openapi.json, POST /ask, POST /review, POST /extract, POST /diff, POST /penalty, POST /check, POST /timeline, POST /draft, POST /triage, POST /duty, POST /export, POST /protocol, POST /scoring. Авторизация: {auth_note}.")
    print(f"Результаты сохраняются в: {server.output_dir.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        server.server_close()
    return 0




# Unreviewed statute-corpus endpoints are deliberately not exposed.

if __name__ == "__main__":
    raise SystemExit(main())
