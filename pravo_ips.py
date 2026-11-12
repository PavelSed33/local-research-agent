"""Read-only client for the official integrated legal information bank (IPS).

The legacy IPS interface exposes documents through /proxy/ips/?docbody=&nd=...
and revision selectors through rdk. Revision snapshots are kept distinct from
legal effective periods: a displayed edition date alone does not prove when
all provisions in that edition entered into force.
"""
from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any

from agent import AgentError, Source
from source_admission import classify_source, SourceAdmissionError

BASE_URL = "https://pravo.gov.ru"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_DOCUMENTS = 3
MAX_REVISIONS = 8
MAX_SNAPSHOT_TEXT_CHARS = 12000
MAX_HISTORY_TEXT_CHARS = MAX_REVISIONS * MAX_SNAPSHOT_TEXT_CHARS


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.text_parts.append(value)
            if self._href is not None:
                self._link_text.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._link_text)))
            self._href = None
            self._link_text = []

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", html.unescape(" ".join(self.text_parts))).strip()


@dataclass(frozen=True)
class RevisionSnapshot:
    document_id: str
    revision_id: str
    edition_date: str | None
    text: str
    url: str


def _parse_date(text: str) -> str | None:
    patterns = (
        r"(?:редакц(?:ия|ии)|ред\.)\s*(?:от\s*)?(\d{2}\.\d{2}\.\d{4})",
        r"(?:по состоянию на|текст по состоянию на)\s*(\d{2}\.\d{2}\.\d{4})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw = match.group(1)
            try:
                return date(int(raw[6:]), int(raw[3:5]), int(raw[:2])).isoformat()
            except ValueError:
                continue
    return None


def _document_id(href: str) -> str | None:
    parsed = urllib.parse.urlsplit(href)
    if parsed.scheme and parsed.scheme != "https":
        return None
    if parsed.netloc and parsed.hostname not in {"pravo.gov.ru", "www.pravo.gov.ru"}:
        return None
    params = urllib.parse.parse_qs(parsed.query)
    value = (params.get("nd") or [None])[0]
    return value if value and value.isdigit() else None


class PravoIpsBank:
    """Search the official integrated bank and expose dated revision snapshots."""

    def __init__(self, timeout: int = 8) -> None:
        self.timeout = max(3, min(int(timeout), 20))

    def _get(self, params: dict[str, Any]) -> str:
        query = urllib.parse.urlencode(params)
        url = f"{BASE_URL}/proxy/ips/?{query}"
        try:
            classify_source(url)
        except SourceAdmissionError as exc:
            raise AgentError(f"Источник не прошёл проверку происхождения: {exc}") from exc
        request = urllib.request.Request(
            url,
            headers={"Accept": "text/html", "User-Agent": "LocalResearchAgent/1.0 (read-only legal bank client)"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise AgentError(f"Эталонный банк «Законодательство России» вернул HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AgentError(f"Не удалось обратиться к эталонному банку «Законодательство России»: {exc}.") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AgentError("Ответ эталонного банка превышает 2 МиБ.")
        return raw.decode("utf-8-sig", errors="replace")

    def _page(self, document_id: str, revision_id: str = "") -> tuple[str, list[tuple[str, str]]]:
        if not document_id.isdigit() or (revision_id and not revision_id.isdigit()):
            raise AgentError("Некорректный идентификатор документа или редакции эталонного банка.")
        raw = self._get({"docbody": "", "nd": document_id, "rdk": revision_id})
        parser = _PageParser()
        parser.feed(raw)
        parser.close()
        return parser.text, parser.links

    def get_history(self, document_id: str, as_of: str | None = None) -> dict[str, Any]:
        """Return snapshots discovered via official IPS revision selectors.

        edition_date is the date printed for a snapshot, not an assertion
        about the effective period of every provision in that snapshot.
        """
        try:
            target = date.fromisoformat(as_of) if as_of else None
        except ValueError as exc:
            raise AgentError("Дата проверки редакции должна быть в формате ГГГГ-ММ-ДД.") from exc

        current_text, links = self._page(document_id)
        revision_ids: list[str] = []
        for href, _ in links:
            parsed = urllib.parse.urlsplit(href)
            if parsed.path and not parsed.path.endswith("/proxy/ips/"):
                continue
            params = urllib.parse.parse_qs(parsed.query)
            if (params.get("nd") or [None])[0] != document_id:
                continue
            revision = (params.get("rdk") or [None])[0]
            if revision and revision.isdigit() and revision not in revision_ids:
                revision_ids.append(revision)
        # Reserve one slot for the live/current text: the revision selector list
        # usually contains only historical rdk entries, not the current page.
        revision_ids = revision_ids[:MAX_REVISIONS - 1]

        snapshots: list[RevisionSnapshot] = []
        remaining_chars = MAX_HISTORY_TEXT_CHARS
        if current_text and remaining_chars > 0:
            current_bounded_text = current_text[:min(MAX_SNAPSHOT_TEXT_CHARS, remaining_chars)]
            current_date = _parse_date(current_text)
            snapshots.append(RevisionSnapshot(
                document_id=document_id, revision_id="",
                edition_date=current_date, text=current_bounded_text,
                url=f"{BASE_URL}/proxy/ips/?docbody=&nd={document_id}",
            ))
            remaining_chars -= len(current_bounded_text)

        for revision_id in revision_ids:
            if remaining_chars <= 0:
                break
            text, _ = self._page(document_id, revision_id)
            if text:
                bounded_text = text[:min(MAX_SNAPSHOT_TEXT_CHARS, remaining_chars)]
                remaining_chars -= len(bounded_text)
                revision_date = _parse_date(text)
                # Identical current/revision snapshots can occur in the legacy
                # selector list. Avoid a false duplicate-date gap for exact copies.
                if any(
                    item.edition_date == revision_date and item.text == bounded_text
                    for item in snapshots
                ):
                    continue
                snapshots.append(RevisionSnapshot(
                    document_id=document_id, revision_id=revision_id,
                    edition_date=revision_date, text=bounded_text,
                    url=f"{BASE_URL}/proxy/ips/?docbody=&nd={document_id}&rdk={revision_id}",
                ))

        if not snapshots:
            snapshots.append(RevisionSnapshot(
                document_id=document_id, revision_id="",
                edition_date=_parse_date(current_text), text=current_text[:MAX_SNAPSHOT_TEXT_CHARS],
                url=f"{BASE_URL}/proxy/ips/?docbody=&nd={document_id}",
            ))

        dated = [item for item in snapshots if item.edition_date]
        selected = None
        if target and dated:
            eligible = [item for item in dated if date.fromisoformat(item.edition_date or "9999-12-31") <= target]
            if eligible:
                selected = max(eligible, key=lambda item: item.edition_date or "")
        elif not target and dated:
            selected = max(dated, key=lambda item: item.edition_date or "")

        snapshot_records = [
            {"revision_id": item.revision_id, "edition_date": item.edition_date,
             "url": item.url, "text": item.text}
            for item in sorted(snapshots, key=lambda item: item.edition_date or "")
        ]
        from legal_history import audit_revision_chain
        revision_chain_audit = audit_revision_chain(snapshot_records, [])

        return {
            "document_id": document_id, "as_of": as_of,
            "snapshots": snapshot_records,
            "revision_chain_audit": revision_chain_audit,
            "selected_snapshot": (
                {"revision_id": selected.revision_id, "edition_date": selected.edition_date,
                 "url": selected.url, "text": selected.text} if selected else None
            ),
            "verified": False,
            "status": "historical_snapshot_candidate" if selected else "revision_history_unavailable",
            "limitation": (
                "Дата редакции — не дата вступления в силу каждой нормы. Для вывода о действии нормы "
                "нужны переходные положения изменяющих актов и дата вступления конкретного положения в силу."
            ),
        }

    def search(self, query: str, limit: int = 3, as_of: str | None = None) -> list[Source]:
        query = str(query or "").strip()
        if not query:
            return []
        limit = max(1, min(int(limit), MAX_DOCUMENTS))
        raw = self._get({
            "docbody": "", "intelsearch": query[:300],
            "bpa": "cd00000", "bpas": "cd00000",
            "firstDoc": "1", "lastDoc": "1",
        })
        parser = _PageParser()
        parser.feed(raw)
        parser.close()

        found: list[tuple[str, str]] = []
        seen: set[str] = set()
        for href, title in parser.links:
            document_id = _document_id(href)
            if document_id and document_id not in seen:
                seen.add(document_id)
                found.append((document_id, title or "Документ эталонного банка"))
            if len(found) >= limit:
                break

        sources: list[Source] = []
        for document_id, title in found:
            history = self.get_history(document_id, as_of=as_of)
            snapshot = history.get("selected_snapshot")
            if not snapshot:
                continue
            details = [
                f"Идентификатор документа эталонного банка: {document_id}",
                f"Редакция-снимок: {snapshot.get('edition_date') or 'дата не извлечена'}",
                f"Статус проверки периода действия: {history['status']}",
                history["limitation"],
                "Текст ниже получен из интегрированного банка «Законодательство России»; его размещение не является официальным опубликованием.",
                "Доступные снимки редакций: " + "; ".join(
                    f"{item['revision_id'] or 'current'}={item['edition_date'] or 'дата не указана'}"
                    for item in history["snapshots"]
                ),
                "Диагностика цепочки редакций: " + json.dumps(
                    history["revision_chain_audit"], ensure_ascii=False
                ),
            ]
            sources.append(Source(
                title=f"{title} — эталонный банк «Законодательство России»",
                url=snapshot["url"],
                text="\n".join(details) + "\n\n" + snapshot["text"],
                query=query,
            ))
        return sources
