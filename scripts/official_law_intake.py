"""Fail-closed official-law intake. Explicit URLs only; no crawling.

Download is opt-in and limited to verified official publication document pages.
Downloaded bytes remain in quarantine; no legal-search admission.
"""
import argparse
import hashlib
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOST = "publication.pravo.gov.ru"
DOC = re.compile(r"^/document/(\d{16})/?$")
MAX_BYTES = 30 * 1024 * 1024

def validate_url(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname != HOST or u.port is not None or u.username or u.password or u.query or u.fragment:
        raise ValueError("Only canonical HTTPS publication.pravo.gov.ru document URLs are allowed")
    m = DOC.fullmatch(u.path)
    if not m:
        raise ValueError("Expected /document/<16-digit-publication-id>")
    return m.group(1)

def validate_public_ips(host):
    for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        ip = ipaddress.ip_address(item[4][0])
        if not ip.is_global:
            raise ValueError("Non-public DNS destination refused")

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirects refused")

def fetch(url, outdir, max_bytes=MAX_BYTES):
    publication_id = validate_url(url)
    validate_public_ips(HOST)
    request = urllib.request.Request(url, headers={"User-Agent": "LegalSourceIntake/1.0 (manual opt-in)"})
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(request, timeout=15) as response:
        content_type = response.headers.get_content_type()
        if content_type not in ("text/html", "application/pdf"):
            raise ValueError("Unexpected content type")
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise ValueError("Source exceeds configured limit")
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Source exceeds configured limit")
    if content_type == "application/pdf" and not data.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF signature")
    if content_type == "text/html" and b"<html" not in data[:8192].lower():
        raise ValueError("Unexpected HTML response")
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    suffix = ".pdf" if content_type == "application/pdf" else ".html"
    path = outdir / (publication_id + suffix)
    path.write_bytes(data)
    metadata = {
        "publication_id": publication_id, "source_url": url,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
        "content_type": content_type, "status": "quarantined",
        "official_text_verified": False, "edition_verified": False,
        "rights_reviewed": False, "approved_for_legal_search": False,
        "note": "HTML landing page is not the legal act PDF; do not treat it as verified text.",
    }
    (outdir / (publication_id + ".json")).write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return metadata

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("url", help="Exact manually approved official publication document URL")
    p.add_argument("--output", default="data/quarantine/official")
    p.add_argument("--download", action="store_true", help="Explicitly permit network request")
    a = p.parse_args()
    validate_url(a.url)
    if not a.download:
        print("DRY RUN: validated URL; no network request or file writes")
        return
    print(json.dumps(fetch(a.url, a.output), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
