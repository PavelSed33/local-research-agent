"""Stage independently acquired official legal texts for review, never auto-approve.

Usage: python scripts/stage_official_law.py ACT_ID PUBLICATION_URL FILE OUTPUT_DIR
The supplied FILE must be acquired independently from the official portal.
This tool does not scrape, alter the production corpus, or claim a current edition.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

PUBLICATION_ID = re.compile(r"^[0-9]{16}$")
ACT_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def validate_official_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname != "publication.pravo.gov.ru"
            or parsed.username is not None or parsed.password is not None
            or parsed.port is not None or parsed.query or parsed.fragment
            or not parsed.path.startswith("/document/")
            or not PUBLICATION_ID.fullmatch(parsed.path.removeprefix("/document/"))):
        raise ValueError("Expected a canonical official publication document URL")
    return value


def stage(act_id, url, source_file, output_dir):
    if not ACT_ID.fullmatch(act_id):
        raise ValueError("Invalid act identifier")
    validate_official_url(url)
    source = Path(source_file)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Source must be a regular local file")
    if source.suffix.lower() not in {".pdf", ".txt", ".xml", ".html"}:
        raise ValueError("Unsupported source format")
    size = source.stat().st_size
    if not 0 < size <= 25_000_000:
        raise ValueError("Source size out of bounds")
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError("Output directory cannot be a symlink")
    target = output / f"{act_id}-{digest.hexdigest()[:16]}.json"
    if target.exists() or target.is_symlink():
        raise FileExistsError("Existing review record must not be overwritten")
    record = {
        "schema_version": 1, "act_id": act_id, "source_url": url,
        "source_file_name": source.name, "source_sha256": digest.hexdigest(),
        "source_size": size, "format": source.suffix.lower().lstrip("."),
        "source_kind": "official_publication_candidate",
        "original_publication_verified": False, "edition_verified": False,
        "history_complete": False, "legal_reviewed": False,
        "commercial_use_approved": False, "admitted_to_agent": False,
    }
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("act_id")
    parser.add_argument("publication_url")
    parser.add_argument("source_file")
    parser.add_argument("output_dir")
    args = parser.parse_args()
    print(stage(args.act_id, args.publication_url, args.source_file, args.output_dir))


if __name__ == "__main__":
    main()
