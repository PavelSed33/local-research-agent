"""Strict reconstruction of effective periods for an individual legal provision.

Edition labels and publication dates are never treated as effective dates.
Only explicitly scoped, officially sourced effective-date events are accepted.
"""
from __future__ import annotations

from datetime import date, timedelta
import re
from urllib.parse import urlsplit
from typing import Any

OFFICIAL_HOSTS = {"publication.pravo.gov.ru", "pravo.gov.ru", "www.pravo.gov.ru"}


def _iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _valid_official_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname in OFFICIAL_HOSTS
        and not parsed.username
        and not parsed.password
    )


def build_provision_timeline(
    provision_id: str,
    events: list[dict[str, Any]],
    *,
    timeline_complete: bool = False,
) -> dict[str, Any]:
    """Build intervals from events scoped to one article/part/clause.

    Each event's effective_from is the date the changed wording enters into
    force. The previous wording ends the preceding day. Verification requires
    an explicit completeness attestation in addition to evidence validation.
    """
    requested = str(provision_id or "").strip()
    if not requested or len(requested) > 300:
        return {"status": "invalid_provision_id", "verified": False, "periods": []}
    if not isinstance(events, list) or not events:
        return {
            "status": "no_effective_date_events", "verified": False, "periods": [],
            "limitation": "Не найдены события вступления изменений в силу для конкретной нормы.",
        }

    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"Событие {index + 1}: неверный формат.")
            continue
        effective = _iso_date(event.get("effective_from"))
        quote = str(event.get("effective_date_quote") or "").strip()
        if str(event.get("provision_id") or "").strip() != requested:
            errors.append(f"Событие {index + 1}: идентификатор нормы не совпадает.")
        if effective is None:
            errors.append(f"Событие {index + 1}: некорректная дата вступления в силу.")
        if not all(str(event.get(key) or "").strip() for key in (
            "act_title", "act_number", "official_publication_id"
        )):
            errors.append(f"Событие {index + 1}: не хватает реквизитов акта/публикации.")
        if not _valid_official_url(event.get("official_url")):
            errors.append(f"Событие {index + 1}: отсутствует официальный HTTPS-источник.")
        if not quote or (
            effective and effective.strftime("%d.%m.%Y") not in quote
            and effective.isoformat() not in quote
        ):
            errors.append(f"Событие {index + 1}: цитата не подтверждает указанную дату.")
        if effective:
            normalized.append({
                "effective_from": effective,
                "act_title": str(event.get("act_title") or "").strip(),
                "act_number": str(event.get("act_number") or "").strip(),
                "official_publication_id": str(event.get("official_publication_id") or "").strip(),
                "official_url": event.get("official_url"),
                "effective_date_quote": quote,
            })

    if errors:
        return {
            "status": "insufficient_or_invalid_evidence", "verified": False,
            "periods": [], "errors": errors,
        }
    normalized.sort(key=lambda item: item["effective_from"])
    if any(a["effective_from"] == b["effective_from"] for a, b in zip(normalized, normalized[1:])):
        return {
            "status": "conflicting_same_day_events", "verified": False, "periods": [],
            "limitation": "Несколько событий для одной нормы имеют одинаковую дату; нужна ручная сверка.",
        }

    periods = []
    for index, event in enumerate(normalized):
        next_start = normalized[index + 1]["effective_from"] if index + 1 < len(normalized) else None
        end = next_start - timedelta(days=1) if next_start else None
        periods.append({
            "valid_from": event["effective_from"].isoformat(),
            "valid_to": end.isoformat() if end else None,
            "end_is_open": end is None,
            "introduced_by": {
                "title": event["act_title"],
                "number": event["act_number"],
                "publication_id": event["official_publication_id"],
                "url": event["official_url"],
                "effective_date_quote": event["effective_date_quote"],
            },
        })

    verified = bool(timeline_complete)
    return {
        "provision_id": requested,
        "status": "verified_timeline" if verified else "candidate_timeline_incomplete",
        "verified": verified,
        "timeline_complete": bool(timeline_complete),
        "periods": periods,
        "limitation": None if verified else (
            "Интервалы рассчитаны по найденным событиям, но полнота истории не подтверждена: "
            "могут быть пропущены изменяющие акты или специальные переходные положения."
        ),
    }


