#!/usr/bin/env python3
"""HTTP API для юридического ИИ-помощника по законодательству РФ.

Эндпоинты:
  GET  /health       — проверка работоспособности и готовности конфигурации;
  GET  /openapi.json — машинное описание API (OpenAPI 3.0);
  POST /ask          — правовой вопрос → {answer, sources, checked_on, disclaimer};
  POST /review       — текст договора → {summary, risks, missing_clauses, disclaimer};
  POST /extract      — текст договора → {parties, amounts, dates, penalties, term, jurisdiction};
  POST /diff         — две редакции договора → {summary, changes, diff}.
  POST /sessions/delete — удалить историю сессии из памяти.

Запуск: python api.py
Настройки через переменные окружения:
  API_HOST, API_PORT, API_OUTPUT_DIR, API_TOKEN (необязательный Bearer-токен),
  плюс LLM_* и TAVILY_* из agent.py.

Сервер использует только стандартную библиотеку Python. Юридическая логика
и обязательная проверка поправок полностью остаются в agent.py.
"""

from __future__ import annotations

import hmac
import hashlib
from collections import OrderedDict
import ipaddress
import json
import os
import re
import sys
import shutil
import signal
import threading
import tempfile
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent import LEGAL_DISCLAIMER, AgentError, load_dotenv, run, validate_as_of, current_check_date
from extract import handle_extract
from redline import handle_diff
from review import handle_review
from legal_review import handle_legal_review, review_options
from case_law import handle_case_law
from legal_coverage import coverage_report
from privacy_review import handle_privacy_check, privacy_options
from legal_research import handle_legal_research, research_options
from legal_skills import load_skills, skill_options, SkillRegistryError
from request_jobs import RequestJobs, JobError, ID_PATTERN
from preflight import inspect_configuration, inspect_runtime

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
MAX_BODY_BYTES = 15 * 1024 * 1024
MAX_DIFF_BODY_BYTES = 28 * 1024 * 1024
MAX_CONCURRENT_REQUESTS = 4
MAX_QUESTION_CHARS = 12_000
SESSION_MAX_ENTRIES = 10
SESSION_MAX_COUNT = 1_000
SERVICE_NAME = "legal-research-agent"
API_VERSION = "1.13"
SESSION_TTL_SECONDS = 3600
SESSION_ANSWER_CHARS = 500
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class SessionStore:
    """Краткосрочная память сессий в оперативной памяти (без БД, с TTL)."""

    def __init__(self, ttl_seconds: int = SESSION_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: OrderedDict[str, dict[str, Any]] = OrderedDict()
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

    def begin(self, session_id: str) -> tuple[str, object]:
        """Capture session identity so deletion also invalidates in-flight updates."""
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            session = self._sessions.get(session_id)
            if session is None:
                if len(self._sessions) >= SESSION_MAX_COUNT:
                    self._sessions.popitem(last=False)
                session = {"updated": now, "entries": []}
                self._sessions[session_id] = session
            entries = list(session["entries"])
        context = "\n---\n".join(f"Вопрос: {item['question']}\nКраткий ответ: {item['answer']}" for item in entries)
        return context, session

    def delete(self, session_id: str) -> bool:
        with self._lock:
            self._purge_expired(time.time())
            return self._sessions.pop(session_id, None) is not None

    def remember(self, session_id: str, question: str, answer: str, *, expected_session: object | None = None) -> None:
        """Запоминает пару вопрос/ответ (ответ усечённо) в сессии."""
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            session = self._sessions.get(session_id)
            if expected_session is not None and session is not expected_session:
                return
            if session is None:
                if len(self._sessions) >= SESSION_MAX_COUNT:
                    self._sessions.popitem(last=False)
                session = {"updated": now, "entries": []}
                self._sessions[session_id] = session
            else:
                self._sessions.move_to_end(session_id)
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


def handle_ask(payload: dict[str, Any], output_dir: Path, sessions: SessionStore | None = None, *, persist_results: bool = True, session_context_override: str | None = None) -> dict[str, Any]:
    """Выполняет полный цикл агента и возвращает структурированный ответ."""
    if not persist_results:
        with tempfile.TemporaryDirectory(prefix="legal-research-") as directory:
            result = handle_ask(payload, Path(directory), sessions, persist_results=True, session_context_override=session_context_override)
            result.update(report_file=None, journal_file=None, results_persisted=False)
            return result
    question = payload.get("question", "")
    if not isinstance(question, str) or not question.strip():
        raise RequestValidationError("Поле «question» обязательно и не должно быть пустым.")
    if len(question) > MAX_QUESTION_CHARS:
        raise RequestValidationError(f"Вопрос не должен превышать {MAX_QUESTION_CHARS} символов.")
    as_of = validate_as_of(payload.get("as_of"))
    session_id = payload.get("session_id")
    if session_id is not None and (not isinstance(session_id, str) or not SESSION_ID_PATTERN.fullmatch(session_id)):
        raise RequestValidationError("Поле «session_id» должно быть строкой из 1–64 символов: латиница, цифры, дефис.")
    query_count = parse_int_field(payload, "queries", 3, 1, 5)
    source_limit = parse_int_field(payload, "sources", 6, 1, 10)
    demo = payload.get("demo", False)
    if not isinstance(demo, bool):
        raise RequestValidationError("Поле «demo» должно быть логическим значением true или false.")
    include_materials = payload.get('include_source_materials', False)
    if not isinstance(include_materials, bool):
        raise RequestValidationError('include_source_materials должен быть логическим значением.')
    context, session_identity = sessions.begin(session_id) if sessions and session_id else ("", None)

    if session_context_override is not None:
        context = session_context_override

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
        as_of=as_of,
        verbose=False,
    )
    answer = report_path.read_text(encoding="utf-8")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if sessions and session_id:
        sessions.remember(session_id, question, answer, expected_session=session_identity)
    result = {
        "answer": answer,
        "verified": False,
        "lawyer_reviewed": False,
        "sources": [
            {"title": source["title"], "url": source["url"],
             **({"retrieval_mode": source['retrieval_mode'], 'period_supported': False,
                 'original_publication': False, 'verified': False}
                if source.get('retrieval_mode') == 'local_statutory_extract' else {})}
            for source in journal["sources"]
        ],
        "checked_on": journal["checked_on"],
        "as_of": journal["as_of"],
        "evidence": journal["evidence"],
        "provision_period": journal["legal_update_check"]["provision_history"]["selection"],
        "disclaimer": LEGAL_DISCLAIMER,
        "demo": journal["demo"],
        "session_id": session_id,
        "results_persisted": True,
        "report_file": str(report_path),
        "journal_file": str(journal_path),
    }
    if include_materials:
        result['source_materials'] = [
            {'id': index, 'title': source['title'], 'url': source['url'], 'text': source['text'],
             'sha256': hashlib.sha256(source['text'].encode('utf-8')).hexdigest(),
             'snapshot_exported_on': current_check_date(), 'period_supported': False,
             'verified': False, 'capture_kind': 'research_material_snapshot'}
            for index, source in enumerate(journal['sources'], 1)]
    return result


