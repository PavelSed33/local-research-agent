"""Strict reconstruction of effective periods for an individual legal provision.

Edition labels and publication dates are never treated as effective dates.
Only explicitly scoped, officially sourced effective-date events are accepted.
"""
from __future__ import annotations

from datetime import date, timedelta
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
        "changed_provisions": changed[:200],
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
