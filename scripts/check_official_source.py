"""Validate metadata for a proposed official-law source without downloading or trusting its text.

This is an intake gate, not legal review, copyright clearance or publication.
Usage: python scripts/check_official_source.py proposal.json
"""
import ipaddress
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ALLOWED_HOSTS = frozenset({
    "publication.pravo.gov.ru",
    "pravo.gov.ru",
    "www.pravo.gov.ru",
})
REQUIRED = frozenset({"url", "document_id", "captured_on", "source_kind", "rights_reviewed"})
MAX_BYTES = 16_384


def validate_proposal(data):
    if not isinstance(data, dict) or set(data) != REQUIRED:
        raise ValueError("Proposal must contain exactly the required metadata fields")
    if data["source_kind"] != "official_publication":
        raise ValueError("Only official publications are eligible for this intake")
    if data["rights_reviewed"] is not True:
        raise ValueError("Rights review must be completed before ingestion")
    if not isinstance(data["document_id"], str) or not (1 <= len(data["document_id"]) <= 120):
        raise ValueError("Invalid document ID")
    from datetime import date
    try:
        if not isinstance(data["captured_on"], str) or date.fromisoformat(data["captured_on"]).isoformat() != data["captured_on"]:
            raise ValueError("Invalid date")
    except (TypeError, ValueError) as exc:
        raise ValueError("captured_on must be ISO date YYYY-MM-DD") from exc
    url = data["url"]
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("Invalid URL")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (parsed.scheme != "https" or host not in ALLOWED_HOSTS or
                parsed.username is not None or parsed.password is not None or
                parsed.port is not None or parsed.query or parsed.fragment or
                not parsed.path.startswith("/")):
            raise ValueError("URL must be pinned to an approved official HTTPS host")
        if host is None or host.endswith(".") or ipaddress.ip_address(host):
            raise ValueError("IP literals are forbidden")
    except ValueError as exc:
        if str(exc) == "URL must be pinned to an approved official HTTPS host":
            raise
        if str(exc) == "IP literals are forbidden":
            raise
        raise ValueError("Invalid URL") from exc
    return {
        "status": "metadata_eligible_only",
        "document_id": data["document_id"],
        "url": url,
        "rights_reviewed": True,
        "text_imported": False,
        "legal_edition_verified": False,
        "commercial_use_allowed": False,
    }


def main(path):
    p = Path(path)
    if not p.is_file() or p.stat().st_size > MAX_BYTES:
        raise ValueError("Missing or oversized proposal")
    data = json.loads(p.read_text(encoding="utf-8"))
    print(json.dumps(validate_proposal(data), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/check_official_source.py proposal.json")
    main(sys.argv[1])
