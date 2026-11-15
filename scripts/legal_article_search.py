"""Read-only legal search adapter: returns citations only for accepted article records.

No direct indexing of PDFs or secondary-source article texts is permitted.
"""
import hashlib
from scripts.legal_article_versions import LegalVersionError, select_article_version


def search_verified_article(records, *, article_id, as_of):
    """Return a cited article result, or a transparent unverified status."""
    try:
        version = select_article_version(records, as_of, article_id)
        # Bind the exact returned article text to its reviewed SHA-256.
        text_digest = version.get("article_text_sha256")
        computed = hashlib.sha256(version["text"].encode("utf-8")).hexdigest()
        if not isinstance(text_digest, str) or text_digest != computed:
            raise LegalVersionError("article text SHA-256 mismatch or missing")
        if version.get("production_approved") is not True:
            raise LegalVersionError("production approval missing")
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
