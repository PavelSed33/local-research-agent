"""Fail-closed article-version selection by effective date.

No law text is bundled. Every returned article must have verified official provenance.
"""
from datetime import date
from urllib.parse import urlparse


class LegalVersionError(ValueError):
    pass


def _date(value, field):
    if not isinstance(value, str):
        raise LegalVersionError(f"{field}: ISO date required")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise LegalVersionError(f"{field}: invalid ISO date") from exc
    if parsed.isoformat() != value:
        raise LegalVersionError(f"{field}: YYYY-MM-DD required")
    return parsed


def _official_url(url):
    if not isinstance(url, str):
        return False
    p = urlparse(url)
    return p.scheme == "https" and p.hostname in {"publication.pravo.gov.ru", "pravo.gov.ru"}


def select_article_version(versions, as_of, article_id):
    """Return an official, legally accepted version or raise.

    Each version requires article_id, text, effective_from, effective_until
    (nullable, exclusive), published_on, official_publication_url,
    source_sha256 and verified=True. Overlaps and gaps are rejected.
    """
    target = _date(as_of, "as_of")
    matching = []
    for item in versions:
        if item.get("article_id") != article_id:
            continue
        start = _date(item.get("effective_from"), "effective_from")
        end = _date(item["effective_until"], "effective_until") if item.get("effective_until") else None
        published = _date(item.get("published_on"), "published_on")
        if end is not None and end <= start:
            raise LegalVersionError("invalid effective interval")
        if published > start:
            raise LegalVersionError("publication after claimed effective date: manual review required")
        if start <= target and (end is None or target < end):
            matching.append(item)
    if len(matching) != 1:
        raise LegalVersionError(f"expected one version on {as_of}; found {len(matching)}")
    selected = matching[0]
    if selected.get("verified") is not True:
        raise LegalVersionError("version not legally verified")
    if not _official_url(selected.get("official_publication_url")):
        raise LegalVersionError("official publication URL not verified")
    digest = selected.get("source_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise LegalVersionError("source SHA-256 missing or invalid")
    if not isinstance(selected.get("text"), str) or not selected["text"].strip():
        raise LegalVersionError("article text missing")
    return selected