def health_payload() -> dict[str, Any]:
    configuration = inspect_configuration()['provider_configuration']
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": API_VERSION,
        "endpoints": ["GET /health", "GET /runtime/readiness", "GET /openapi.json", "POST /ask", "POST /review", "POST /extract", "POST /extract/local", "POST /diff", "POST /sessions/delete", "POST /requests/cancel", "POST /review/legal", "POST /case-law/check", "POST /legal/coverage", "GET /privacy/options", "POST /privacy/check", "GET /review/options", "GET /legal/research/options", "POST /legal/research", "POST /legal/corpus", "GET /legal/skills/options", "POST /legal/skills/run"],
        "llm_configured": configuration['llm'],
        "tavily_configured": configuration['search'],
        "demo_available": True,
    }


def openapi_spec(*, require_auth: bool = True) -> dict[str, Any]:
    """Машинное описание API (OpenAPI 3.0) для интеграции с оркестраторами."""
    try:
        skill_ids = list(load_skills())
    except SkillRegistryError:
        skill_ids = None
    document_property = {
        "document": {"type": "string", "description": "Полный текст договора (UTF-8)"},
        "document_base64": {"type": "string", "description": "Договор в формате DOCX, закодированный в base64"},
        "demo": {"type": "boolean", "default": False, "description": "Демонстрационный режим без внешних запросов"},
    }
    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": SERVICE_NAME,
            "version": API_VERSION,
            "description": "Локальный юридический ИИ-помощник по законодательству РФ.",
        },
        "paths": {
            "/legal/research/options": {"get": {
                "summary": "Сценарии договоров, процесса, подписи, ответственности и данных сервиса",
                "responses": {"200": {"description": "Вопросы и границы сценариев"}}}},
            "/legal/research": {"post": {
                "summary": "Локальный план правового исследования с нормами и источниками",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {
                    "type": "object", "additionalProperties": False, "required": ["scenario"], "properties": {
                        "scenario": {"type": "string", "enum": list(research_options()['scenarios'])},
                        "facts": {"type": "object", "additionalProperties": {"type": "boolean", "nullable": True}},
                        "as_of": {"type": "string", "format": "date"}, "demo": {"type": "boolean"}}}}}},
                "responses": {"200": {"description": "Вопросы, источники и открытые пробелы; verified=false"},
                              "400": {"description": "Ошибка ввода"}, "401": {"description": "Нужен токен"}}}},
            "/legal/corpus": {"post": {
                "summary": "Полученные нормативные тексты с контрольными суммами и ограничениями",
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object", "additionalProperties": False, "properties": {
                        "card_ids": {"type": "array", "maxItems": 1000, "items": {"type": "string"}},
                        "include_text": {"type": "boolean", "default": False,
                                         "description": "Для полного извлечения задайте от 1 до 50 card_ids"},
                        "as_of": {"type": "string", "format": "date"}}}}}},
                "responses": {"200": {"description": "Извлечения; история и применимость не подтверждены"},
                              "400": {"description": "Ошибка ввода"}, "401": {"description": "Нужен токен"}}}},
            "/review/options": {"get": {
                "summary": "Вопросы по профилю, типу договора и специальному режиму; ответы не заменяют квалификацию",
                "responses": {"200": {"description": "Профили и дополнительные ключи facts"}}}},
            "/legal/coverage": {"post": {
                "summary": "Матрица исследования норм и открытых пробелов по ТЗ",
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object", "properties": {
                        "module_ids": {"type": "array", "items": {"type": "string"}},
                        "card_ids": {"type": "array", "items": {"type": "string"}},
                        "as_of": {"type": "string", "format": "date"}}}}}},
                "responses": {"200": {"description": "Исследовательские карточки, verified=false"},
                              "400": {"description": "Ошибка ввода"}, "401": {"description": "Нужен токен"}}}},
            "/privacy/options": {"get": {
                "summary": "Сценарии и вопросы локальной анкеты LAW-01",
                "responses": {"200": {"description": "Допустимые сценарии и ключи фактов"}}}},
            "/privacy/check": {"post": {
                "summary": "Сбор обстоятельств обработки данных без внешних запросов и юридического заключения",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {
                    "type": "object", "additionalProperties": False, "required": ["scenario"],
                    "properties": {
                        "scenario": {"type": "string", "enum": list(privacy_options()['scenarios'])},
                        "as_of": {"type": "string", "format": "date"},
                        "demo": {"type": "boolean"},
                        "facts": {"type": "object", "additionalProperties": {"type": "boolean", "nullable": True}}
                    }}}}},
                "responses": {"200": {"description": "Уточнения и пробелы доказательств, verified=false"},
                              "400": {"description": "Ошибка ввода"}, "401": {"description": "Нужен токен"}}}},
            "/review/legal": {"post": {
                "summary": "Проверка договора с цитатами правовых источников и уточнением фактов",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {
                    "type": "object", "properties": {**document_property,
                        "profile": {"type": "string", "enum": ["business", "consumer", "employment"]},
                        "contract_type": {"type": "string", "enum": ["general", "supply", "lease", "services", "work"]},
                        "contract_signed_on": {"type": "string", "format": "date", "nullable": True},
                        "review_event": {"type": "string", "enum": ["unspecified", "conclusion", "performance", "termination", "dispute"]},
                        "as_of": {"type": "string", "format": "date"},
                        "facts": {"type": "object", "additionalProperties": {"type": "boolean", "nullable": True}}
                    }}}}},
                "responses": {"200": {"description": "Черновик или запрос уточнений; verified=false"},
                              "400": {"description": "Ошибка ввода"}, "401": {"description": "Нужен токен"},
                              "502": {"description": "Ошибка внешнего сервиса"}}}},
            "/case-law/check": {"post": {
                "summary": "Судебные материалы: реквизиты, фрагменты исхода и истории с проверкой цитат",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {
                    "type": "object", "required": ["question"], "properties": {
                        "question": {"type": "string", "maxLength": 12000},
                        "as_of": {"type": "string", "format": "date"}, "demo": {"type": "boolean"}
                    }}}}},
                "responses": {"200": {"description": "Выборочные материалы, verified=false. act_type/instance/outcome/case_facts — цитатные фрагменты или not_established; subsequent_history — неполная цепочка. non_case_materials отделены от актов по делам; сопоставимость и вступление в силу не установлены."},
                              "400": {"description": "Ошибка ввода"}, "401": {"description": "Нужен токен"},
                              "502": {"description": "Ошибка внешнего сервиса"}}}},
            "/sessions/delete": {
                "post": {
                    "summary": "Удалить историю сессии из памяти; незавершённые запросы не восстановят её",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["session_id"],
                        "properties": {"session_id": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,64}$"}}
                    }}}},
                    "responses": {"200": {"description": "deleted=true/false"},
                                  "400": {"description": "Неверный session_id"},
                                  "401": {"description": "Требуется Bearer-токен"}}
                }
            },
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
                                        "as_of": {"type": "string", "format": "date", "description": "Дата правовой ситуации; по умолчанию дата проверки"},
                                        "queries": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
                                        "sources": {"type": "integer", "minimum": 1, "maximum": 10, "default": 6},
                                        "include_source_materials": {"type": "boolean", "default": False,
                                            "description": "Включить тексты фактически использованных материалов и SHA-256 для локальной оценки; это не архив оригиналов"},
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
    spec['paths']['/requests/cancel'] = {'post': {
        'summary': 'Отменить запрос по X-Request-ID; общий токен, без изоляции пользователей',
        'requestBody': {'required': True, 'content': {'application/json': {'schema': {
            'type': 'object', 'required': ['request_id'], 'properties': {
                'request_id': {'type': 'string', 'pattern': '^[A-Za-z0-9-]{16,64}$'}}}}}},
        'responses': {'200': {'description': 'Отмена принята либо запрос уже завершён'},
                      '400': {'description': 'Неверный идентификатор'},
                      '401': {'description': 'Нужен токен'}}}}
    spec['paths']['/legal/skills/options'] = {'get': {
        'summary': 'Доступные закреплённые методики и их происхождение',
        'responses': {'200': {'description': 'Каталог навыков'}}}}
    spec['paths']['/runtime/readiness'] = {'get': {
        'summary': 'Локальная проверка конфигурации и готовности отдельных функций без сети',
        'security': [{'ApiToken': []}] if require_auth else [],
        'responses': {'200': {'description': 'Наличие конфигурации не означает успешный вызов провайдера'},
                      '401': {'description': 'Нужен токен API'}}}}
    spec['paths']['/legal/skills/run'] = {'post': {
        'summary': 'Выполнить методику; цитаты проверяются, правовые выводы требуют приёмки',
        'requestBody': {'required': True, 'content': {'application/json': {'schema': {
            'type': 'object', 'additionalProperties': False, 'required': ['skill_id'],
            'properties': {'skill_id': {'type': 'string', **({'enum': skill_ids} if skill_ids else {})},
                'document': {'type': 'string', 'minLength': 20, 'maxLength': 200000},
                'document_base64': {'type': 'string'}, 'demo': {'type': 'boolean'},
                'as_of': {'type': 'string', 'format': 'date'}, 'jurisdiction': {'enum': ['RU']},
                'user_position': {'type': 'string', 'maxLength': 500},
                'playbook': {'type': 'string', 'maxLength': 12000},
                'research_query': {'type': 'string', 'maxLength': 500}},
            'oneOf': [{'required': ['document']}, {'required': ['document_base64']}]}}}},
        'responses': {'200': {'description': 'Черновик, запрос фактов или отсутствие проверяемых источников'},
                      '400': {'description': 'Ошибка входных данных'}, '401': {'description': 'Нужен токен'},
                      '502': {'description': 'Ошибка провайдера или проверки цитат'},
                      '503': {'description': 'Реестр навыков недоступен'}}}}
    spec['paths']['/extract/local'] = {'post': {
        'summary': 'Локальные реквизиты: точные фрагменты и позиции, без модели и внешних вызовов',
        'requestBody': {'required': True, 'content': {'application/json': {'schema': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'document': {'type': 'string', 'minLength': 20, 'maxLength': 200000},
                           'document_base64': {'type': 'string'},
                           'max_matches': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 200}},
            'oneOf': [{'required': ['document']}, {'required': ['document_base64']}]}}}},
        'responses': {'200': {'description': 'Совпадения; принадлежность, полнота и юридическая применимость не проверены'},
                      '400': {'description': 'Ошибка ввода'}, '401': {'description': 'Нужен токен'}}}}
    for route in ('/ask', '/review', '/review/legal', '/extract', '/diff', '/case-law/check', '/legal/skills/run', '/extract/local'):
        operation = spec['paths'][route]['post']
        operation.setdefault('parameters', []).append({'in': 'header', 'name': 'X-Request-ID',
            'schema': {'type': 'string', 'pattern': '^[A-Za-z0-9-]{16,64}$'},
            'description': 'Уникальный идентификатор для отмены; при отсутствии генерируется сервером.'})
        operation['responses'].update({'409': {'description': 'Отменён или идентификатор уже использован'},
            '429': {'description': 'Все рабочие процессы заняты'}, '504': {'description': 'Общий дедлайн истёк'}})
    spec['components'] = {'securitySchemes': {'ApiToken': {
        'type': 'http', 'scheme': 'bearer',
        'description': 'Токен API_TOKEN сервера; это не ключ модели или поискового провайдера.'}}}
    for path in spec['paths'].values():
        if 'post' in path:
            path['post']['security'] = [{'ApiToken': []}] if require_auth else []
    return spec


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Threaded server with a hard cap on simultaneous request handlers."""

    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler]):
        self._request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        super().__init__(server_address, handler_class)

    def server_close(self) -> None:
        if hasattr(self, "jobs"):
            self.jobs.close()
        super().server_close()

    def process_request(self, request: Any, client_address: Any) -> None:
        # A just-sent response can reach the client before its handler exits.
        # Briefly wait for that slot rather than reset the next control request.
        if not self._request_slots.acquire(timeout=0.1):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class LegalApiHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVICE_NAME}/{API_VERSION}"
    protocol_version = "HTTP/1.1"
    # Applied by StreamRequestHandler.setup before headers are read.
    timeout = 30

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if getattr(self, "request_id", None):
            self.send_header("X-Request-ID", self.request_id)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _read_payload(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise RequestValidationError("Transfer-Encoding не поддерживается; передайте Content-Length.")
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or 0)
        except ValueError:
            self.close_connection = True
            raise RequestValidationError("Некорректный заголовок Content-Length.") from None
        if length <= 0:
            self.close_connection = True
            raise RequestValidationError("Тело запроса пустое: ожидается JSON.")
        body_limit = MAX_DIFF_BODY_BYTES if self.path == "/diff" else MAX_BODY_BYTES
        if length > body_limit:
            self.close_connection = True
            raise RequestValidationError(f"Тело запроса больше допустимого лимита {body_limit} байт для этого эндпоинта.")
        self.connection.settimeout(30)
        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            raise RequestValidationError("Тело запроса передано не полностью.")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.close_connection = True
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
        return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))

    def do_GET(self) -> None:
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                  "/style.css": ("style.css", "text/css")}
        if self.path in assets:
            name, content_type = assets[self.path]
            try:
                body = (Path(__file__).parent / "web" / name).read_bytes()
            except OSError:
                self._send_json(503, {"error": "Файлы интерфейса не установлены."})
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/runtime/readiness":
            if not self._authorized():
                self._send_json(401, {"error": "Для проверки настройки введите токен API."})
                return
            self._send_json(200, inspect_runtime())
        elif self.path == "/health":
            self._send_json(200, {**health_payload(),
                                  "request_timeout_seconds": self.server.jobs.timeout_seconds,
                                  "request_cancellation_available": True})
        elif self.path == "/openapi.json":
            self._send_json(200, openapi_spec(require_auth=bool(self.server.api_token)))
        elif self.path == "/privacy/options":
            self._send_json(200, privacy_options())
        elif self.path == "/legal/skills/options":
            try:
                self._send_json(200, skill_options())
            except SkillRegistryError:
                self._send_json(503, {'error': 'Реестр навыков недоступен.', 'code': 'skill_registry_unavailable'})
        elif self.path == "/legal/research/options":
            self._send_json(200, research_options())
        elif self.path == "/review/options":
            self._send_json(200, review_options())
        else:
            self._send_json(404, {"error": "Неизвестный путь. Доступны: GET /health, GET /openapi.json, POST /ask, POST /review, POST /extract, POST /diff, POST /sessions/delete."})

    def do_POST(self) -> None:
        if self.path not in ("/ask", "/review", "/review/legal", "/case-law/check", "/extract", "/diff", "/sessions/delete", "/legal/coverage", "/privacy/check", "/legal/research", "/legal/corpus", "/requests/cancel", "/legal/skills/run", "/extract/local"):
            self._send_json(404, {"error": "Неизвестный путь. Доступны: GET /health, GET /openapi.json, POST /ask, POST /review, POST /extract, POST /diff, POST /sessions/delete."})
            return
        if not self._authorized():
            # Соединение закрываем: непрочитанное тело запроса не должно
            # восприниматься как начало следующего запроса.
            self.close_connection = True
            self._send_json(401, {"error": "Требуется заголовок Authorization: Bearer <API_TOKEN>."})
            return
        try:
            payload = self._read_payload()
            if self.path == "/legal/coverage":
                result = coverage_report(payload.get('module_ids'), payload.get('as_of'), card_ids=payload.get('card_ids'))
            elif self.path == "/legal/research":
                result = handle_legal_research(payload)
            elif self.path == "/legal/corpus":
                from statute_corpus import handle_corpus
                result = handle_corpus(payload)
            elif self.path == "/privacy/check":
                result = handle_privacy_check(payload)
            elif self.path == "/sessions/delete":
                session_id = payload.get("session_id")
                if not isinstance(session_id, str) or not SESSION_ID_PATTERN.fullmatch(session_id):
                    raise RequestValidationError("Требуется корректный session_id: латиница, цифры, дефис, 1–64 символа.")
                result = {"deleted": self.server.sessions.delete(session_id)}
            elif self.path == '/requests/cancel':
                result = {'request_id': payload.get('request_id'),
                          'cancelled': self.server.jobs.cancel(payload.get('request_id'))}
            else:
                request_id = self.headers.get('X-Request-ID') or uuid.uuid4().hex
                if not ID_PATTERN.fullmatch(request_id):
                    raise RequestValidationError('Некорректный X-Request-ID.')
                self.request_id = request_id
                session_id = payload.get('session_id') if self.path == '/ask' else None
                context, identity = '', None
                if session_id is not None:
                    if not isinstance(session_id, str) or not SESSION_ID_PATTERN.fullmatch(session_id):
                        raise RequestValidationError('Некорректный session_id.')
                    context, identity = self.server.sessions.begin(session_id)
                def commit(result):
                    if self.path == '/ask':
                        if self.server.persist_results:
                            source = Path(result['report_file']).parent
                            destination = Path(self.server.output_dir) / source.name
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            try:
                                shutil.copytree(source, destination)
                            except Exception:
                                shutil.rmtree(destination, ignore_errors=True)
                                raise
                            result['report_file'] = str(destination / Path(result['report_file']).name)
                            result['journal_file'] = str(destination / Path(result['journal_file']).name)
                        else:
                            result.update(report_file=None, journal_file=None, results_persisted=False)
                        if session_id:
                            self.server.sessions.remember(session_id, payload.get('question', ''),
                                                          result['answer'], expected_session=identity)
                    return result
                result = self.server.jobs.execute(self.request_id,
                    {'path': self.path, 'payload': payload, 'context': context}, commit)
        except JobError as exc:
            self._send_json(exc.status, {'error': str(exc), 'code': exc.code,
                                        'request_id': self.request_id})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except AgentError as exc:
            self._send_json(502, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — последняя линия защиты сервиса
            print(f"Внутренняя ошибка API: {type(exc).__name__}", file=sys.stderr)
            self._send_json(500, {"error": "Внутренняя ошибка сервиса."})
        else:
            self._send_json(200, result)


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    output_dir: Path | str = Path("результаты"),
    api_token: str = "",
    persist_results: bool = False,
    request_timeout_seconds: float = 300,
) -> ThreadingHTTPServer:
    normalized_host = host.strip().lower()
    is_loopback = normalized_host == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(normalized_host).is_loopback
        except ValueError:
            is_loopback = False
    if not api_token.strip() and not is_loopback:
        raise ValueError(
            "API_TOKEN обязателен при прослушивании внешнего интерфейса. "
            "Для локального режима используйте API_HOST=127.0.0.1."
        )
    jobs = RequestJobs(request_timeout_seconds)
    server = BoundedThreadingHTTPServer((host, port), LegalApiHandler)
    server.output_dir = Path(output_dir)
    server.api_token = api_token.strip()
    server.persist_results = persist_results
    server.sessions = SessionStore()
    server.jobs = jobs
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
        persistence = os.getenv("API_PERSIST_RESEARCH", "false").strip().lower()
        if persistence not in {"true", "false"}:
            raise ValueError("API_PERSIST_RESEARCH должен быть true или false.")
        server = create_server(host, port, output_dir, api_token, persist_results=persistence == "true",
                               request_timeout_seconds=float(os.getenv("API_REQUEST_TIMEOUT_SECONDS", "300")))
    except ValueError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 1
    auth_note = "включена (API_TOKEN)" if server.api_token else "локальный доступ без токена"
    print(f"HTTP API юридического помощника: http://{host}:{port}")
    print("Эндпоинты: " + ", ".join(health_payload()["endpoints"]) + f". Авторизация: {auth_note}.")
    if server.persist_results:
        print(f"Постоянные результаты /ask сохраняются в: {server.output_dir.resolve()}")
    else:
        print("Результаты /ask временные и удаляются после обработки; память сессий управляется отдельно.")
    def stop_on_signal(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_on_signal)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


