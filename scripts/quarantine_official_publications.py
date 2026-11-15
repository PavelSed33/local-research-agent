"""Download bounded official publication candidates into temporary quarantine only.

Never treats a publication as a verified current legal edition. Output is not the
statutory corpus and must not be committed or shipped in Docker.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from scripts.monitor_official_sources import NoRedirect, allowed_url

DOCUMENT_URL = re.compile(r"^https://publication\.pravo\.gov\.ru/document/\d{16}$", re.I)
MAX_BYTES = 3_000_000


def quarantine(report, output, *, limit=5, opener=None):
    opener = opener or build_opener(NoRedirect)
    output.mkdir(parents=True, exist_ok=True)
    records = []
    seen = set()
    for act in report.get("results", []):
        for candidate in act.get("candidates", []):
            url = candidate.get("url", "")
            if url in seen or not allowed_url(url) or not DOCUMENT_URL.fullmatch(url):
                continue
            seen.add(url)
            if len(records) >= limit:
                break
            record = {"act_id": act.get("id"), "url": url, "accepted": False,
                      "edition_verified": False, "legal_reviewed": False}
            try:
                with opener.open(Request(url, headers={"User-Agent": "LegalPublicationQuarantine/1.0"}),
                                 timeout=12) as response:
                    if response.status != 200:
                        raise ValueError("Unexpected HTTP status")
                    mime = response.headers.get("Content-Type", "").split(";")[0].lower().strip()
                    if mime not in {"text/html", "application/pdf"}:
                        raise ValueError("Unsupported content type")
                    data = response.read(MAX_BYTES + 1)
                    if len(data) > MAX_BYTES:
                        raise ValueError("Oversized document")
                    if not data:
                        raise ValueError("Empty response")
                    if mime == "application/pdf" and not data.startswith(b"%PDF-"):
                        raise ValueError("Invalid PDF header")
                    if mime == "text/html" and b"<html" not in data[:8192].lower() and b"<!doctype html" not in data[:8192].lower():
                        raise ValueError("Invalid HTML header")
                    digest = hashlib.sha256(data).hexdigest()
                    suffix = ".pdf" if mime == "application/pdf" else ".html"
                    filename = digest + suffix
                    (output / filename).write_bytes(data)
                    record.update({"status": "quarantined_unverified", "sha256": digest,
                                   "bytes": len(data), "content_type": mime, "filename": filename})
            except (HTTPError, URLError, OSError, ValueError, TimeoutError) as exc:
                record.update({"status": "download_error", "error_type": type(exc).__name__})
            records.append(record)
        if len(records) >= limit:
            break
    result = {"items": records, "downloaded": sum(x.get("status") == "quarantined_unverified" for x in records),
              "legal_acceptance": False, "corpus_imported": 0}
    (output / "quarantine_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10:
        parser.error("--limit must be between 1 and 10")
    report = json.loads(args.candidates.read_text(encoding="utf-8"))
    print(json.dumps(quarantine(report, args.output, limit=args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
