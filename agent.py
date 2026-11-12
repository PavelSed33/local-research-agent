#!/usr/bin/env python3
"""Локальный ИИ-помощник по законодательству РФ с проверкой изменений."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol


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


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def validate_service_url(url: str) -> str:
    """Проверяет URL внешнего сервиса до любого сетевого обращения.

    Разрешены только https:// и локальные http:// (127.0.0.1, localhost, [::1]).
    Запрещены учётные данные в URL, фрагменты и не-http схемы.
    """
    if not isinstance(url, str) or not url.strip():
        raise AgentError("URL сервиса должен быть непустой строкой.")
    try:
        parsed = urllib.parse.urlsplit(url.strip())
    except ValueError as exc:
        raise AgentError(f"Некорректный URL сервиса: {exc}.") from exc
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https":
        pass
    elif parsed.scheme == "http" and host in LOCAL_HOSTS:
        pass
    else:
        raise AgentError(
            "Разрешены только https:// и локальный http:// (127.0.0.1, localhost, [::1])."
        )
    if parsed.username is not None or parsed.password is not None:
        raise AgentError("Учётные данные в URL запрещены: используйте заголовки.")
    if parsed.fragment:
        raise AgentError("Фрагменты (#...) в URL сервиса запрещены.")
    return url.strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Перенаправления не выполняются: credentials и тело документа не уходят третьим лицам."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())

def open_json_request(request: urllib.request.Request, timeout: int):
    """Выполняет запрос без следования перенаправлениям; возвращает объект ответа."""
    try:
        return _OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise AgentError(
                f"Перенаправление отклонено (HTTP {exc.code}): повторите запрос по итоговому адресу."
            ) from exc
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise AgentError(f"Сервис вернул HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise AgentError(f"Не удалось подключиться к сервису: {exc.reason}") from exc


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 90, attempts: int = 3) -> dict[str, Any]:
    url = validate_service_url(url)
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    for attempt in range(attempts):
        try:
            response = open_json_request(request, timeout)
            raw = response.read()
        except TimeoutError:
            if attempt < attempts - 1:
                time.sleep(min(0.25 * (2 ** attempt), 2.0))
                continue
            raise AgentError("Сервис не ответил вовремя: превышен таймаут ожидания.") from None
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError("Сбой сетевого обращения к сервису.") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentError("Сервис вернул некорректный JSON.") from exc
    raise AgentError("Сервис не ответил вовремя: превышен таймаут ожидания.")


def extract_json(text: str) -> dict[str, Any]:
    """Извлекает JSON-объект даже из ответа с Markdown-ограждением."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise AgentError("Модель не вернула ожидаемый JSON.")
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AgentError("Модель вернула повреждённый JSON.") from exc
    if not isinstance(value, dict):
        raise AgentError("Ожидался JSON-объект.")
    return value


def current_check_date() -> str:
    """Сегодняшняя дата проверки в формате ГГГГ-ММ-ДД."""
    return datetime.now(timezone.utc).date().isoformat()


