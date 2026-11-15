"""Register a locally obtained official PDF as an unverified candidate, never as corpus text."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

MAX_BYTES = 25_000_000


def register(pdf: Path, *, act_id: str, official_url: str):
    from scripts.monitor_official_sources import allowed_url
    parsed = urlsplit(official_url)
    if not allowed_url(official_url) or not (
        parsed.hostname == "publication.pravo.gov.ru" and
        (parsed.path.startswith("/document/") or parsed.path == "/File/Pdf")
    ):
        raise ValueError("Exact official publication document URL is required")
    if parsed.path.startswith("/document/") and not (
        len(parsed.path) == len("/document/") + 16 and parsed.path[len("/document/"):].isdigit()
    ):
        raise ValueError("Official document URL must contain a 16-digit publication number")
    if parsed.path == "/File/Pdf" and not parsed.query.startswith("eoNumber="):
        raise ValueError("Official PDF URL requires eoNumber")
    if not act_id or len(act_id) > 128:
        raise ValueError("Invalid act identifier")
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        raise ValueError("Existing PDF file required")
    with pdf.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if not data.startswith(b"%PDF-") or len(data) > MAX_BYTES:
        raise ValueError("Invalid or oversized PDF")
    return {
        "act_id": act_id,
        "official_source_url": official_url,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "status": "local_pdf_unverified",
        "edition_verified": False,
        "legal_reviewed": False,
        "accepted": False,
        "corpus_imported": 0,
        "note": "Local file remains outside repository and corpus until provenance, edition and rights review."
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--act-id", required=True)
    parser.add_argument("--official-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = register(args.pdf, act_id=args.act_id, official_url=args.official_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
