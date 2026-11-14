"""Validate official-law registry; fail closed if source or edition is unverified."""
import argparse
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

ALLOWED_HOSTS = {"publication.pravo.gov.ru", "pravo.gov.ru"}

def audit(registry, as_of=None):
    as_of = as_of or date.today()
    errors, pending = [], []
    ids = set()
    for act in registry.get("acts", []):
        aid = act.get("id")
        if not aid or aid in ids:
            errors.append("missing or duplicate act id: " + str(aid))
        ids.add(aid)
        url = act.get("official_document_url")
        if url:
            u = urlsplit(url)
            if u.scheme != "https" or u.hostname not in ALLOWED_HOSTS or u.username or u.password or u.fragment:
                errors.append(str(aid) + ": invalid official URL")
        verified = act.get("last_verified_edition")
        approved = act.get("approved_for_legal_search", False)
        if approved:
            if not url or not verified or not act.get("source_sha256") or not act.get("reviewed_by"):
                errors.append(str(aid) + ": approval lacks evidence")
            else:
                try:
                    if date.fromisoformat(verified) > as_of:
                        errors.append(str(aid) + ": future verification date")
                except (TypeError, ValueError):
                    errors.append(str(aid) + ": invalid verification date")
        else:
            pending.append(aid)
    return {"total": len(registry.get("acts", [])), "pending": pending, "errors": errors, "safe": not errors}

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