AS_OF_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
def validate_as_of(value: Any) -> str | None:
    """Нормализует ситуационную дату; None, если значение некорректно.

    Принимается только строгий формат ГГГГ-ММ-ДД с реальной календарной датой.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not AS_OF_PATTERN.match(value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


SOURCE_AUTHORITY_CLASSES = {
    "official_legal_portal": "Официальный портал правовой информации",
    "official_court": "Официальный сайт суда",
    "case_law_aggregator": "Агрегатор судебной практики",
    "secondary_legal_reference": "Справочная правовая система (не официальный текст)",
    "government_legal_reproduction": "Опубликовано на сайте органа власти (не исходная публикация)",
    "official_legal_explanation": "Официальное разъяснение",
    "unclassified": "Источник без классификации",
}

_AUTHORITY_EXACT_HOSTS = {
    "pravo.gov.ru": "official_legal_portal",
    "www.pravo.gov.ru": "official_legal_portal",
    "publication.pravo.gov.ru": "official_legal_portal",
    "kad.arbitr.ru": "official_court",
    "arbitr.ru": "official_court",
    "www.arbitr.ru": "official_court",
    "vsrf.ru": "official_court",
    "sudact.ru": "case_law_aggregator",
    "consultant.ru": "secondary_legal_reference",
    "www.consultant.ru": "secondary_legal_reference",
    "garant.ru": "secondary_legal_reference",
    "www.garant.ru": "secondary_legal_reference",
}


def classify_source_authority(url: str) -> str:
    """Классифицирует источник по хосту; lookalike-домены не получают гос. статус."""
    try:
        parsed = urllib.parse.urlsplit(str(url))
    except ValueError:
        return "unclassified"
    host = (parsed.hostname or "").lower()
    if not host:
        return "unclassified"
    if host in _AUTHORITY_EXACT_HOSTS:
        return _AUTHORITY_EXACT_HOSTS[host]
    if host == "zpp.rospotrebnadzor.ru":
        return "government_legal_reproduction" if parsed.path.startswith("/npa") else "official_legal_explanation"
    if host == "epp.genproc.gov.ru":
        return "official_legal_explanation"
    if host.endswith(".gov.ru"):
        return "government_legal_reproduction"
    return "unclassified"


def source_authority_label(url: str) -> str:
    """Человекочитаемая метка класса источника."""
    return SOURCE_AUTHORITY_CLASSES[classify_source_authority(url)]


def deduplicate_queries(queries: list[Any]) -> list[str]:
    """Убирает дубликаты без учёта регистра и краевых пробелов, сохраняя порядок."""
    seen: set[str] = set()
    unique: list[str] = []
    for item in queries:
        query = str(item).strip()
        if not query:
            continue
        key = query.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(query)
    return unique


def _status_from_check(check: dict[str, Any]) -> str:
    if check.get("performed"):
        return "evidence_found_unverified"
    if check.get("attempted"):
        return "unavailable" if check.get("errors") else "no_evidence"
    return "not_attempted"


def evidence_summary(update_check: dict[str, Any], court_check: dict[str, Any], demo: bool) -> dict[str, Any]:
    """Сводный статус доказательной базы: найденное свидетельство никогда не считается верификацией."""
    if demo:
        return {
            "status": "demo",
            "verified": False,
            "amendments": {"status": "demo", "verified": False},
            "official_publications": {"status": "demo"},
            "court_practice": {"status": "demo"},
        }
    update_check = update_check if isinstance(update_check, dict) else {}
    court_check = court_check if isinstance(court_check, dict) else {}
    update_errors = [str(error) for error in update_check.get("errors", []) if str(error).strip()]
    court_errors = [str(error) for error in court_check.get("errors", []) if str(error).strip()]
    portal = update_check.get("official_portal") if isinstance(update_check.get("official_portal"), dict) else {}
    portal_records = portal.get("records") or portal.get("found") or portal.get("sources")
    if portal_records:
        portal_status = "evidence_found_unverified"
    elif portal.get("performed") or portal.get("attempted"):
        portal_status = "unavailable" if portal.get("errors") else "no_evidence"
    else:
        portal_status = "not_attempted"
    return {
        "status": "unverified",
        "verified": False,
        "amendments": {
            "status": _status_from_check(update_check),
            "verified": False,
            "error_count": len(update_errors),
        },
        "official_publications": {"status": portal_status, "verified": False},
        "court_practice": {
            "status": _status_from_check(court_check),
            "verified": False,
            "error_count": len(court_errors),
        },
    }


def run(
    topic: str,
    query_count: int = 3,
    source_limit: int = 6,
    output_dir: Path | str = Path("результаты"),
    demo: bool = False,
    session_context: str = "",
) -> tuple[Path, Path]:
    """Safe demo-only fallback until the verified research pipeline is restored.

    Never present synthetic evidence as a real legal answer.
    """
    if not isinstance(topic, str) or not topic.strip():
        raise AgentError("Укажите непустой правовой вопрос.")
    if not demo:
        raise AgentError(
            "Реальный юридический поиск временно недоступен: "
            "полный исследовательский конвейер ещё не восстановлен. "
            "Используйте demo=true только для проверки интеграции."
        )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checked_on = current_check_date()
    report = output_dir / "ответ-demo.md"
    journal = output_dir / "журнал-demo.json"
    answer = (
        "# Демонстрационный ответ (не юридическое заключение)\\n\\n"
        f"**Вопрос:** {topic.strip()}\\n\\n"
        "## Краткий ответ\\n"
        "Демонстрационный режим проверяет только техническую интеграцию.\\n\\n"
        "## Правовое основание\\n"
        "Нормативные источники не запрашивались и не проверялись.\\n\\n"
        "## Актуальность и изменения\\n"
        f"Дата технического запуска: {checked_on}. Актуальность норм не проверялась.\\n\\n"
        "## Как применить к ситуации\\n"
        "Для реального ответа требуется восстановленный поиск по официальным источникам.\\n\\n"
        "## Ограничения\\n"
        f"{LEGAL_DISCLAIMER}\\n"
    ).replace("\\\\n", "\\n")
    report.write_text(answer, encoding="utf-8")
    journal.write_text(
        json.dumps({
            "topic": topic.strip(),
            "checked_on": checked_on,
            "demo": True,
            "sources": [],
            "evidence": {"status": "demo", "verified": False},
            "session_context_provided": bool(session_context),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report, journal
