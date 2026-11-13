"""Fetch a known official publication PDF into quarantine, never into the answer corpus.

Requires an independently checked official publication ID and matching act ID.
Usage: python scripts/fetch_official_publication.py ACT_ID PUBLICATION_ID OUTPUT_DIR
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.request import build_opener, HTTPRedirectHandler, Request
from urllib.error import HTTPError

from scripts.stage_official_law import ACT_ID

PUBLICATION_ID = re.compile(r"^[0-9]{16}$")
MAX_BYTES = 25_000_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirects are not permitted for official publication downloads")


def fetch(act_id, publication_id, output_dir, opener=None):
    if not ACT_ID.fullmatch(act_id) or not PUBLICATION_ID.fullmatch(publication_id):
        raise ValueError("Invalid act ID or publication number")
    # Prevent a known implementing order from being mislabeled as the parent statute.\n    if publication_id == "0001202506300007" and act_id != "pd152-access-order-2025":\n        raise ValueError("Publication belongs to an implementing order, not 152-FZ")\n    url = f"https://publication.pravo.gov.ru/file/pdf?eoNumber={publication_id}"
    opener = opener or build_opener(NoRedirect())
    response = opener.open(Request(url, headers={"User-Agent": "LegalSourceReview/1.0", "Accept": "application/pdf"}), timeout=25)
    with response:
        final_url = response.geturl()
        if final_url != url:
            raise ValueError("Unexpected download URL")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/pdf":
            raise ValueError("Official endpoint did not return a PDF")
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > MAX_BYTES:
            raise ValueError("PDF exceeds maximum size")
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or not data.startswith(b"%PDF-"):
        raise ValueError("Invalid or oversized PDF")
    digest = hashlib.sha256(data).hexdigest()
    output = Path(output_dir)
    if output.is_symlink():
        raise ValueError("Output directory must not be a symlink")
    output.mkdir(parents=True, exist_ok=True)
    pdf = output / f"{act_id}-{publication_id}-{digest[:16]}.pdf"
    metadata = pdf.with_suffix(".json")
    if pdf.exists() or metadata.exists() or pdf.is_symlink() or metadata.is_symlink():
        raise FileExistsError("Candidate already exists; refusing overwrite")
    record = {
        "schema_version": 1, "act_id": act_id,
        "publication_number": publication_id,
        "source_document_url": f"https://publication.pravo.gov.ru/document/{publication_id}",
        "download_url": url, "sha256": digest, "size_bytes": len(data),
        "document_type_verified": False, "document_number_verified": False,
        "document_date_verified": False, "title_verified": False,
        "edition_verified": False, "legal_reviewed": False,
        "admitted_to_agent": False,
    }
    with pdf.open("xb") as stream:
        stream.write(data)
    try:
        with metadata.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except BaseException:
        pdf.unlink()
        raise
    return pdf, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("act_id")
    parser.add_argument("publication_id")
    parser.add_argument("output_dir")
    args = parser.parse_args()
    print(*fetch(args.act_id, args.publication_id, args.output_dir), sep="\n")


if __name__ == "__main__":
    main()
