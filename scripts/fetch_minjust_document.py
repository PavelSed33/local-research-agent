"""Quarantine a PDF from an explicitly reviewed Ministry of Justice document URL.

No scraping, crawling, or automatic admission to the legal answer corpus.
Usage: python -m scripts.fetch_minjust_document ACT_ID HTTPS_PDF_URL OUTPUT_DIR
"""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from scripts.fetch_official_publication import MAX_BYTES, NoRedirect
from scripts.stage_official_law import ACT_ID

ALLOWED_HOSTS = frozenset({"pravo.minjust.ru", "pravo-search.minjust.ru"})
def validate_url(url):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS
            or parts.username or parts.password or parts.port not in (None, 443)
            or parts.fragment or not parts.path.startswith("/")
            or "\\" in url or any(ord(c) < 32 for c in url)):
        raise ValueError("URL must be an exact HTTPS document URL on an approved Ministry of Justice host")
    return url

def fetch(act_id, url, output_dir, opener=None):
    if not ACT_ID.fullmatch(act_id):
        raise ValueError("Invalid act ID")
    validate_url(url)
    opener = opener or build_opener(NoRedirect())
    with opener.open(Request(url, headers={"User-Agent": "LegalSourceReview/1.0", "Accept": "application/pdf"}), timeout=25) as response:
        if response.geturl() != url:
            raise ValueError("Unexpected redirect")
        ctype = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if ctype != "application/pdf":
            raise ValueError("Response is not a PDF")
        size = response.headers.get("Content-Length")
        if size is not None and int(size) > MAX_BYTES:
            raise ValueError("PDF exceeds size limit")
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or not data.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF or excessive size")
    output = Path(output_dir)
    if output.is_symlink():
        raise ValueError("Output directory is a symlink")
    output.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    path = output / f"{act_id}-{digest[:16]}.pdf"
    meta = path.with_suffix(".json")
    if path.exists() or meta.exists() or path.is_symlink() or meta.is_symlink():
        raise FileExistsError("Quarantined source already exists")
    path.write_bytes(data)
    meta.write_text(json.dumps({
        "act_id": act_id, "source_url": url, "sha256": digest,
        "bytes": len(data), "status": "unreviewed",
        "full_text_imported": False, "edition_verified": False,
        "legal_reviewed": False, "commercial_use_allowed": False
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("act_id")
    parser.add_argument("url")
    parser.add_argument("output_dir")
    args = parser.parse_args()
    print(fetch(args.act_id, args.url, args.output_dir))
