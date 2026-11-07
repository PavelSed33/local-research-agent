"""Read-only adapter for the official Russian legal publication portal.

The portal's public read endpoints are described by its Swagger surface. This
client deliberately treats publication records as evidence of publication,
not as proof that a consolidated law is currently in force.
"""
from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from agent import AgentError, Source


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_BASE_URL = "https://publication.pravo.gov.ru"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def _html_to_text(value: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        return parser.text() or html.unescape(value)
    except Exception:
        return html.unescape(re.sub(r"<[^>]+>", " ", value))


def _value(mapping: dict[str, Any], *names: str) -> Any:
    lowered = {str(key).casefold(): value for key, value in mapping.items()}
    for name in names:
        value = lowered.get(name.casefold())
        if value not in (None, ""):
            return value
    return None


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _find_records(value: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 5:
        return []
    if isinstance(value, list):
        records = [item for item in value if isinstance(item, dict)]
        if records:
            return records
        result: list[dict[str, Any]] = []
        for item in value:
            result.extend(_find_records(item, depth + 1))
        return result
    if not isinstance(value, dict):
        return []
    for key in ("items", "results", "documents", "records", "data", "value"):
        nested = _value(value, key)
        if isinstance(nested, (list, dict)):
            records = _find_records(nested, depth + 1)
            if records:
                return records
    if _value(value, "EoNumber", "eoNumber", "DocumentEONumber", "DocumentName", "Name", "Title"):
        return [value]
    return []


def _extract_document_text(value: Any) -> str:
    if isinstance(value, str):
        return _html_to_text(value)
    if isinstance(value, list):
        return "\n".join(filter(None, (_extract_document_text(item) for item in value)))
    if isinstance(value, dict):
        for key in ("Text", "DocumentText", "Content", "Html", "Body", "text", "content"):
            candidate = _value(value, key)
            if isinstance(candidate, (str, list, dict)):
                text = _extract_document_text(candidate)
                if text:
                    return text
        # Some responses wrap the actual result in a nested object.
        for candidate in value.values():
            if isinstance(candidate, (dict, list)):
                text = _extract_document_text(candidate)
                if text:
                    return text
    return ""


class PravoPortalSearch:
    """Search official publication records and fetch each available full text."""

    def __init__(self, base_url: str | None = None, timeout: int = 20) -> None:
        self.base_url = (base_url or os.getenv("PRAVO_PORTAL_API_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "publication.pravo.gov.ru", "pravo.gov.ru"
        }:
            raise AgentError("PRAVO_PORTAL_API_BASE_URL должен указывать на официальный HTTPS-домен портала.")
        self.timeout = max(3, min(int(timeout), 45))
        self.last_records: list[dict[str, Any]] = []

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode(
            [(key, value) for key, value in params.items() if value not in (None, "")],
            doseq=True,
        )
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, text/plain, text/html;q=0.8",
                "User-Agent": "LocalResearchAgent/1.0 (read-only legal source client)",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise AgentError(f"Портал правовой информации вернул HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AgentError(f"Не удалось обратиться к порталу правовой информации: {exc}.") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AgentError("Портал правовой информации вернул ответ больше 2 МиБ.")
        decoded = raw.decode("utf-8-sig", errors="replace").strip()
        if not decoded:
            return None
        try:
            return json.loads(decoded)
        except json.JSONDecodeError:
            return decoded

    def search(self, query: str, limit: int) -> list[Source]:
        if not isinstance(query, str) or not query.strip():
            return []
        limit = max(1, min(int(limit), 10))
        response = self._get(
            "/api/Documents",
            {"Name": query.strip()[:300], "NumberSearchType": 0},
        )
        records = _find_records(response)
        self.last_records = records[:limit]
        sources: list[Source] = []
        seen: set[str] = set()

        for record in records:
            if len(sources) >= limit:
                break
            eo_number = _as_text(_value(
                record, "EoNumber", "eoNumber", "DocumentEONumber", "eo_number"
            ))
            title = _as_text(_value(
                record, "DocumentName", "Name", "Title", "DocumentTitle"
            )) or "Правовой акт (официальная публикация)"
            number = _as_text(_value(
                record, "DocumentNumber", "Number", "DocNumber"
            ))
            signed = _as_text(_value(
                record, "SignDate", "DocumentDate", "Date", "SignedDate"
            ))
            published = _as_text(_value(
                record, "PubDate", "PublishDate", "PublicationDate", "PublishedAt"
            ))
            effective = _as_text(_value(
                record, "EffectiveDate", "EntryIntoForceDate", "DateOfEntryIntoForce"
            ))
            identity = eo_number or f"{title}|{number}|{published}"
            if identity in seen:
                continue
            seen.add(identity)

            full_text = ""
            text_error = ""
            if eo_number:
                try:
                    text_response = self._get(
                        "/api/DocumentText", {"eonumber": eo_number}
                    )
                    full_text = _extract_document_text(text_response)
                except AgentError as exc:
                    text_error = str(exc)
            if not full_text:
                full_text = _extract_document_text(record)
            if not full_text:
                # Metadata-only hits are not supplied to the LLM as if they
                # were the legal text. Keep the failure explicit instead.
                continue

            metadata = [
                f"Наименование: {title}",
                f"Номер: {number or 'не указан в ответе API'}",
                f"Дата подписания: {signed or 'не указана в ответе API'}",
                f"Дата опубликования: {published or 'не указана в ответе API'}",
                f"Дата вступления в силу: {effective or 'не указана в ответе API'}",
                f"Идентификатор официальной публикации: {eo_number or 'не указан'}",
                "Тип подтверждения: запись об официальном опубликовании; это не подтверждение консолидированной действующей редакции.",
            ]
            if text_error:
                metadata.append(f"Ошибка получения полного текста: {text_error}")
            api_url = f"{self.base_url}/api/Document?{urllib.parse.urlencode({'eoNumber': eo_number})}" if eo_number else f"{self.base_url}/api/Documents"
            sources.append(
                Source(
                    title=f"{title}{' № ' + number if number else ''} — официальный портал",
                    url=api_url,
                    text="\n".join(metadata) + "\n\nТекст официальной публикации:\n" + full_text[:9000],
                    query=query,
                )
            )
        return sources


class FederatedLegalSearch:
    """Combine official portal search with the existing web-search provider."""

    def __init__(self, *providers: Any) -> None:
        self.providers = providers
        self.last_errors: list[str] = []

    def search(self, query: str, limit: int) -> list[Source]:
        self.last_errors = []
        results: list[Source] = []
        for provider in self.providers:
            try:
                results.extend(provider.search(query, limit))
            except AgentError as exc:
                self.last_errors.append(f"{provider.__class__.__name__}: {exc}")
        # De-duplicate by canonical URL while retaining provider priority:
        # the official portal's records are placed before general web results.
        unique: dict[str, Source] = {}
        for source in results:
            key = source.url.split("#", 1)[0].rstrip("/")
            unique.setdefault(key, source)
            if len(unique) >= limit * 2:
                break
        if not unique and self.last_errors:
            raise AgentError("; ".join(self.last_errors))
        return list(unique.values())
