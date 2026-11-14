"""Explicit source admission policy for the legal research corpus.

This is an allowlist of origin and handling constraints, NOT a license grant.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


class SourceAdmissionError(ValueError):
    pass


@dataclass(frozen=True)
class SourceDecision:
    url: str
    category: str
    storage_policy: str
    approved_for_full_text: bool = False


OFFICIAL_HOSTS = frozenset({
    "pravo.gov.ru", "publication.pravo.gov.ru", "www.pravo.gov.ru",
})
OFFICIAL_COURT_HOSTS = frozenset({
    "vsrf.ru", "www.vsrf.ru", "kad.arbitr.ru", "arbitr.ru", "www.arbitr.ru",
})
PROPRIETARY_HOSTS = frozenset({
    "consultant.ru", "www.consultant.ru", "garant.ru", "www.garant.ru",
})


def classify_source(url: str) -> SourceDecision:
    """Fail closed on unknown, ambiguous or commercial sources."""
    if not isinstance(url, str):
        raise SourceAdmissionError("Source URL must be a string.")
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise SourceAdmissionError("Malformed source URL.") from exc
    if (parsed.scheme != "https" or not host or parsed.username is not None
            or parsed.password is not None or parsed.fragment or port not in (None, 443)):
        raise SourceAdmissionError("Only canonical HTTPS source URLs are allowed.")
    if host in PROPRIETARY_HOSTS:
        raise SourceAdmissionError("Commercial legal reference content is not admitted without a separate license.")
    if parsed.path.startswith("//") or "\\" in parsed.path:
        raise SourceAdmissionError("Ambiguous URL path is not allowed.")
    if host in OFFICIAL_HOSTS:
        return SourceDecision(url, "official_legal_publication", "metadata_and_short_excerpts")
    if host in OFFICIAL_COURT_HOSTS or host.endswith(".sudrf.ru"):
        return SourceDecision(url, "official_court", "metadata_and_short_excerpts")
    raise SourceAdmissionError("Source not on the approved allowlist; manual review required.")


def admit_record(record: dict) -> dict:
    """Validate minimal provenance and prevent accidental full-text ingestion."""
    if not isinstance(record, dict):
        raise SourceAdmissionError("Record must be a JSON object.")
    decision = classify_source(record.get("source_url"))
    forbidden_payload_keys = {"full_text", "raw_content", "pdf_base64", "content", "text", "body", "html", "markdown", "pdf_bytes", "attachment"}
    if forbidden_payload_keys.intersection(record):
        raise SourceAdmissionError("Document content ingestion requires a separately approved workflow.")
    fetched_at = record.get("fetched_at")
    if not isinstance(fetched_at, str) or not fetched_at.strip():
        raise SourceAdmissionError("Missing retrieval timestamp.")
    document_id = record.get("document_id")
    if not isinstance(document_id, str) or not document_id.strip():
        raise SourceAdmissionError("Missing document identifier.")
    return {
        "document_id": document_id.strip(),
        "source_url": decision.url,
        "fetched_at": fetched_at.strip(),
        "source_category": decision.category,
        "storage_policy": decision.storage_policy,
        "license_review": "pending",
    }
