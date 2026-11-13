"""Local statute corpus for legal research agent.

The corpus is evidence of retrieval, never legal verification: every entry
keeps explicit flags (verified=False, original_publication=False) so that a
captured copy can never be promoted into an accepted statutory edition.
"""
import hashlib
import json
from pathlib import Path
import re
import urllib.parse

from agent import AgentError, Source, current_check_date, validate_as_of

ROOT = Path(__file__).resolve().parent / "fixtures" / "statute_corpus"
COVERAGE = Path(__file__).resolve().parent / "fixtures" / "legal_coverage.json"

MAX_SECTION_TEXT_CHARS = 200_000
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_.-]+$")
_PUBLIC_COPY_HOSTS = {"www.consultant.ru", "consultant.ru"}


def _url(url: str, kind: str = "public_copy") -> str:
    """Проверяет, что URL — закреплённая публичная копия, и возвращает его.

    Принимаются только https-URL без учётных данных, порта, фрагмента и
    query на разрешённом хосте справочной системы.
    """
    if not isinstance(url, str):
        raise ValueError("URL должен быть строкой.")
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise ValueError(f"Некорректный URL: {exc}.") from exc
    if parsed.scheme != "https":
        raise ValueError("Только https-URL публичных копий.")
    host = (parsed.hostname or "").lower()
    if host not in _PUBLIC_COPY_HOSTS:
        raise ValueError(f"Недопустимый хост публичной копии: {host or 'пусто'}.")
    if parsed.port is not None or parsed.query or parsed.fragment:
        raise ValueError("URL не закреплён: порт, query и фрагменты запрещены.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Учётные данные в URL запрещены.")
    return url


def _load_manifest(corpus_dir: Path) -> dict:
    path = corpus_dir / "manifest.json"
    if not path.exists():
        return {"schema_version": 1, "captured_on": None, "groups": [], "gaps": [],
                "verified": False, "original_publication": False}
    return json.loads(path.read_text(encoding="utf-8"))


def _coverage_cards() -> dict:
    data = json.loads(COVERAGE.read_text(encoding="utf-8"))
    return {card["id"]: card for card in data["cards"]}


def _safe_group_file(corpus_dir: Path, name: str) -> Path | None:
    if not _SAFE_PATH.match(name or ""):
        return None
    path = corpus_dir / name
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.resolve().parent != corpus_dir.resolve():
            return None
    except OSError:
        return None
    return path


def corpus_report(card_ids=None, as_of=None, *, corpus_dir: Path | str | None = None,
                  include_text: bool = False) -> dict:
    """Сводка корпуса: целостность файлов, происхождение, явные пробелы.

    Никакие флаги в файлах не могут повысить запись до принятой редакции —
    verified/original_publication/period_supported всегда False.
    """
    date = validate_as_of(as_of) or current_check_date()
    base = Path(corpus_dir) if corpus_dir is not None else ROOT
    manifest = _load_manifest(base)
    cards = _coverage_cards()
    if card_ids is not None:
        if not isinstance(card_ids, list) or any(not isinstance(cid, str) or cid not in cards for cid in card_ids):
            raise ValueError("card_ids должен содержать известные ID карточек норм.")
    wanted = set(card_ids) if card_ids is not None else None
    entries: list[dict] = []
    errors: list[str] = []
    for group in manifest.get("groups", []):
        name = group.get("file", "")
        path = _safe_group_file(base, name)
        if path is None:
            errors.append(f"Недопустимый или отсутствующий файл группы: {name!r}.")
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != group.get("sha256"):
            errors.append(f"Несовпадение sha256 файла группы: {name}.")
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            errors.append(f"Файл группы не является корректным JSON: {name}.")
            continue
        if data.get("act_id") != group.get("act_id"):
            errors.append(f"act_id файла {name} не совпадает с манифестом.")
            continue
        for section in data.get("sections", []):
            card_id = section.get("card_id", "")
            if card_id not in cards:
                errors.append(f"Неизвестная карточка {card_id!r} в {name}.")
                continue
            if wanted is not None and card_id not in wanted:
                continue
            text = section.get("text", "")
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != section.get("sha256"):
                errors.append(f"Несовпадение sha256 текста карточки {card_id}.")
                continue
            try:
                _url(section.get("url", ""), section.get("kind", "public_copy"))
            except ValueError as exc:
                errors.append(f"URL карточки {card_id} отклонён: {exc}")
                continue
            entry = {
                "card_id": card_id,
                "act_id": data["act_id"],
                "provision": section.get("provision", ""),
                "title": section.get("title", ""),
                "heading": section.get("heading", ""),
                "url": section.get("url", ""),
                "kind": section.get("kind", "public_copy"),
                "captured_on": section.get("captured_on", manifest.get("captured_on") or ""),
                "sha256": section.get("sha256", ""),
                "response_sha256": section.get("response_sha256", ""),
                "edition_label": section.get("edition_label"),
                "file": name,
                "integrity_checked": True,
                "original_publication": False,
                "raw_response_archived": False,
                "period_supported": False,
                "verified": False,
            }
            if include_text:
                entry["text"] = text
            entries.append(entry)
    gaps = [{"status": gap.get("status", "unknown"), "reason": gap.get("reason", "")}
            for gap in manifest.get("gaps", [])]
    return {
        "as_of": date,
        "checked_on": current_check_date(),
        "captured_on": manifest.get("captured_on"),
        "entries": entries,
        "errors": errors,
        "text_received_count": len(entries),
        "refresh_gaps": gaps,
        "original_publication": False,
        "raw_response_archived": False,
        "verified": False,
        "history_complete": False,
        "commercial_use_allowed": False,  # No accepted legal edition or rights review in this corpus.
    }