def audit_revision_chain(
    snapshots: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare dated official-bank snapshots with amendment events.

    This detects observable mismatches and gaps, but cannot prove that the
    bank exposed every historical version or that every amendment is present.
    """
    if not isinstance(snapshots, list) or not snapshots:
        return {
            "status": "revision_snapshots_missing",
            "coverage_verified": False,
            "verified": False,
            "gaps": ["Нет доступных снимков редакций для сверки."],
            "changed_provisions": [],
        }

    dated: list[tuple[date, dict[str, Any]]] = []
    gaps: list[str] = []
    seen_dates: set[date] = set()
    for index, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict):
            gaps.append(f"Снимок {index + 1}: неверный формат.")
            continue
        snapshot_date = _iso_date(snapshot.get("edition_date"))
        if snapshot_date is None:
            gaps.append(f"Снимок {index + 1}: дата редакции отсутствует или некорректна.")
            continue
        if snapshot_date in seen_dates:
            gaps.append(f"Для даты {snapshot_date.isoformat()} найдено несколько снимков.")
        seen_dates.add(snapshot_date)
        dated.append((snapshot_date, snapshot))

    dated.sort(key=lambda item: item[0])
    changed: list[dict[str, Any]] = []
    for (left_date, left), (right_date, right) in zip(dated, dated[1:]):
        left_text = str(left.get("text") or "")
        right_text = str(right.get("text") or "")
        if not left_text or not right_text:
            gaps.append(
                f"Не хватает текста одного из снимков для сравнения "
                f"{left_date.isoformat()} и {right_date.isoformat()}."
            )
            continue
        left_sections = _provision_sections(left_text)
        right_sections = _provision_sections(right_text)
        for provision_id in sorted(set(left_sections) | set(right_sections)):
            if left_sections.get(provision_id) != right_sections.get(provision_id):
                changed.append({
                    "provision_id": provision_id,
                    "from_snapshot_date": left_date.isoformat(),
                    "to_snapshot_date": right_date.isoformat(),
                    "change_kind": (
                        "added" if provision_id not in left_sections
                        else "removed" if provision_id not in right_sections
                        else "text_changed"
                    ),
                    "before_excerpt": left_sections.get(provision_id, "")[:1800],
                    "after_excerpt": right_sections.get(provision_id, "")[:1800],
                })

    event_dates: set[date] = set()
    for index, event in enumerate(events if isinstance(events, list) else []):
        if not isinstance(event, dict):
            gaps.append(f"Событие изменения {index + 1}: неверный формат.")
            continue
        effective = _iso_date(event.get("effective_from"))
        if effective is None:
            gaps.append(f"Событие изменения {index + 1}: дата вступления в силу не указана.")
        else:
            event_dates.add(effective)

    if dated:
        first_date, last_date = dated[0][0], dated[-1][0]
        for effective in sorted(event_dates):
            if effective < first_date or effective > last_date:
                gaps.append(
                    f"Событие со вступлением в силу {effective.isoformat()} находится вне "
                    "диапазона дат доступных снимков."
                )
    elif event_dates:
        gaps.append("Невозможно сопоставить события изменений с датами снимков.")

    return {
        "status": "revision_chain_gaps_detected" if gaps else "revision_chain_compared_incomplete",
        "snapshot_count": len(snapshots),
        "dated_snapshot_count": len(dated),
        "changed_provisions": changed[:80],
        "change_count": len(changed),
        "gaps": gaps,
        "coverage_verified": False,
        "verified": False,
        "limitation": (
            "Сравнение доступных снимков выявляет различия и несогласованности, но не доказывает "
            "полноту архива редакций, полноту списка изменяющих актов или юридические даты действия норм."
        ),
    }


def _provision_sections(text: str) -> dict[str, str]:
    """Split plain text by recognizable Russian article headings."""
    import re

    # IPS HTML text is flattened into whitespace-separated text, so article
    # headings may not begin a physical line. Match capitalized legal headings
    # anywhere while avoiding lowercase references such as "согласно статье 5".
    pattern = re.compile(
        r"(?<![а-яё])((?:Статья|СТАТЬЯ)\s+\d+(?:\.\d+)?(?:\s*\[[^\]]+\])?\.?)"
    )
    matches = list(pattern.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        heading = re.sub(r"\s+", " ", match.group(1)).strip().rstrip(".")
        body = re.sub(r"\s+", " ", text[match.start():end]).strip()
        sections[heading] = body
    return sections


def extract_amendment_references(text: str) -> list[dict[str, str]]:
    """Extract amendment-act citations printed in consolidated legal text."""
    if not isinstance(text, str) or not text:
        return []
    pattern = re.compile(
        r"(?:Федерального\s+закона|Федеральным\s+законом)\s+от\s+"
        r"(\d{2}\.\d{2}\.\d{4})\s*(?:г\.?\s*)?(?:N|№)\s*"
        r"([\d-]+\s*-?\s*ФЗ)",
        re.IGNORECASE,
    )
    found: dict[tuple[str, str], dict[str, str]] = {}
    for match in pattern.finditer(text):
        raw_date, raw_number = match.groups()
        try:
            parsed = date(int(raw_date[6:]), int(raw_date[3:5]), int(raw_date[:2]))
        except ValueError:
            continue
        number = re.sub(r"\s+", "", raw_number).replace("--", "-").upper()
        found[(parsed.isoformat(), number)] = {
            "signed_date": parsed.isoformat(),
            "act_number": number,
            "citation": match.group(0),
        }
    return list(found.values())


def audit_amendment_links(
    changed_provisions: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate proposed links to official amendment acts and transition clauses."""
    linked: list[dict[str, Any]] = []
    rejected: list[str] = []
    changes = {
        str(item.get("provision_id") or "").strip(): item
        for item in changed_provisions if isinstance(item, dict)
    }
    for index, candidate in enumerate(candidates[:100] if isinstance(candidates, list) else []):
        if not isinstance(candidate, dict):
            rejected.append(f"Связь {index + 1}: неверный формат.")
            continue
        provision_id = str(candidate.get("provision_id") or "").strip()
        change = changes.get(provision_id)
        source_text = str(candidate.get("source_text") or "")
        source_url = candidate.get("source_url")
        act_number = str(candidate.get("act_number") or "").strip()
        act_signed_date = str(candidate.get("act_signed_date") or "").strip()
        publication_id = str(candidate.get("official_publication_id") or "").strip()
        amendment_quote = str(candidate.get("amendment_quote") or "").strip()
        target_act_quote = str(candidate.get("target_act_quote") or "").strip()
        transition_quote = str(candidate.get("transition_quote") or "").strip()
        if change is None:
            rejected.append(f"Связь {index + 1}: статья не найдена среди обнаруженных изменений.")
            continue
        if not _valid_official_url(source_url):
            rejected.append(f"Связь {index + 1}: источник не является официальным HTTPS-источником.")
            continue
        act_title = str(candidate.get("act_title") or "").strip()
        if not act_number or act_number not in source_text:
            rejected.append(f"Связь {index + 1}: номер изменяющего акта не найден в источнике.")
            continue
        if not act_title or act_title not in source_text:
            rejected.append(f"Связь {index + 1}: наименование изменяющего акта не найдено в источнике.")
            continue
        try:
            parsed_signed_date = date.fromisoformat(act_signed_date)
        except ValueError:
            parsed_signed_date = None
        if (
            parsed_signed_date is None
            or parsed_signed_date.strftime("%d.%m.%Y") not in source_text
            and parsed_signed_date.isoformat() not in source_text
        ):
            rejected.append(f"Связь {index + 1}: дата подписания изменяющего акта не подтверждена.")
            continue
        if not publication_id or publication_id not in source_text:
            rejected.append(f"Связь {index + 1}: идентификатор публикации не найден в источнике.")
            continue
        if not amendment_quote or amendment_quote not in source_text:
            rejected.append(f"Связь {index + 1}: цитата изменения не найдена дословно.")
            continue
        if (
            not target_act_quote
            or target_act_quote not in source_text
            or target_act_quote not in amendment_quote
        ):
            rejected.append(f"Связь {index + 1}: цитата не подтверждает идентичность изменяемого закона.")
            continue
        article_match = re.search(r"Статья\s+(\d+(?:\.\d+)?)", provision_id, re.IGNORECASE)
        if not article_match:
            rejected.append(f"Связь {index + 1}: не удалось разобрать номер изменённой статьи.")
            continue
        article_number = re.escape(article_match.group(1))
        if not re.search(
            r"(?:внести|вносятся|внесение|изложить|дополнить|признать)\b"
            r".{0,600}(?:в\s+)?стать[ьяеюи]\s+" + article_number + r"(?:\D|$)",
            amendment_quote,
            re.IGNORECASE | re.DOTALL,
        ):
            rejected.append(
                f"Связь {index + 1}: цитата не связывает изменяющий акт с изменённой статьёй."
            )
            continue
        effective_from = candidate.get("effective_from")
        effective_date_quote = str(candidate.get("effective_date_quote") or "").strip()
        transition_verified = bool(transition_quote and transition_quote in source_text)
        parsed_effective = _iso_date(effective_from)
        article_specific_effective_clause = bool(
            re.search(r"вступает\s+в\s+силу", effective_date_quote, re.IGNORECASE)
            and re.search(
                r"стать[ьяеюи]\s+" + article_number,
                effective_date_quote,
                re.IGNORECASE,
            )
        )
        if (
            parsed_effective is None
            or not effective_date_quote
            or not transition_verified
            or effective_date_quote not in transition_quote
            or effective_date_quote not in source_text
            or not article_specific_effective_clause
        ):
            parsed_effective = None
            effective_date_quote = ""
        elif (
            parsed_effective.strftime("%d.%m.%Y") not in transition_quote
            and parsed_effective.isoformat() not in transition_quote
        ):
            parsed_effective = None
            effective_date_quote = ""
        linked.append({
            "provision_id": provision_id,
            "change_window": {
                "from_snapshot_date": change.get("from_snapshot_date"),
                "to_snapshot_date": change.get("to_snapshot_date"),
            },
            "act_number": act_number,
            "act_signed_date": parsed_signed_date.isoformat(),
            "act_title": act_title,
            "official_publication_id": publication_id,
            "official_url": source_url,
            "amendment_quote": amendment_quote,
            "transition_clause_found": transition_verified,
            "transition_quote": transition_quote if transition_verified else "",
            "effective_from_candidate": parsed_effective.isoformat() if parsed_effective else None,
            "effective_date_quote": effective_date_quote,
            "link_verified": True,
            "effective_date_verified": bool(parsed_effective),
            "verified": False,
            "limitation": (
                "Дословная связь с изменяющим актом подтверждена, но полнота цепочки поправок "
                "и применимость переходных положений не доказаны."
            ),
        })
    return {
        "status": "candidate_links_found" if linked else "no_verified_amendment_links",
        "links": linked,
        "link_count": len(linked),
        "rejected": rejected,
        "verified": False,
        "coverage_verified": False,
    }
