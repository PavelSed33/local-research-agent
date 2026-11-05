#!/usr/bin/env python3
"""Локальный ИИ-агент для поиска информации и подготовки отчётов."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


REQUIRED_SECTIONS = (
    "Краткий вывод",
    "Основные факты",
    "Подробный анализ",
    "Противоречия и ограничения",
    "Выводы",
)
CITATION_SECTIONS = ("Краткий вывод", "Основные факты", "Подробный анализ")


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


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 90) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise AgentError(f"Сервис вернул HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise AgentError(f"Не удалось подключиться к сервису: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise AgentError("Сервис вернул некорректный JSON.") from exc


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
    def __init__(self) -> None:
        self.api_key = os.getenv("TAVILY_API_KEY", "").strip()
        self.url = os.getenv("TAVILY_API_URL", "https://api.tavily.com/search")
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
                "include_raw_content": "text",
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
            text = str(item.get("raw_content") or item.get("content") or "").strip()
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
        if "additional_queries" in system:
            return json.dumps(
                {"sufficient": True, "additional_queries": [], "reason": "Демонстрационные материалы получены."},
                ensure_ascii=False,
            )
        if "queries" in system:
            topic = user.split("Тема:", 1)[-1].splitlines()[0].strip()
            return json.dumps({"queries": [topic, f"{topic}: основные факты", f"{topic}: ограничения"]}, ensure_ascii=False)
        return (
            "## Краткий вывод\n\n"
            "Демонстрационный запуск успешно проверяет полный цикл работы агента [1]. "
            "Для реального исследования необходимо подключить модель и поисковый API.\n\n"
            "## Основные факты\n\n"
            "- Агент формирует поисковые запросы и объединяет результаты без дублей [1].\n"
            "- Отчёт создаётся только на основании переданных источников [1][2].\n\n"
            "## Подробный анализ\n\n"
            "В обычном режиме здесь появится анализ материалов, найденных по вашей теме [1]. "
            "Демонстрационные источники являются условными и не содержат реальных фактов.\n\n"
            "## Противоречия и ограничения\n\n"
            "Это локальная проверка без обращения к внешним сервисам; качество реального поиска в ней не оценивается.\n\n"
            "## Выводы\n\n"
            "Прототип готов к настройке реальных API-ключей и проведению исследования."
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


def format_materials(sources: list[Source]) -> str:
    materials = []
    for index, source in enumerate(sources, start=1):
        materials.append(
            f"[{index}] {source.title}\nURL: {source.url}\nТекст:\n{source.text[:9000]}"
        )
    return "\n\n---\n\n".join(materials)


def plan_queries(model: LanguageModel, topic: str, count: int) -> list[str]:
    response = model.complete(
        "Ты планируешь исследование. Верни только JSON с ключом queries: список разнообразных поисковых запросов. "
        "Не добавляй пояснений. Учитывай актуальность, первоисточники и возможные противоречия.",
        f"Тема: {topic}\nКоличество поисковых запросов: {count}",
    )
    raw_queries = extract_json(response).get("queries", [])
    queries = [str(item).strip() for item in raw_queries if str(item).strip()]
    if not queries:
        raise AgentError("Не удалось составить поисковые запросы.")
    return list(dict.fromkeys([topic, *queries]))[:count]


def assess_sources(model: LanguageModel, topic: str, sources: list[Source]) -> tuple[bool, list[str], str]:
    response = model.complete(
        "Ты оцениваешь полноту исследования. Верни только JSON с полями: sufficient (boolean), "
        "additional_queries (список до 3 новых запросов), reason (краткое обоснование). "
        "Материалов достаточно, если они позволяют ответить на основные аспекты темы и содержат подтверждения важных фактов. "
        "Если данных достаточно, additional_queries должен быть пустым. Не выдумывай факты.",
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


def build_report(model: LanguageModel, topic: str, sources: list[Source]) -> str:
    return model.complete(
        "Ты исследовательский агент. Пиши по-русски, точно и без выдуманных фактов. "
        "Используй только переданные материалы. Каждое существенное утверждение сопровождай ссылкой [N]. "
        "Если данных мало, прямо укажи это. Не добавляй раздел источников: он будет сформирован отдельно. "
        "Используй разделы: Краткий вывод, Основные факты, Подробный анализ, Противоречия и ограничения, Выводы.",
        f"Тема исследования: {topic}\n\nМатериалы:\n\n{format_materials(sources)}",
    )


def section_text(report: str, section: str) -> str:
    match = re.search(
        rf"^##\s+{re.escape(section)}\s*$\n(.*?)(?=^##\s+|\Z)",
        report,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


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
    return issues


def build_validated_report(model: LanguageModel, topic: str, sources: list[Source]) -> tuple[str, bool]:
    """Создаёт отчёт и при необходимости выполняет одно исправление."""
    report = build_report(model, topic, sources)
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
        raise AgentError("Отчёт не прошёл проверку после исправления: " + " ".join(remaining))
    return repaired, True


def search_queries(search: SearchProvider, queries: list[str], per_query: int) -> tuple[list[Source], list[str]]:
    found: list[Source] = []
    errors: list[str] = []
    for query in queries:
        try:
            found.extend(search.search(query, per_query))
        except AgentError as exc:
            errors.append(f"{query}: {exc}")
            print(f"      Предупреждение: {exc}", file=sys.stderr)
    return found, errors


def save_results(
    output_dir: Path,
    topic: str,
    queries: list[str],
    sources: list[Source],
    report: str,
    demo: bool,
    research_log: list[dict[str, Any]],
    report_repaired: bool,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_path = output_dir / f"отчёт-{stamp}.md"
    run_path = output_dir / f"журнал-{stamp}.json"
    source_list = "\n".join(f"{i}. [{s.title}]({s.url})" for i, s in enumerate(sources, start=1))
    warning = "\n> **Демонстрационный режим:** источники и факты условны.\n" if demo else ""
    report_path.write_text(
        f"# {topic}\n{warning}\n{report.strip()}\n\n## Источники\n\n{source_list}\n",
        encoding="utf-8",
    )
    run_path.write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "topic": topic,
                "demo": demo,
                "queries": queries,
                "sources": [asdict(source) for source in sources],
                "research_rounds": research_log,
                "report_validated": True,
                "report_repaired": report_repaired,
                "report_file": report_path.name,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report_path, run_path


def run(topic: str, query_count: int, source_limit: int, output_dir: Path, demo: bool = False) -> tuple[Path, Path]:
    if demo:
        model: LanguageModel = DemoModel()
        search: SearchProvider = DemoSearch()
    else:
        model = OpenAICompatibleModel()
        search = TavilySearch()

    print("[1/5] Планирую исследование…")
    queries = plan_queries(model, topic, query_count)
    executed_queries = set(queries)
    print("      Запросы: " + "; ".join(queries))

    print("[2/5] Ищу и читаю источники…")
    per_query = max(2, min(5, source_limit))
    found, errors = search_queries(search, queries, per_query)
    sources = deduplicate(found, source_limit)
    if not sources:
        details = "\n".join(errors)
        raise AgentError("Не найдено пригодных источников." + (f"\n{details}" if details else ""))
    print(f"      Выбрано источников: {len(sources)}")

    print("[3/5] Оцениваю полноту материалов…")
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
        print(f"      {reason or ('Материалов достаточно.' if sufficient else 'Требуются дополнительные источники.')}")
        new_queries = [query for query in additional_queries if query not in executed_queries]
        if sufficient or additional_round >= 2 or len(sources) >= source_limit or not new_queries:
            if not sufficient and additional_round >= 2:
                print("      Достигнут лимит дополнительного поиска; продолжаю с доступными материалами.")
            break
        additional_round += 1
        queries.extend(new_queries)
        executed_queries.update(new_queries)
        print(f"      Дополнительный поиск {additional_round}/2: " + "; ".join(new_queries))
        found, errors = search_queries(search, new_queries, per_query)
        sources = deduplicate([*sources, *found], source_limit)
        print(f"      Выбрано источников: {len(sources)}")

    print("[4/5] Готовлю и проверяю отчёт…")
    report, report_repaired = build_validated_report(model, topic, sources)
    if report_repaired:
        print("      Отчёт исправлен после автоматической проверки.")
    else:
        print("      Структура и ссылки прошли проверку.")

    print("[5/5] Сохраняю результаты…")
    return save_results(output_dir, topic, queries, sources, report, demo, research_log, report_repaired)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Локальный ИИ-агент для исследований и отчётов")
    parser.add_argument("topic", help="Тема исследования в кавычках")
    parser.add_argument("--queries", type=int, default=3, help="Число первоначальных поисковых запросов, 1–5")
    parser.add_argument("--sources", type=int, default=6, help="Максимум источников, 1–10")
    parser.add_argument("--output-dir", type=Path, default=Path("результаты"), help="Папка результатов")
    parser.add_argument("--demo", action="store_true", help="Проверка без API-ключей и внешних запросов")
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
