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
