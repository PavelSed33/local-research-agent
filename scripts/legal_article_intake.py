"""Strict ingestion gate for article text from an independently captured official publication.

A URL alone does not prove provenance: this verifies captured bytes and an
explicit human acceptance flag. It does not scrape or accept publisher PDFs.
"""
import hashlib
from pathlib import Path
from scripts.legal_article_versions import LegalVersionError, select_article_version
from scripts.legal_article_search import search_verified_article


def accept_article_record(record, source_bytes, *, as_of):
    if not isinstance(source_bytes, bytes) or not source_bytes:
        raise LegalVersionError("official source bytes are required")
    actual = hashlib.sha256(source_bytes).hexdigest()
    if not isinstance(record, dict):
        raise LegalVersionError("article record must be an object")
    if record.get("source_sha256") != actual:
        raise LegalVersionError("official source checksum mismatch")
    if record.get("reviewer_accepted") is not True:
        raise LegalVersionError("human legal review required")
    if not record.get("article_id") or not record.get("amendment_history_checked"):
        raise LegalVersionError("article identity or amendment history not verified")
    result = search_verified_article([record], article_id=record["article_id"], as_of=as_of)
    if result["status"] != "verified":
        raise LegalVersionError(result["reason"])
    return select_article_version([record], as_of, record["article_id"])


def accept_article_file(record, source_path, *, as_of):
    return accept_article_record(record, Path(source_path).read_bytes(), as_of=as_of)
