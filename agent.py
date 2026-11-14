#!/usr/bin/env python3
"""Локальный ИИ-помощник по законодательству РФ с проверкой изменений."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from source_admission import classify_source, SourceAdmissionError
from urllib.parse import urlsplit

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore[assignment,misc]


REQUIRED_SECTIONS = (
    "Краткий ответ",
    "Правовое основание",
    "Актуальность и изменения",
    "Как применить к ситуации",
    "Ограничения",
)
CITATION_SECTIONS = ("Краткий ответ", "Правовое основание", "Актуальность и изменения")
LEGAL_DISCLAIMER = "Информационная справка, а не индивидуальная юридическая консультация. Перед принятием решения проверьте текст нормы и при необходимости обратитесь к юристу."


class AgentError(RuntimeError):
    """Понятная ошибка выполнения агента."""


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    text: str
    query: str


class LanguageModel(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class SearchProvider(Protocol):
    def search(self, query: str, limit: int) -> list[Source]: ...


def load_dotenv(path: Path = Path(".env")) -> None:
    """Загружает простой .env без сторонних зависимостей."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def validate_service_url(url: str) -> None:
    """Require encrypted external transport and reject ambiguous endpoint URLs."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        parsed.port
    except ValueError:
        raise AgentError("Некорректный URL внешнего сервиса.") from None
    local = host.lower() == "localhost"
    if not local:
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
    if (not host or parsed.username is not None or parsed.password is not None or parsed.fragment
            or parsed.scheme not in {"https", "http"}
            or (parsed.scheme == "http" and not local)):
        raise AgentError("Внешний сервис требует HTTPS без логина, пароля и фрагмента в URL; HTTP разрешён только для loopback.")


class NoCredentialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        response.close()
        raise AgentError("Перенаправление внешнего сервиса заблокировано: укажите конечный URL, чтобы не передавать ключи и документы другому адресу.")


def open_json_request(request: urllib.request.Request, timeout: int):
    return urllib.request.build_opener(NoCredentialRedirects()).open(request, timeout=timeout)


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 90) -> dict[str, Any]:
    """POST JSON with one bounded retry for transient upstream failures."""
    validate_service_url(url)
    retryable_statuses = {429, 500, 502, 503, 504}
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(2):
        request = urllib.request.Request(
            url,
            data=encoded,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with open_json_request(request, timeout) as response:
                raw = response.read(5 * 1024 * 1024 + 1)
                if len(raw) > 5 * 1024 * 1024:
                    raise AgentError("Сервис вернул слишком большой ответ.")
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise AgentError("Сервис вернул JSON не в виде объекта.")
                return value
        except urllib.error.HTTPError as exc:
            exc.close()
            last_error = AgentError(f"Сервис вернул HTTP {exc.code}.")
            if exc.code not in retryable_statuses or attempt:
                raise last_error from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = AgentError("Не удалось подключиться к сервису.")
            if attempt:
                raise last_error from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AgentError("Сервис вернул некорректный JSON.") from exc
        if attempt == 0:
            time.sleep(1)
    raise AgentError(str(last_error or "Не удалось выполнить запрос."))


def current_check_date() -> str:
    """Return the calendar date in Moscow (UTC+3), independent of host timezone."""
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
        except (OSError, ValueError):
            pass
    return datetime.now(timezone(timedelta(hours=3))).date().isoformat()


def _normalized_query_key(query: str) -> str:
    return " ".join(query.split()).casefold()


def deduplicate_queries(queries: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for query in queries:
        if not isinstance(query, str):
            continue
        clean = " ".join(query.split())
        key = _normalized_query_key(clean)
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def extract_json(text: str) -> dict[str, Any]:
    """Извлекает JSON-объект даже из ответа с Markdown-ограждением."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        value = None
        for index, char in enumerate(cleaned):
            if char != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(cleaned[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                value = candidate
                break
        if value is None:
            raise AgentError("Модель не вернула корректный JSON-объект.")
    if not isinstance(value, dict):
        raise AgentError("Ожидался JSON-объект.")
    return value


class OpenAICompatibleModel:
    """Клиент для API со схемой POST /chat/completions."""

    def __init__(self) -> None:
        self.api_key = os.getenv("LLM_API_KEY", "").strip()
        self.model = os.getenv("LLM_MODEL", "").strip()
        self.base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        if not self.api_key or not self.model:
            raise AgentError("Задайте LLM_API_KEY и LLM_MODEL в файле .env.")

    def complete(self, system: str, user: str) -> str:
        data = post_json(
            f"{self.base_url}/chat/completions",
            {
                "model": self.model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            {"Authorization": f"Bearer {self.api_key}"},
        )
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentError("Ответ модели имеет неподдерживаемый формат.") from exc
        if not isinstance(content, str) or not content.strip():
            raise AgentError("Модель вернула пустой ответ.")
        return content.strip()


class TavilySearch:
    def __init__(self, domains: list[str] | None = None) -> None:
        self.api_key = os.getenv("TAVILY_API_KEY", "").strip()
        self.url = os.getenv("TAVILY_API_URL", "https://api.tavily.com/search")
        self.domains = domains if domains is not None else [
            item.strip()
            for item in os.getenv(
                "LEGAL_SEARCH_DOMAINS", "pravo.gov.ru,publication.pravo.gov.ru"
            ).split(",")
            if item.strip()
        ]
        if not self.domains:
            raise AgentError("Список LEGAL_SEARCH_DOMAINS не может быть пустым.")
        for domain in self.domains:
            try:
                classify_source(f"https://{domain}/")
            except SourceAdmissionError as exc:
                raise AgentError(f"Недопустимый поисковый домен: {domain}") from exc
        if not self.api_key:
            raise AgentError("Задайте TAVILY_API_KEY в файле .env.")

    def search(self, query: str, limit: int) -> list[Source]:
        data = post_json(
            self.url,
            {
                "query": query,
                "search_depth": "basic",
                "max_results": limit,
                "include_answer": False,
                "include_raw_content": False,
                "include_domains": self.domains,
            },
            {"Authorization": f"Bearer {self.api_key}"},
            timeout=60,
        )
        sources: list[Source] = []
        for item in data.get("results", []):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if not url.startswith(("https://", "http://")):
                continue
            try:
                classify_source(url)
            except SourceAdmissionError:
                continue
            text = str(item.get("content") or "").strip()
            if not text:
                continue
            sources.append(
                Source(
                    title=str(item.get("title") or url).strip(),
                    url=url,
                    text=text,
                    query=query,
                )
            )
        return sources


class DemoModel:
    def complete(self, system: str, user: str) -> str:
        if "legal_update_queries" in system:
            return json.dumps(
                {"legal_update_queries": ["условная проверка последних изменений"], "notes": "Демонстрационная проверка изменений."},
                ensure_ascii=False,
            )
        if "court_queries" in system:
            return json.dumps(
                {"court_queries": ["условный поиск судебной практики"], "notes": "Демонстрационный поиск практики."},
                ensure_ascii=False,
            )
        if "additional_queries" in system:
            return json.dumps(
                {"sufficient": True, "additional_queries": [], "reason": "Демонстрационные правовые материалы получены."},
                ensure_ascii=False,
            )
        if "consistent" in system:
            return json.dumps(
                {"consistent": True, "issues": []},
                ensure_ascii=False,
            )
        if "queries" in system:
            topic = user.split("Вопрос:", 1)[-1].splitlines()[0].strip()
            return json.dumps({"queries": [topic, f"{topic}: нормы права", f"{topic}: последние изменения"]}, ensure_ascii=False)
        return (
            "## Краткий ответ\n\n"
            "Демонстрационный запуск проверяет цикл юридического помощника; реальный правовой вывод здесь не формируется [1].\n\n"
            "## Правовое основание\n\n"
            "В рабочем режиме агент должен указать точные нормы только при наличии их текста в источниках [1][2].\n\n"
            "## Актуальность и изменения\n\n"
            "Перед ответом выполняется отдельный поиск последних изменений; в демонстрации используются условные данные [1].\n\n"
            "## Как применить к ситуации\n\n"
            "Для реального ответа необходимо подключить модель и поисковый API, затем проверить нормы по ссылкам.\n\n"
            "## Ограничения\n\n"
            "Демонстрационные источники условны. Это не юридическая консультация."
        )


class DemoSearch:
    def search(self, query: str, limit: int) -> list[Source]:
        return [
            Source(
                title="Условный источник: устройство агента",
                url="https://example.org/demo-agent",
                text="Условный текст для проверки цикла: агент планирует поиск, собирает источники и создаёт отчёт.",
                query=query,
            ),
            Source(
                title="Условный источник: качество отчёта",
                url="https://example.org/demo-report",
                text="Условный текст для проверки ссылок: важные утверждения должны сопровождаться ссылками на источники.",
                query=query,
            ),
        ][:limit]


def deduplicate(sources: list[Source], limit: int) -> list[Source]:
    unique: dict[str, Source] = {}
    for source in sources:
        key = source.url.split("#", 1)[0].rstrip("/")
        if key not in unique:
            unique[key] = source
        if len(unique) >= limit:
            break
    return list(unique.values())


def classify_source_authority(url: str) -> str:
    """Classify source provenance using exact trusted hosts, not lookalike suffixes."""
    try:
        parsed = urlsplit(str(url))
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return "unclassified"
    if host in {"pravo.gov.ru", "www.pravo.gov.ru", "publication.pravo.gov.ru"}:
        return "official_legal_portal"
    if host in {"arbitr.ru", "www.arbitr.ru", "kad.arbitr.ru", "vsrf.ru", "www.vsrf.ru"} or host.endswith(".sudrf.ru"):
        return "official_court"
    if host in {"sudact.ru", "www.sudact.ru"}:
        return "case_law_aggregator"
    if host in {"consultant.ru", "www.consultant.ru", "garant.ru", "www.garant.ru"}:
        return "secondary_legal_reference"
    if host == "zpp.rospotrebnadzor.ru":
        return "government_legal_reproduction" if parsed.path.startswith("/npa/") else "official_legal_explanation"
    if host in {"epp.genproc.gov.ru", "rostrud.gov.ru", "www.rostrud.gov.ru"}:
        return "official_legal_explanation"
    if host.endswith(".gov.ru"):
        return "government_legal_reproduction"
    return "unclassified"


def source_authority_label(url: str) -> str:
    labels = {
        "official_legal_portal": "официальный портал правовой информации",
        "official_court": "официальный судебный сайт",
        "government_legal_reproduction": "ведомственная копия правового текста; не исходная публикация",
        "official_legal_explanation": "разъяснение государственного органа; не текст изменяющего акта",
        "case_law_aggregator": "агрегатор судебных актов",
        "secondary_legal_reference": "коммерческая правовая справочная система",
        "unclassified": "источник не классифицирован",
    }
    return labels[classify_source_authority(url)]


def format_materials(sources: list[Source]) -> str:
    materials = []
    for index, source in enumerate(sources, start=1):
        authority = source_authority_label(source.url)
        materials.append(
            f"[{index}] {source.title}\nURL: {source.url}\nПроисхождение: {authority}\n"
            f"Текст:\n{source.text[:9000]}"
        )
    return "\n\n---\n\n".join(materials)


def plan_queries(model: LanguageModel, topic: str, count: int) -> list[str]:
    response = model.complete(
        "Ты планируешь правовое исследование по законодательству РФ. Верни только JSON с ключом queries: "
        "список разнообразных запросов для поиска действующих норм, точных статей и официальных текстов. "
        "Не добавляй пояснений. Не полагайся на память модели о содержании законов.",
        f"Вопрос: {topic}\nКоличество поисковых запросов: {count}",
    )
    raw_queries = extract_json(response).get("queries", [])
    if not isinstance(raw_queries, list):
        raise AgentError("Список поисковых запросов имеет неверный формат.")
    queries = [item for item in raw_queries if isinstance(item, str)]
    result = deduplicate_queries([topic, *queries])[:count]
    if not result:
        raise AgentError("Не удалось составить поисковые запросы.")
    return result


def assess_sources(model: LanguageModel, topic: str, sources: list[Source]) -> tuple[bool, list[str], str]:
    response = model.complete(
        "Ты оцениваешь полноту исследования. Верни только JSON с полями: sufficient (boolean), "
        "additional_queries (список до 3 новых запросов), reason (краткое обоснование). "
        "Материалов достаточно, если они содержат применимые нормы, точные реквизиты и позволяют ответить на вопрос. "
        "Отдельно учитывай, нужны ли подзаконные акты или судебная практика. Если данных достаточно, additional_queries пуст. "
        "Не выдумывай нормы и факты.",
        f"Тема: {topic}\n\nМатериалы:\n\n{format_materials(sources)}",
    )
    data = extract_json(response)
    sufficient = data.get("sufficient")
    if not isinstance(sufficient, bool):
        raise AgentError("Оценка полноты исследования имеет неверный формат.")
    raw_queries = data.get("additional_queries", [])
    if not isinstance(raw_queries, list):
        raise AgentError("Дополнительные запросы имеют неверный формат.")
    queries = [str(item).strip() for item in raw_queries if str(item).strip()]
    return sufficient, list(dict.fromkeys(queries))[:3], str(data.get("reason", "")).strip()


def plan_legal_update_check(model: LanguageModel, question: str, sources: list[Source], checked_on: str) -> tuple[list[str], str]:
    """Всегда готовит отдельный поиск поправок и новых редакций."""
    prompt = (
        "Ты проверяешь актуальность правовых норм РФ. Верни только JSON с полями legal_update_queries "
        "(список от 1 до 3 запросов) и notes. Запросы должны искать последние изменения, новые редакции, "
        "даты вступления в силу и признание норм утратившими силу. Не утверждай, что норма действует, без источника."
    )
    user = f"Вопрос: {question}\nДата применимости норм: {checked_on}\n\nМатериалы:\n\n{format_materials(sources)}"
    for attempt in range(2):
        try:
            data = extract_json(model.complete(prompt, user))
            break
        except AgentError:
            if attempt:
                raise
            time.sleep(1)
    raw_queries = data.get("legal_update_queries", data.get("queries", []))
    if not isinstance(raw_queries, list):
        raise AgentError("Запросы для проверки изменений имеют неверный формат.")
    queries = [item for item in raw_queries if isinstance(item, str)]
    fallback = f"{question} последние изменения действующая редакция {checked_on[:4]}"
    return deduplicate_queries([*queries, fallback])[:3], str(data.get("notes", "")).strip()


def extract_provision_history(
    model: LanguageModel, question: str, sources: list[Source]
) -> dict[str, Any]:
    """Extract candidate effective-date events, then verify every quote against source text."""
    from legal_history import build_provision_timeline

    official_sources = [
        source for source in sources
        if (urlsplit(source.url).hostname or "").lower() in {
            "publication.pravo.gov.ru", "pravo.gov.ru", "www.pravo.gov.ru"
        }
        and source.url.startswith("https://")
    ]
    if not official_sources:
        return {
            "status": "no_official_evidence", "verified": False, "periods": [],
            "limitation": "Нет официальных публикаций для извлечения дат вступления конкретной нормы в силу.",
        }
    prompt = (
        "Извлеки из переданных ОФИЦИАЛЬНЫХ источников только явно подтверждённые события изменения "
        "одной конкретной нормы РФ. Верни JSON: provision_id (точное обозначение акта, статьи, части, "
        "пункта), provision_source_url и provision_quote (дословная цитата, подтверждающая именно эту "
        "норму), events (массив объектов: effective_from в YYYY-MM-DD, act_title, act_number, "
        "official_publication_id, source_url, effective_date_quote). Не используй дату принятия, "
        "подписания, опубликования или дату редакции вместо даты вступления в силу. Учитывай специальные "
        "переходные положения. Каждая цитата должна дословно присутствовать в одном из источников и "
        "содержать указанную дату. Не выводи события по косвенным данным. Если область нормы или дата "
        "неясна, верни пустой массив. Не утверждай полноту истории изменений."
    )
    material = format_materials(official_sources[:8])
    try:
        data = extract_json(model.complete(prompt, f"Вопрос: {question}\n\nОфициальные материалы:\n{material}"))
    except AgentError as exc:
        return {
            "status": "extraction_failed", "verified": False, "periods": [],
            "errors": [str(exc)],
        }
    provision_id = str(data.get("provision_id") or "").strip()
    provision_source = next(
        (source for source in official_sources if source.url == data.get("provision_source_url")),
        None,
    )
    provision_quote = str(data.get("provision_quote") or "").strip()
    if provision_source is None or not provision_quote or provision_quote not in provision_source.text:
        return {
            "status": "provision_identity_not_source_verified", "verified": False, "periods": [],
            "limitation": "Не удалось дословно подтвердить идентичность статьи/части/пункта в официальном источнике.",
        }
    raw_events = data.get("events", [])
    if not isinstance(raw_events, list):
        raw_events = []
    by_url = {source.url: source for source in official_sources}
    validated_events: list[dict[str, Any]] = []
    rejected: list[str] = []
    for index, event in enumerate(raw_events[:20]):
        if not isinstance(event, dict):
            rejected.append(f"Событие {index + 1}: неверный формат.")
            continue
        source = by_url.get(event.get("source_url"))
        if source is None:
            rejected.append(f"Событие {index + 1}: ссылка не совпадает с полученным официальным источником.")
            continue
        quote = str(event.get("effective_date_quote") or "").strip()
        publication_id = str(event.get("official_publication_id") or "").strip()
        act_title = str(event.get("act_title") or "").strip()
        act_number = str(event.get("act_number") or "").strip()
        if not quote or quote not in source.text:
            rejected.append(f"Событие {index + 1}: цитата даты не найдена дословно в источнике.")
            continue
        if not publication_id or publication_id not in source.text:
            rejected.append(f"Событие {index + 1}: идентификатор публикации не подтверждён текстом источника.")
            continue
        if not act_title or act_title not in source.text or not act_number or act_number not in source.text:
            rejected.append(f"Событие {index + 1}: реквизиты изменяющего акта не подтверждены текстом.")
            continue
        validated_events.append({
            "provision_id": provision_id,
            "effective_from": event.get("effective_from"),
            "act_title": act_title,
            "act_number": act_number,
            "official_publication_id": publication_id,
            "official_url": source.url,
            "effective_date_quote": quote,
        })
    result = build_provision_timeline(provision_id, validated_events, timeline_complete=False)
    result["events_rejected"] = rejected
    result["verified"] = False
    result["timeline_complete"] = False
    if result.get("periods"):
        result["status"] = "candidate_timeline_incomplete"
        result["limitation"] = (
            "Периоды рассчитаны только по найденным и дословно подтверждённым событиям. "
            "Полнота истории поправок и переходных положений не установлена, поэтому результат не является "
            "подтверждением юридического периода действия нормы."
        )
    return result



def extract_amendment_chain_evidence(
    model: LanguageModel,
    changed_provisions: list[dict[str, Any]],
    sources: list[Source],
) -> dict[str, Any]:
    """Use the model only to propose exact quotations; validate them deterministically."""
    from legal_history import audit_amendment_links

    official_sources = [
        source for source in sources
        if (urlsplit(source.url).hostname or "").lower() == "publication.pravo.gov.ru"
        and source.url.startswith("https://")
    ]
    if not changed_provisions:
        return {
            "status": "no_changed_provisions", "links": [], "link_count": 0,
            "verified": False, "coverage_verified": False,
        }
    if not official_sources:
        return {
            "status": "no_official_amendment_publications", "links": [],
            "link_count": 0, "verified": False, "coverage_verified": False,
        }

    prompt = (
        "Ты сопоставляешь изменения текста статей с официально опубликованными изменяющими "
        "федеральными законами РФ. Верни только JSON {candidates:[...]}. Для каждой связи "
        "укажи provision_id строго из списка; act_title, act_number, act_signed_date (YYYY-MM-DD), official_publication_id, "
        "source_url строго из переданных материалов; amendment_quote — ДОСЛОВНУЮ цитату "
        "из текста изменяющего закона, где прямо указано внесение изменения именно в эту "
        "статью исходного закона; target_act_quote — дословную часть той же цитаты, "
        "идентифицирующую исходный закон (название/дату/номер); transition_quote — ДОСЛОВНУЮ цитату переходного/заключительного "
        "положения этого же закона, если оно есть. effective_from и effective_date_quote указывай "
        "только если переходное положение прямо задаёт дату вступления в силу изменения именно "
        "этой нормы; не используй общую дату закона, дату публикации или дату снимка. Если связь "
        "неочевидна, не возвращай её. Не выдумывай цитаты и реквизиты."
    )
    payload = {
        "changed_provisions": [
            {
                "provision_id": item.get("provision_id"),
                "from_snapshot_date": item.get("from_snapshot_date"),
                "to_snapshot_date": item.get("to_snapshot_date"),
                "before_excerpt": str(item.get("before_excerpt") or "")[:2500],
                "after_excerpt": str(item.get("after_excerpt") or "")[:2500],
            }
            for item in changed_provisions[:40] if isinstance(item, dict)
        ],
        "official_publications": [
            {"title": source.title, "url": source.url, "text": source.text[:9000]}
            for source in official_sources[:12]
        ],
    }
    try:
        data = extract_json(model.complete(prompt, json.dumps(payload, ensure_ascii=False)))
    except AgentError as exc:
        return {
            "status": "extraction_failed", "links": [], "link_count": 0,
            "verified": False, "coverage_verified": False, "errors": [str(exc)],
        }
    raw = data.get("candidates", [])
    if not isinstance(raw, list):
        raw = []
    by_url = {source.url: source for source in official_sources}
    candidates: list[dict[str, Any]] = []
    for item in raw[:100]:
        if not isinstance(item, dict):
            continue
        source = by_url.get(item.get("source_url"))
        if source is None:
            continue
        candidates.append({
            **item,
            "source_text": source.text,
        })
    return audit_amendment_links(changed_provisions, candidates)


def plan_court_practice_check(model: LanguageModel, question: str, checked_on: str) -> tuple[list[str], str]:
    """Готовит отдельный поиск судебной практики по вопросу."""
    response = model.complete(
        "Ты подбираешь судебную практику РФ. Верни только JSON с полями court_queries "
        "(список от 1 до 3 запросов) и notes. Запросы должны искать решения арбитражных судов и "
        "судов общей юрисдикции, позиции Верховного Суда РФ и обзоры практики по теме вопроса. "
        "Не выдумывай номера дел и не утверждай, что практика единообразна, без источника.",
        f"Вопрос: {question}\nДата применимости норм: {checked_on}",
    )
    data = extract_json(response)
    raw_queries = data.get("court_queries", data.get("queries", []))
    if not isinstance(raw_queries, list):
        raise AgentError("Запросы для поиска судебной практики имеют неверный формат.")
    queries = [item for item in raw_queries if isinstance(item, str)]
    fallback = f"{question} судебная практика {checked_on[:4]}"
    return deduplicate_queries([*queries, fallback])[:3], str(data.get("notes", "")).strip()


def build_report(model: LanguageModel, topic: str, sources: list[Source], session_context: str = "") -> str:
    context_block = (
        "\n\nКонтекст предыдущих вопросов этой сессии (учитывай, если вопрос продолжает их):\n" + session_context
        if session_context
        else ""
    )
    return model.complete(
        "Ты юридический помощник по законодательству РФ. Пиши по-русски простыми словами. "
        "Используй только переданные материалы. Каждую статью связывай с конкретным кодексом или законом; одного номера статьи недостаточно. "
        "Не называй редакцию действующей, если источник не подтверждает статус и дату вступления изменений в силу. "
        "Различай официальный первичный источник, судебный сайт, агрегатор судебных актов и коммерческую справочную систему. "
        "Не называй единичное судебное решение устойчивой практикой; указывай суд, дату и номер дела только если они есть в источнике. "
        "Каждое правовое утверждение сопровождай ссылкой [N] в том же предложении. В одном предложении указывай один закон или кодекс; несколько законов разделяй на отдельные предложения с собственными ссылками. Указанный источник должен содержать именно заявленную статью этого акта. Отдельно укажи, какие изменения проверены и что подтвердить не удалось. "
        "Не выдавай ответ за индивидуальную юридическую консультацию. Не добавляй раздел источников. "
        "Используй разделы: Краткий ответ, Правовое основание, Актуальность и изменения, Как применить к ситуации, Ограничения.",
        f"Правовой вопрос: {topic}{context_block}\n\nМатериалы:\n\n{format_materials(sources)}",
    )


def section_text(report: str, section: str) -> str:
    match = re.search(
        rf"^##\s+{re.escape(section)}\s*$\n(.*?)(?=^##\s+|\Z)",
        report,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


ARTICLE_PATTERN = re.compile(r"(?<!\w)(?:статья|статье|статьи|ст\.)\s*(\d+(?:\.\d+)?)(?!\d|\.\d)", re.IGNORECASE)
ARTICLE_MENTION_PATTERN = re.compile(r"(?<!\w)(?:статья|статье|статьи|ст\.)\s*(\d+(?:\.\d+)?)(?!\d|\.\d)", re.IGNORECASE)
ACT_ALIASES: dict[str, re.Pattern[str]] = {
    "ГК РФ": re.compile(r"\b(?:ГК\s*РФ|Гражданск(?:ий|ого)\s+кодекс(?:\s+Российской Федерации|\s+РФ)?)\b", re.IGNORECASE),
    "ТК РФ": re.compile(r"\b(?:ТК\s*РФ|Трудов(?:ой|ого)\s+кодекс(?:\s+Российской Федерации|\s+РФ)?)\b", re.IGNORECASE),
    "НК РФ": re.compile(r"\b(?:НК\s*РФ|Налогов(?:ый|ого)\s+кодекс(?:\s+Российской Федерации|\s+РФ)?)\b", re.IGNORECASE),
    "УК РФ": re.compile(r"\b(?:УК\s*РФ|Уголовн(?:ый|ого)\s+кодекс(?:\s+Российской Федерации|\s+РФ)?)\b", re.IGNORECASE),
    "КоАП РФ": re.compile(r"\b(?:КоАП\s*РФ|Кодекс(?:а)?\s+Российской Федерации\s+об\s+административных\s+правонарушениях)\b", re.IGNORECASE),
    "АПК РФ": re.compile(r"\b(?:АПК\s*РФ|Арбитражн(?:ый|ого)\s+процессуальн(?:ый|ого)\s+кодекс(?:\s+Российской Федерации|\s+РФ)?)\b", re.IGNORECASE),
    "ГПК РФ": re.compile(r"\b(?:ГПК\s*РФ|Гражданск(?:ий|ого)\s+процессуальн(?:ый|ого)\s+кодекс(?:\s+Российской Федерации|\s+РФ)?)\b", re.IGNORECASE),
    "ЖК РФ": re.compile(r"\b(?:ЖК\s*РФ|Жилищн(?:ый|ого)\s+кодекс(?:\s+Российской Федерации|\s+РФ)?)\b", re.IGNORECASE),
    "СК РФ": re.compile(r"\b(?:СК\s*РФ|Семейн(?:ый|ого)\s+кодекс(?:\s+Российской Федерации|\s+РФ)?)\b", re.IGNORECASE),
    "ЗК РФ": re.compile(r"\b(?:ЗК\s*РФ|Земельн(?:ый|ого)\s+кодекс(?:\s+Российской Федерации|\s+РФ)?)\b", re.IGNORECASE),
    "БК РФ": re.compile(r"\b(?:БК\s*РФ|Бюджетн(?:ый|ого)\s+кодекс(?:\s+Российской Федерации|\s+РФ)?)\b", re.IGNORECASE),
    "КАС РФ": re.compile(r"\b(?:КАС\s*РФ|Кодекс административного судопроизводства Российской Федерации)\b", re.IGNORECASE),
}
FEDERAL_LAW_ID_PATTERN = re.compile(r"(?<!\d)(\d{1,4})\s*[-–—]\s*ФЗ\b", re.IGNORECASE)


def _article_sort_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _act_ids(text: str) -> set[str]:
    identifiers = {name for name, pattern in ACT_ALIASES.items() if pattern.search(text)}
    identifiers.update(f"{match.group(1)}-ФЗ" for match in FEDERAL_LAW_ID_PATTERN.finditer(text))
    return identifiers


def _citation_segments(text: str):
    """Bound citations to a sentence/clause; preserve decimal numbers and abbreviations."""
    # Paragraphs, headings and list entries must not lend citations to each other.
    blocks = re.split(r'\n\s*\n|\n(?=\s*(?:##|[-*]\s|\d+[.)]\s))', text)
    abbreviations = {'ст', 'ч', 'п', 'подп', 'г', 'ред', 'им', 'т', 'д', 'тд', 'тп'}
    attached_citations = re.compile(r'[»”"\)]*\s*(?:\[\d+\]\s*)+')
    for block in blocks:
        start, index = 0, 0
        while index < len(block):
            char = block[index]
            boundary = char in '!?;'
            if char == '.':
                word = re.search(r'([А-Яа-яA-Za-z]+)$', block[max(0, index - 16):index])
                decimal = index > 0 and index + 1 < len(block) and block[index - 1].isdigit() and block[index + 1].isdigit()
                boundary = not decimal and not (word and word.group(1).casefold() in abbreviations)
            if boundary:
                end = index + 1
                attached = attached_citations.match(block, end)
                if attached:
                    end = attached.end()
                yield block[start:end]
                start, index = end, end
            else:
                index += 1
        if start < len(block):
            yield block[start:]


def _source_article_pairs(source: Source) -> set[tuple[str, str]]:
    title_acts = _act_ids(source.title)
    supported = set()
    for segment in _citation_segments(source.text):
        acts = _act_ids(segment) or title_acts
        if len(acts) == 1:
            supported.update((number, next(iter(acts))) for number in ARTICLE_PATTERN.findall(segment))
    return supported


def find_unconfirmed_articles(report: str, sources: list[Source]) -> list[str]:
    """Check article/act identity only against the citations in that claim's segment."""
    unconfirmed: set[str] = set()
    source_indexes = [_source_article_pairs(source) for source in sources]
    for segment in _citation_segments(report):
        act_ids = _act_ids(segment)
        citations = [int(value) for value in re.findall(r'\[(\d+)\]', segment)]
        cited = [source_indexes[index - 1] for index in citations if 1 <= index <= len(sources)]
        for match in ARTICLE_MENTION_PATTERN.finditer(segment):
            number = match.group(1)
            if not act_ids:
                unconfirmed.add(f"{number} (не указан конкретный закон или кодекс)")
            elif len(act_ids) != 1:
                unconfirmed.add(f"{number} (неоднозначная связь с законом: {', '.join(sorted(act_ids))})")
            elif not any((number, next(iter(act_ids))) in pairs for pairs in cited):
                unconfirmed.add(f"{number} ({', '.join(sorted(act_ids))})")
    return sorted(unconfirmed, key=lambda value: _article_sort_key(value.split(" ", 1)[0]))


def validate_report(report: str, sources: list[Source]) -> list[str]:
    """Проверяет структуру, наличие цитат и допустимость ссылок."""
    issues: list[str] = []
    for section in REQUIRED_SECTIONS:
        if not section_text(report, section):
            issues.append(f"Отсутствует или пуст раздел «{section}».")
    for section in CITATION_SECTIONS:
        text = section_text(report, section)
        if text and not re.search(r"\[\d+\]", text):
            issues.append(f"В разделе «{section}» нет ссылки на источник.")
    citations = {int(value) for value in re.findall(r"\[(\d+)\]", report)}
    invalid = sorted(number for number in citations if not 1 <= number <= len(sources))
    if invalid:
        issues.append(f"Есть ссылки на несуществующие источники: {invalid}.")
    allowed_urls = {source.url for source in sources}
    external_links = set(re.findall(r"\[[^\]]+\]\((https?://[^)]+)\)", report))
    unknown_links = sorted(external_links - allowed_urls)
    if unknown_links:
        issues.append(f"Есть ссылки, которых нет в материалах: {unknown_links}.")
    missing_articles = find_unconfirmed_articles(report, sources)
    if missing_articles:
        issues.append(f"Упомянуты статьи без подтверждения в указанных для утверждения источниках: {missing_articles}. Укажите один закон на предложение и ссылку [N] в том же предложении.")
    return issues


def build_validated_report(model: LanguageModel, topic: str, sources: list[Source], session_context: str = "") -> tuple[str, bool]:
    """Создаёт отчёт и при необходимости выполняет одно исправление."""
    report = build_report(model, topic, sources, session_context)
    issues = validate_report(report, sources)
    if not issues:
        return report, False
    repaired = model.complete(
        "Ты исправляешь исследовательский отчёт. Используй только переданные материалы, не добавляй новых фактов. "
        "Устрани все перечисленные проблемы, сохрани русский язык и формат Markdown. "
        "Не добавляй итоговый раздел источников. Верни только исправленный отчёт.",
        f"Тема: {topic}\n\nПроблемы:\n- " + "\n- ".join(issues)
        + f"\n\nОтчёт:\n{report}\n\nМатериалы:\n\n{format_materials(sources)}",
    )
    remaining = validate_report(repaired, sources)
    if remaining:
        raise AgentError(f"Отчёт не прошёл проверку после исправления. Нарушений: {len(remaining)}.")
    return repaired, True


def reflect_on_report(model: LanguageModel, topic: str, report: str, sources: list[Source]) -> tuple[str, dict[str, Any]]:
    """Самопроверка готового ответа: модель ищет противоречия и неподтверждённые выводы.

    Этап вспомогательный: при сбое самой самопроверки исходный ответ сохраняется,
    а запись в журнале помечается performed=False (как этап судебной практики).
    """
    response = extract_json(
        model.complete(
            "Ты проверяешь готовый юридический ответ по законодательству РФ. Ищи только реальные проблемы: "
            "противоречия между разделами, выводы, которые не следуют из правовых оснований, "
            "утверждения без ссылки на источник там, где она нужна. "
            'Верни только JSON: {"consistent": true|false, "issues": ["краткое описание проблемы"]}. '
            "Если проблем нет, верни пустой список issues и consistent=true. Не придумывай проблемы.",
            f"Вопрос: {topic}\n\nОтвет:\n{report}\n\nМатериалы:\n\n{format_materials(sources)}",
        )
    )
    raw_issues = response.get("issues", [])
    if not isinstance(raw_issues, list) or any(not isinstance(issue, str) for issue in raw_issues):
        raise AgentError("Самопроверка вернула список замечаний неверного формата.")
    issues = [issue.strip() for issue in raw_issues if issue.strip()]
    consistent_value = response.get("consistent", not issues)
    if not isinstance(consistent_value, bool):
        raise AgentError("Самопроверка вернула поле consistent неверного типа.")
    consistent = consistent_value and not issues
    journal: dict[str, Any] = {"performed": True, "consistent": consistent, "issues": issues, "repaired": False}
    if consistent:
        return report, journal
    repaired = model.complete(
        "Ты исправляешь юридический ответ по замечаниям самопроверки. Используй только переданные материалы, "
        "не добавляй новых фактов. Устрани перечисленные противоречия, сохрани русский язык, формат Markdown "
        "и обязательные разделы. Не добавляй итоговый раздел источников. Верни только исправленный ответ.",
        f"Вопрос: {topic}\n\nЗамечания:\n- " + "\n- ".join(issues)
        + f"\n\nОтвет:\n{report}\n\nМатериалы:\n\n{format_materials(sources)}",
    )
    validation = validate_report(repaired, sources)
    if validation:
        raise AgentError(f"Ответ после самопроверки не прошёл структурную проверку. Нарушений: {len(validation)}.")
    journal["repaired"] = True
    return repaired, journal


def search_queries(search: SearchProvider, queries: list[str], per_query: int, *, verbose: bool = True) -> tuple[list[Source], list[str]]:
    found: list[Source] = []
    errors: list[str] = []
    for query in queries:
        try:
            found.extend(search.search(query, per_query))
            for provider_error in getattr(search, "last_errors", []):
                errors.append(f"{query}: {provider_error}")
                if verbose:
                    print(f"      Предупреждение: {provider_error}", file=sys.stderr)
        except AgentError as exc:
            errors.append(f"{query}: {exc}")
            if verbose:
                print(f"      Предупреждение: {exc}", file=sys.stderr)
    return found, errors


def evidence_summary(update_check: dict[str, Any], court_check: dict[str, Any], demo: bool) -> dict[str, Any]:
    """Expose research evidence without promoting retrieval to legal verification."""
    def stage(check: dict[str, Any]) -> dict[str, Any]:
        if demo:
            status = "demo"
        elif check.get("performed") is True:
            status = "evidence_found_unverified"
        elif check.get("errors") or check.get("status") in {"unavailable", "search_error", "planning_failed"}:
            status = "unavailable"
        elif check.get("attempted") is True:
            status = "no_evidence"
        else:
            status = "not_attempted"
        return {"status": status, "verified": False, "error_count": len(check.get("errors") or [])}

    return {
        "status": "demo" if demo else "not_legally_verified",
        "verified": False,
        "amendments": stage(update_check),
        "official_publications": stage(update_check.get("official_portal", {})),
        "revision_snapshots": stage(update_check.get("integrated_legal_bank", {})),
        "court_practice": stage(court_check),
        "limitation": "Найденные материалы и корректные ссылки не подтверждают полноту истории, период действия или применимость нормы.",
    }


def save_results(
    output_dir: Path,
    topic: str,
    queries: list[str],
    sources: list[Source],
    report: str,
    demo: bool,
    research_log: list[dict[str, Any]],
    report_repaired: bool,
    update_check: dict[str, Any],
    court_check: dict[str, Any],
    reflection: dict[str, Any],
    checked_on: str,
    as_of: str | None = None,
) -> tuple[Path, Path]:
    evidence = evidence_summary(update_check, court_check, demo)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    report_path = output_dir / f"ответ-{stamp}.md"
    run_path = output_dir / f"журнал-{stamp}.json"
    source_list = "\n".join(
        f"{i}. [{s.title[:500]}]({s.url[:2000]}) — {source_authority_label(s.url)}"
        for i, s in enumerate(sources, start=1)
    )
    journal_sources = [
        {**asdict(source), "title": source.title[:500], "url": source.url[:2000],
         "query": source.query[:1000], "text": source.text[:9000],
         **({'retrieval_mode': 'local_statutory_extract', 'period_supported': False,
             'original_publication': False, 'verified': False}
            if source.query == 'local statutory extract' else {}),
         "authority_class": classify_source_authority(source.url)}
        for source in sources[:16]
    ]
    warning = "\n> **Демонстрационный режим:** источники и факты условны.\n" if demo else ""
    update_status = "подтверждена" if update_check.get("verified") is True else "не подтверждена автоматически"
    court_status = "подтверждена" if court_check.get("verified") is True else "не подтверждена автоматически"
    portal_check = update_check.get("official_portal", {})
    portal_status = portal_check.get("status", "not_configured")
    portal_count = portal_check.get("records_with_text", 0)
    verification_note = (
        "\n## Статус юридической проверки\n\n"
        f"- Актуальность редакций: {update_status} (статус поиска: {update_check.get('status', 'unknown')}).\n"
        f"- Официальный портал правовой информации: {portal_status}; публикаций с полученным текстом: {portal_count}.\n"
        f"- Эталонный банк «Законодательство России»: {update_check.get('integrated_legal_bank', {}).get('status', 'not_configured')}; документов с текстом: {update_check.get('integrated_legal_bank', {}).get('documents_with_text', 0)}.\n"
        f"- Судебная практика: {court_status} (статус поиска: {court_check.get('status', 'unknown')}).\n"
        "- Найденные материалы сами по себе не доказывают, что норма действует на дату проверки или что судебная позиция является устойчивой. "
        "Проверяйте дату вступления изменений в силу и полный текст первичного источника.\n"
    )
    report_path.write_text(
        f"# {topic}\n\n**Дата проверки изменений:** {checked_on}\n\n**Дата правовой ситуации:** {as_of or checked_on}\n\n> {LEGAL_DISCLAIMER}\n{warning}\n{report.strip()}\n{verification_note}\n## Источники\n\n{source_list}\n",
        encoding="utf-8",
    )
    run_path.write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "topic": topic,
                "demo": demo,
                "queries": queries,
                "sources": journal_sources,
                "research_rounds": research_log,
                "report_validated": True,
                "report_repaired": report_repaired,
                "evidence": evidence,
                "legal_update_check": update_check,
                "court_practice_check": court_check,
                "self_reflection": reflection,
                "checked_on": checked_on,
                "as_of": as_of or checked_on,
                "report_file": report_path.name,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report_path, run_path


def validate_as_of(value: str | None) -> str | None:
    """Validate an explicit legal situation date without silently normalizing it."""
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("as_of должна быть датой в формате ГГГГ-ММ-ДД.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("as_of содержит несуществующую дату.") from None
    return value


def run(topic: str, query_count: int, source_limit: int, output_dir: Path, demo: bool = False, session_context: str = "", as_of: str | None = None, verbose: bool = True) -> tuple[Path, Path]:
    log = print if verbose else lambda *args, **kwargs: None
    checked_on = current_check_date()
    legal_as_of = validate_as_of(as_of) or checked_on
    original_topic = topic
    if not demo and os.getenv("ENABLE_UNREVIEWED_STATUTE_CORPUS", "").lower() == "true":
        raise AgentError("Непроверенный локальный корпус законодательства отключён до аудита происхождения и лицензий.")
    if as_of is not None:
        topic = (f"{topic}\nДата правовой ситуации: {legal_as_of}. "
                 "Определи применимую норму на эту дату; отличай её от даты проверки. "
                 "Позднейшие поправки не считай действовавшими тогда без подтверждения обратной силы.")
    if demo:
        model: LanguageModel = DemoModel()
        search: SearchProvider = DemoSearch()
        portal_search = None
        ips_bank = None
        court_search: SearchProvider = DemoSearch()
    else:
        from pravo_portal import FederatedLegalSearch, PravoPortalSearch
        from pravo_ips import PravoIpsBank

        model = OpenAICompatibleModel()
        tavily_search = TavilySearch()
        portal_search = PravoPortalSearch()
        ips_bank = PravoIpsBank()
        search = FederatedLegalSearch(portal_search, tavily_search)
        court_domains = [
            item.strip()
            for item in os.getenv(
                "COURT_SEARCH_DOMAINS", "kad.arbitr.ru,vsrf.ru"
            ).split(",")
            if item.strip()
        ]
        court_search = TavilySearch(domains=court_domains)

    log("[1/8] Планирую правовое исследование…")
    queries = plan_queries(model, topic, query_count)
    executed_queries = {_normalized_query_key(query) for query in queries}
    log(f"      Поисковых запросов: {len(queries)} (текст скрыт для защиты данных)")

    log("[2/8] Ищу нормы и читаю источники…")
    per_query = max(2, min(5, source_limit))
    found, errors = search_queries(search, queries, per_query, verbose=verbose)
    sources = deduplicate(found, source_limit)
    if not sources:
        details = "\n".join(errors)
        raise AgentError("Не найдено пригодных источников." + (f"\n{details}" if details else ""))
    log(f"      Выбрано источников: {len(sources)}")

    log("[3/8] Оцениваю полноту правовых материалов…")
    research_log: list[dict[str, Any]] = []
    additional_round = 0
    while True:
        sufficient, additional_queries, reason = assess_sources(model, topic, sources)
        research_log.append(
            {
                "round": additional_round,
                "sufficient": sufficient,
                "reason": reason,
                "additional_queries": additional_queries,
                "source_count": len(sources),
                "errors": errors,
            }
        )
        log(f"      {reason or ('Материалов достаточно.' if sufficient else 'Требуются дополнительные источники.')}")
        new_queries = [query for query in deduplicate_queries(additional_queries) if _normalized_query_key(query) not in executed_queries]
        if sufficient or additional_round >= 2 or len(sources) >= source_limit or not new_queries:
            if not sufficient and additional_round >= 2:
                log("      Достигнут лимит дополнительного поиска; продолжаю с доступными материалами.")
            break
        additional_round += 1
        queries.extend(new_queries)
        executed_queries.update(_normalized_query_key(query) for query in new_queries)
        log(f"      Дополнительный поиск {additional_round}/2: " + "; ".join(new_queries))
        found, round_errors = search_queries(search, new_queries, per_query, verbose=verbose)
        errors.extend(round_errors)
        sources = deduplicate([*sources, *found], source_limit)
        log(f"      Выбрано источников: {len(sources)}")

    log("[4/8] Обязательно проверяю последние изменения…")
    update_queries, update_notes = plan_legal_update_check(model, topic, sources, legal_as_of)
    new_update_queries = [query for query in deduplicate_queries(update_queries) if _normalized_query_key(query) not in executed_queries]
    if not new_update_queries:
        new_update_queries = [f"{topic} изменения действующая редакция {legal_as_of[:4]}"]
    queries.extend(new_update_queries)
    executed_queries.update(_normalized_query_key(query) for query in new_update_queries)
    update_found, update_errors = search_queries(search, new_update_queries, per_query, verbose=verbose)
    # The integrated full-text bank is a separate official source from the
    # publication feed. Search it independently before assembling update_sources.
    ips_sources: list[Source] = []
    ips_errors: list[str] = []
    if ips_bank is not None:
        for ips_query in new_update_queries[:2]:
            try:
                ips_sources.extend(ips_bank.search(ips_query, 2, as_of=legal_as_of))
            except AgentError as exc:
                ips_errors.append(str(exc))
    ips_sources = deduplicate(ips_sources, 2)

    # Read the bounded revision audit embedded by the IPS connector.
    ips_revision_audits: list[dict[str, Any]] = []
    audit_marker = "Диагностика цепочки редакций: "
    for ips_source in ips_sources:
        marker_position = ips_source.text.find(audit_marker)
        if marker_position < 0:
            continue
        raw_audit = ips_source.text[marker_position + len(audit_marker):]
        try:
            audit, _ = json.JSONDecoder().raw_decode(raw_audit)
        except (TypeError, ValueError):
            continue
        if isinstance(audit, dict):
            ips_revision_audits.append(audit)

    changed_provisions = [
        item
        for audit in ips_revision_audits
        for item in audit.get("changed_provisions", [])
        if isinstance(item, dict)
    ]

    # Amendment citations printed in consolidated text are search leads only.
    # Retrieve candidate acts separately from the official publication portal.
    from legal_history import extract_amendment_references
    amendment_refs: dict[tuple[str, str], dict[str, str]] = {}
    for change in changed_provisions:
        excerpt = (
            str(change.get("before_excerpt") or "")
            + "\\n"
            + str(change.get("after_excerpt") or "")
        )
        for reference in extract_amendment_references(excerpt):
            amendment_refs[(reference["signed_date"], reference["act_number"])] = reference

    amendment_sources: list[Source] = []
    amendment_errors: list[str] = []
    if portal_search is not None:
        for reference in list(amendment_refs.values())[:8]:
            try:
                amendment_sources.extend(portal_search.search_act(reference["signed_date"], reference["act_number"]))
            except AgentError as exc:
                amendment_errors.append(str(exc))
    amendment_sources = deduplicate(amendment_sources, 12)

    update_sources = deduplicate([*update_found, *ips_sources, *amendment_sources], 15)
    portal_update_sources = [
        source for source in update_sources
        if (urlsplit(source.url).hostname or "").lower() == "publication.pravo.gov.ru"
    ]
    portal_errors = [
        error for error in update_errors if "PravoPortalSearch" in error
    ]

    try:
        provision_history = extract_provision_history(model, topic, update_sources)
    except Exception as exc:
        provision_history = {
            "status": "extraction_failed", "verified": False, "periods": [],
            "errors": [str(exc)],
        }

    from legal_history import select_provision_period
    provision_history["selection"] = select_provision_period(provision_history, legal_as_of)

    amendment_chain_audit = extract_amendment_chain_evidence(
        model, changed_provisions, amendment_sources
    )
    from legal_history import audit_amendment_retrieval
    amendment_retrieval_audit = audit_amendment_retrieval(
        list(amendment_refs.values()),
        [
            {"title": source.title, "url": source.url, "text": source.text}
            for source in amendment_sources
        ],
        query_limit=8,
    )
    amendment_chain_audit["candidate_references_found"] = len(amendment_refs)
    amendment_chain_audit["official_publications_retrieved"] = len(amendment_sources)
    amendment_chain_audit["retrieval_coverage"] = amendment_retrieval_audit
    amendment_chain_audit["retrieval_errors"] = amendment_errors

    # For the 152-FZ control case, independently parse the literal scope
    # instructions in every retrieved official amendment publication. This is
    # a candidate map only: no scope or completeness claim is inferred from
    # consolidated-text citations or secondary sources.
    from legal_history import extract_target_law_amendment_scope
    target_law_scope_audits: list[dict[str, Any]] = []
    for amendment_source in amendment_sources[:12]:
        scope_audit = extract_target_law_amendment_scope(
            amendment_source.text, target_law_number="152-ФЗ"
        )
        if scope_audit.get("target_law_found"):
            target_law_scope_audits.append({
                "act_title": amendment_source.title,
                "source_url": amendment_source.url,
                **scope_audit,
            })
    amendment_chain_audit["target_law_scope_audit"] = {
        "target_law_number": "152-ФЗ",
        "status": (
            "scope_candidates_found" if target_law_scope_audits
            else "no_scoped_official_amendment_texts"
        ),
        "official_texts_examined": len(amendment_sources[:12]),
        "acts_with_target_law_mention": len(target_law_scope_audits),
        "candidate_items": sum(
            len(item.get("items", [])) for item in target_law_scope_audits
        ),
        "acts": target_law_scope_audits,
        "verified": False,
        "coverage_verified": False,
        "limitation": (
            "Область поправок извлечена только из буквальных инструкций в полученных "
            "официальных текстах. Неполученные акты, переходные положения и полнота архива "
            "не покрыты; отсутствие кандидатов не означает отсутствия поправок."
        ),
    }

    ips_check = {
        "attempted": ips_bank is not None,
        "performed": bool(ips_sources),
        "verified": False,
        "status": (
            "revision_snapshots_found" if ips_sources
            else "unavailable" if ips_errors
            else "no_matching_document"
        ) if ips_bank is not None else "demo_not_configured",
        "documents_with_text": len(ips_sources),
        "revision_chain_audits": ips_revision_audits,
        "errors": ips_errors,
        "limitation": (
            "Банк предоставляет официальные тексты и снимки редакций. Дата редакции не равна "
            "дате вступления в силу каждой нормы; периоды действия положений автоматически не подтверждены."
        ),
    }
    official_portal_check = {
        "attempted": portal_search is not None,
        "performed": bool(portal_update_sources),
        "verified": False,
        "status": (
            "official_publications_found"
            if portal_update_sources
            else "unavailable" if portal_errors
            else "no_matching_publication"
        ) if portal_search is not None else "demo_not_configured",
        "records_with_text": len(portal_update_sources),
        "note": (
            "Официально опубликованные акты получены через публичный read-only интерфейс. "
            "Это подтверждает публикацию и доступность текста, но не консолидированную редакцию закона."
        ),
        "errors": portal_errors,
    }
    update_check = {
        "attempted": True,
        "performed": bool(update_sources),
        "verified": False,
        "status": "evidence_found" if update_sources else ("search_error" if update_errors else "no_evidence"),
        "queries": new_update_queries,
        "notes": update_notes,
        "new_sources_found": len(update_found),
        "official_portal": official_portal_check,
        "integrated_legal_bank": ips_check,
        "provision_history": provision_history,
        "amendment_chain_audit": amendment_chain_audit,
        "errors": update_errors,
    }
    log(f"      Проверочных запросов: {len(new_update_queries)}; источников всего: {len(sources)}")

    log("[5/8] Ищу судебную практику…")
    try:
        court_queries, court_notes = plan_court_practice_check(model, topic, legal_as_of)
    except AgentError as exc:
        # Практика — вспомогательный этап: его сбой не отменяет ответ.
        court_check = {
            "attempted": True,
            "performed": False,
            "verified": False,
            "status": "planning_failed",
            "queries": [],
            "notes": "",
            "new_sources_found": 0,
            "errors": [str(exc)],
        }
        log(f"      Этап пропущен: {exc}")
    else:
        new_court_queries = [query for query in deduplicate_queries(court_queries) if _normalized_query_key(query) not in executed_queries]
        if not new_court_queries:
            new_court_queries = [f"{topic} судебная практика {legal_as_of[:4]}"]
        queries.extend(new_court_queries)
        executed_queries.update(_normalized_query_key(query) for query in new_court_queries)
        court_found, court_errors = search_queries(court_search, new_court_queries, per_query, verbose=verbose)
        # Reserve up to three slots for case-law evidence as well.
        court_sources = deduplicate(court_found, 3)
        court_check = {
            "attempted": True,
            "performed": bool(court_sources),
            "verified": False,
            "status": "evidence_found" if court_sources else ("search_error" if court_errors else "no_evidence"),
            "queries": new_court_queries,
            "notes": court_notes,
            "new_sources_found": len(court_found),
            "errors": court_errors,
        }
        log(f"      Запросов по практике: {len(new_court_queries)}; источников всего: {len(sources)}")

    # The user-configured source_limit applies to the core legal research.
    # Add separately capped evidence for amendments and case law so those
    # mandatory checks remain visible to the report and its citations.
    sources = deduplicate(
        [*sources, *update_sources, *court_sources] if court_check.get("performed")
        else [*sources, *update_sources],
        source_limit + 6,
    )

    log("[6/8] Готовлю и проверяю правовой ответ…")
    report, report_repaired = build_validated_report(model, topic, sources, session_context)
    if report_repaired:
        log("      Отчёт исправлен после автоматической проверки.")
    else:
        log("      Структура и ссылки прошли проверку.")

    log("[7/8] Самопроверка ответа…")
    try:
        report, reflection = reflect_on_report(model, topic, report, sources)
    except AgentError as exc:
        # Самопроверка — вспомогательный этап: её сбой не отменяет ответ.
        reflection = {"performed": False, "consistent": None, "issues": [], "repaired": False, "errors": [str(exc)]}
        log(f"      Этап пропущен: {exc}")
    else:
        if reflection["repaired"]:
            log(f"      Найдено замечаний: {len(reflection['issues'])}; ответ исправлен.")
        else:
            log("      Противоречий не найдено.")

    log("[8/8] Сохраняю результаты…")
    return save_results(
        output_dir, original_topic, queries, sources, report, demo, research_log, report_repaired, update_check, court_check, reflection, checked_on, legal_as_of
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Локальный ИИ-помощник по законодательству РФ")
    parser.add_argument("topic", help="Правовой вопрос в кавычках")
    parser.add_argument("--queries", type=int, default=3, help="Число первоначальных поисковых запросов, 1–5")
    parser.add_argument("--sources", type=int, default=6, help="Максимум источников, 1–10")
    parser.add_argument("--output-dir", type=Path, default=Path("результаты"), help="Папка результатов")
    parser.add_argument("--demo", action="store_true", help="Проверка без API-ключей и внешних запросов")
    parser.add_argument("--as-of", type=validate_as_of, help="Дата правовой ситуации: ГГГГ-ММ-ДД")
    args = parser.parse_args()
    if not args.topic.strip():
        parser.error("Тема не должна быть пустой.")
    if not 1 <= args.queries <= 5:
        parser.error("--queries должен быть от 1 до 5.")
    if not 1 <= args.sources <= 10:
        parser.error("--sources должен быть от 1 до 10.")
    return args


def main() -> int:
    load_dotenv()
    args = parse_args()
    try:
        report_path, run_path = run(
            topic=args.topic.strip(),
            query_count=args.queries,
            source_limit=args.sources,
            output_dir=args.output_dir,
            demo=args.demo,
            as_of=args.as_of,
        )
    except AgentError as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nВыполнение остановлено пользователем.", file=sys.stderr)
        return 130
    print(f"\nГотово.\nОтчёт: {report_path}\nЖурнал: {run_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


