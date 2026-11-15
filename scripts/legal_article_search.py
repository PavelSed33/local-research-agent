"""Read-only legal search adapter: returns citations only for accepted article records.

No direct indexing of PDFs or secondary-source article texts is permitted.
"""
from scripts.legal_article_versions import LegalVersionError, select_article_version


def search_verified_article(records, *, article_id, as_of):
    """Return a cited article result, or a transparent unverified status."""
    try:
        version = select_article_version(records, as_of, article_id)
        if version.get("reviewer_accepted") is not True:
            raise LegalVersionError("human review missing")
        if version.get("amendment_history_checked") is not True:
            raise LegalVersionError("amendment history missing")
        return {
            "status": "verified",
            "article_id": article_id,
            "as_of": as_of,
            "text": version["text"],
            "source_url": version["official_publication_url"],
            "source_sha256": version["source_sha256"],
            "effective_from": version["effective_from"],
            "effective_until": version.get("effective_until"),
        }
    except LegalVersionError as exc:
        return {"status": "unverified", "article_id": article_id, "as_of": as_of,
                "text": None, "source_url": None, "reason": str(exc)}