def handle_corpus(payload: dict) -> dict:
    """Обработчик запроса к корпусу с строгой валидацией входа."""
    if not isinstance(payload, dict):
        raise ValueError("Ожидается JSON-объект.")
    if "document" in payload:
        raise ValueError("document не поддерживается: запрашивайте карточки по card_ids.")
    card_ids = payload.get("card_ids")
    if card_ids is None and payload.get("include_text"):
        raise ValueError("include_text требует явного списка card_ids.")
    include_text = payload.get("include_text", False)
    if not isinstance(include_text, bool):
        raise ValueError("include_text должен быть булевым.")
    as_of = validate_as_of(payload.get("as_of"))
    if payload.get("as_of") is not None and as_of is None:
        raise ValueError("as_of должен быть датой в формате ГГГГ-ММ-ДД.")
    return corpus_report(card_ids=card_ids, as_of=as_of, include_text=include_text)


def statutory_review_sources(domain: str, topic: str, as_of: str | None = None) -> tuple[list[Source], dict]:
    """Источники корпуса для проверки договора и отчёт о их временной применимости.

    Копия, полученная позже даты ситуации, помечается later_capture и в контекст
    исторической модели не попадает.
    """
    date = validate_as_of(as_of) or current_check_date()
    report = corpus_report(as_of=date, include_text=True)
    sources: list[Source] = []
    for entry in report["entries"]:
        if entry["captured_on"] and entry["captured_on"] > date:
            entry["temporal_status"] = f"later_capture_not_applicable_to_{date}"
            continue
        entry["temporal_status"] = f"captured_on_or_before_{date}"
        sources.append(Source(title=entry["title"], url=entry["url"], text=entry["text"], query=f"{domain}:{topic}"))
    return sources, report


def search_statutory_sources(query: str, as_of: str | None = None, limit: int = 5) -> list[Source]:
    """Поиск по корпусу с обязательной датой отсечки."""
    date = validate_as_of(as_of)
    if date is None:
        raise ValueError("as_of должен быть датой в формате ГГГГ-ММ-ДД.")
    report = corpus_report(as_of=date, include_text=True)
    results: list[Source] = []
    needle = (query or "").strip().lower()
    for entry in report["entries"]:
        if entry["captured_on"] and entry["captured_on"] > date:
            continue
        if needle and needle in entry.get("text", "").lower():
            results.append(Source(title=entry["title"], url=entry["url"],
                                  text=entry["text"], query=query))
    return results[:limit]


class StatuteCorpus:
    """Local corpus of statute texts (legacy stub-based interface)."""

    def __init__(self, base_dir="fixtures/statutes"):
        self.base_dir = Path(base_dir)
        self.metadata = self._load_metadata()
        self.cache = {}

    def _load_metadata(self):
        metadata_path = self.base_dir / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"statutes": {}}

    def get_statute(self, statute_id):
        if statute_id in self.cache:
            return self.cache[statute_id]
        statute_path = self.base_dir / statute_id / "content.json"
        if not statute_path.exists():
            return None
        with open(statute_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.cache[statute_id] = data
        return data

    def get_article(self, statute_id, article_number):
        statute = self.get_statute(statute_id)
        if not statute:
            return None
        return statute.get("articles", {}).get(f"article_{article_number}")

    def search(self, query, statute_ids=None, limit=10):
        results = []
        if not statute_ids:
            statute_ids = list(self.metadata.get("statutes", {}).keys())
        for statute_id in statute_ids:
            statute = self.get_statute(statute_id)
            if not statute:
                continue
            for article_key, article in statute.get("articles", {}).items():
                if query.lower() in article.get("text", "").lower():
                    results.append({
                        "statute_id": statute_id,
                        "article": article,
                        "relevance": article.get("text", "").lower().count(query.lower())
                    })
        results.sort(key=lambda x: x["relevance"], reverse=True)
        return results[:limit]

    def list_statutes(self):
        return list(self.metadata.get("statutes", {}).keys())


_corpus = None


def get_corpus():
    global _corpus
    if _corpus is None:
        _corpus = StatuteCorpus()
    return _corpus


def get_statute_text(statute_id, article_number=None):
    corpus = get_corpus()
    if article_number:
        article = corpus.get_article(statute_id, article_number)
        return article["text"] if article else None
    statute = corpus.get_statute(statute_id)
    return statute.get("full_text") if statute else None


def search_statutes(query, statute_ids=None, limit=10):
    corpus = get_corpus()
    return corpus.search(query, statute_ids, limit)
