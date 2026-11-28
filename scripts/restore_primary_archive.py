"""Restore the original court PDFs from a pinned GitHub archive capture."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import primary_sources


ARCHIVE_COMMIT = "277a5c8a3bfa2e95dfadf05c0fe42e28a9d8e2ad"
CAPTURE_URL = (
    f"https://raw.githubusercontent.com/PavelSed33/local-research-agent/"
    f"{ARCHIVE_COMMIT}/fixtures/primary_sources/"
)
MAX_PDF_BYTES = 10 * 1024 * 1024


def restore(output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Validate both the tracked metadata and every existing destination PDF
    # using the same checks that guard the workspace API.
    inventory = primary_sources._load(primary_sources.ROOT, output_dir)
    missing = [row for row in inventory if not row["integrity_checked"]]
    restored = 0
    if missing:
        with tempfile.TemporaryDirectory(dir=output_dir) as temp:
            staged = Path(temp)
            for row in missing:
                name = row["original_file"]
                with urlopen(CAPTURE_URL + name, timeout=30) as response:
                    (staged / name).write_bytes(response.read(MAX_PDF_BYTES + 1))
                # Check PDF signature, size and manifest SHA-256 before publication.
                primary_sources.original_bytes(row, root=staged)

            for row in missing:
                name = row["original_file"]
                try:
                    os.link(staged / name, output_dir / name)
                except FileExistsError:
                    # Concurrent restoration may already have installed this PDF.
                    primary_sources.original_bytes(row, root=output_dir)
                else:
                    restored += 1

    return {"status": "passed", "archive_commit": ARCHIVE_COMMIT, "sources": len(inventory),
            "restored": restored, "already_present": len(inventory) - restored}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=os.environ.get("PRIMARY_ARCHIVE_PDF_DIR") or primary_sources.ROOT,
                        help="PDF directory (default: PRIMARY_ARCHIVE_PDF_DIR or fixtures/primary_sources)")
    args = parser.parse_args()
    try:
        report = restore(args.output_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
