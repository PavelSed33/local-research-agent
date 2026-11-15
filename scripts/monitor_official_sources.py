"""Read-only official-source monitor. Never imports legal text or approves editions."""
import argparse
import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "fixtures" / "official_source_candidates.json"
ALLOWED = {"publication.pravo.gov.ru", "pravo.gov.ru", "www.pravo.gov.ru"}
MAX_URL = 2048


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def allowed_url(url):
    if not isinstance(url, str) or len(url) > MAX_URL:
        return False
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in ALLOWED
                and parsed.username is None and parsed.password is None
                and parsed.port is None and not parsed.fragment)
    except ValueError:
        return False


def check(registry, *, online=False):
    opener = build_opener(NoRedirect) if online else None
    results = []
    for act in registry["acts"]:
        url = act.get("source_document_url")
        result = {"id": act["id"], "title": act["title"],
                  "source_document_url": url, "status": "needs_official_document_url",
                  "verified": False, "imported": False}
        if url is not None:
            result["status"] = "candidate_url_unverified" if allowed_url(url) else "rejected_url"
            if online and allowed_url(url):
                try:
                    response = opener.open(Request(url, method="HEAD", headers={
                        "User-Agent": "LegalSourceMetadataMonitor/1.0"}), timeout=8)
                    with response:
                        result["http_status"] = response.status
                        result["status"] = "reachable_unverified" if response.status == 200 else "http_error"
                except HTTPError as exc:
                    result["http_status"] = exc.code
                    result["status"] = "http_error"
                except (URLError, OSError, TimeoutError, ValueError) as exc:
                    result["status"] = "network_error"
                    result["error_type"] = type(exc).__name__
        results.append(result)
    return {"checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "mode": "online_head_only" if online else "offline",
            "total": len(results),
            "needs_url": sum(r["status"] == "needs_official_document_url" for r in results),
            "reachable_unverified": sum(r["status"] == "reachable_unverified" for r in results),
            "legal_acceptance": False, "full_text_imported": 0, "acts": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--online", action="store_true", help="HEAD only; no body downloads")
    parser.add_argument("--output", type=Path, help="Write JSON diagnostic report")
    args = parser.parse_args()
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    report = check(registry, online=args.online)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
