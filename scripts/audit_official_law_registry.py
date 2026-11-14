"""Audit the official-law watchlist. Fail closed on unsupported approvals.

This audit verifies metadata consistency only. It cannot certify the legal
validity or effective date of a statute.
"""
import argparse
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

ALLOWED_HOSTS = {"publication.pravo.gov.ru", "pravo.gov.ru"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PUBLICATION_PATH = re.compile(r"^/document/\d{16}/?$")

def audit(registry, as_of=None):
    as_of = as_of or date.today()
    errors, pending, approved_ids = [], [], []
    acts = registry.get("acts")
    if not isinstance(acts, list) or not acts:
        return {"total": 0, "pending": [], "approved": [], "errors": ["empty or invalid acts list"], "safe": False}
    ids = set()
    for act in acts:
        if not isinstance(act, dict):
            errors.append("act must be an object")
            continue
        aid = act.get("id")
        if not isinstance(aid, str) or not aid.strip() or aid in ids:
            errors.append("missing or duplicate act id: " + str(aid))
        ids.add(aid)
        url = act.get("official_document_url")
        valid_url = False
        if url is not None:
            if not isinstance(url, str):
                errors.append(str(aid) + ": invalid official URL")
            else:
                try:
                    u = urlsplit(url)
                    valid_url = (u.scheme == "https" and u.hostname in ALLOWED_HOSTS
                                 and u.port is None and not u.username and not u.password
                                 and not u.query and not u.fragment
                                 and (u.hostname != "publication.pravo.gov.ru"
                                      or bool(PUBLICATION_PATH.fullmatch(u.path))))
                except ValueError:
                    valid_url = False
                if not valid_url:
                    errors.append(str(aid) + ": invalid official URL")
        approved = act.get("approved_for_legal_search", False)
        if not isinstance(approved, bool):
            errors.append(str(aid) + ": approval must be boolean")
            continue
        if not approved:
            pending.append(aid)
            continue
        required_flags = ("official_text_verified", "edition_verified", "rights_reviewed")
        if not valid_url or not all(act.get(flag) is True for flag in required_flags):
            errors.append(str(aid) + ": approval lacks verified official source, edition or rights review")
        digest = act.get("source_sha256")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            errors.append(str(aid) + ": invalid SHA-256")
        if not isinstance(act.get("reviewed_by"), str) or not act["reviewed_by"].strip():
            errors.append(str(aid) + ": reviewer missing")
        verified = act.get("last_verified_edition")
        try:
            if not isinstance(verified, str) or date.fromisoformat(verified) > as_of:
                raise ValueError()
        except ValueError:
            errors.append(str(aid) + ": invalid or future verification date")
        if not any(e.startswith(str(aid) + ":") for e in errors):
            approved_ids.append(aid)
    return {"total": len(acts), "pending": pending, "approved": approved_ids,
            "errors": errors, "safe": not errors}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("registry", nargs="?", default="fixtures/official_uploads/official_law_registry.json")
    a = p.parse_args()
    result = audit(json.loads(Path(a.registry).read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False))
    if result["errors"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
