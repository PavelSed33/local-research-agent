"""Find unverified official publication candidates via the portal's read-only JSON API."""
import argparse
import datetime as dt
import json
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from scripts.monitor_official_sources import NoRedirect, REGISTRY

BASE = "https://publication.pravo.gov.ru"
EO = re.compile(r"^\d{16}$")
MAX_BYTES = 1_000_000


def api_candidates(registry, *, fetch=None, limit=5):
    if fetch is None:
        opener = build_opener(NoRedirect)
        def fetch(url):
            with opener.open(Request(url, headers={"Accept": "application/json",
                                                   "User-Agent": "OfficialLegalDiscovery/1.0"}),
                             timeout=4) as response:
                if response.status != 200:
                    raise ValueError("HTTP status not 200")
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError("Oversized JSON response")
                return json.loads(data)
    results = []
    errors = 0
    # Prioritize acts signed after electronic official publication began in 2011.
    # Older statutes remain eligible but their original texts may not exist in this API.
    ordered = sorted(registry["acts"], key=lambda act: (
        str(act.get("signed_on", "")) < "2011-10-21",
        str(act.get("signed_on", "")), str(act.get("id", ""))))
    for act in ordered[:limit]:
        item = {"id": act["id"], "number": act.get("number"),
                "candidates": [], "verified": False, "imported": False,
                "edition_scope": "original_official_publication_only",
                "current_consolidated_edition_required": True,
                "original_predates_electronic_publication": str(act.get("signed_on", "")) < "2011-10-21"}
        if errors >= 3:
            item["status"] = "network_circuit_open"
            results.append(item)
            continue
        try:
            signed = dt.date.fromisoformat(act["signed_on"]).isoformat()
            number = act["number"]
            url = BASE + "/api/Documents?" + urlencode({
                "Number": number, "NumberSearchType": 0,
                "DocumentDateFrom": signed, "DocumentDateTo": signed,
                "PageSize": 30, "Index": 1})
            data = fetch(url)
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ValueError("Unexpected API JSON schema")
            errors = 0
            for hit in data["items"][:30]:
                if not isinstance(hit, dict):
                    continue
                eo = str(hit.get("eoNumber", ""))
                hit_number = str(hit.get("number", "")).strip()
                date = str(hit.get("documentDate", ""))[:10]
                if EO.fullmatch(eo) and hit_number.casefold() == number.casefold() and date == signed:
                    item["candidates"].append({
                        "url": BASE + "/document/" + eo,
                        "publication_number": eo,
                        "matched_link_text": str(hit.get("title") or hit.get("name") or "")[:350],
                        "status": "candidate_requires_manual_identity_edition_rights_review",
                        "verified": False})
            item["status"] = "candidate_found" if item["candidates"] else "no_exact_match_in_api_response"
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            errors += 1
            item["status"] = "official_api_network_error"
            item["error_type"] = type(exc).__name__
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            item["status"] = "api_schema_or_metadata_error"
            item["error_type"] = type(exc).__name__
        results.append(item)
    return {"source": BASE + "/api/Documents", "acts_checked": len(results),
            "candidates_found": sum(len(i["candidates"]) for i in results),
            "legal_acceptance": False, "full_text_imported": 0,
            "warning": "Original publications do not establish the current consolidated edition; older acts may predate electronic publication.",
            "results": results}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--limit", type=int, default=5)
    args = p.parse_args()
    if not 1 <= args.limit <= 59:
        p.error("--limit must be 1..59")
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    args.output.write_text(json.dumps(api_candidates(registry, limit=args.limit),
                                      ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
