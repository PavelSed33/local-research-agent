#!/usr/bin/env python3
"""Fail closed on potentially unlicensed legal data accidentally committed to Git.

This is a preventive repository check, not a copyright ownership determination.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BINARY_EXTENSIONS = {".pdf", ".doc", ".docx", ".rtf", ".zip", ".rar", ".7z", ".epub"}
RESTRICTED_PATH_PARTS = {"legal_uploads", "legal_corpus", "case_files", "downloads"}
# Only the quarantine manifest may remain; old article JSON extracts are blocked.
QUARANTINED_CORPUS_DIR = pathlib.PurePosixPath("fixtures/statute_corpus")
# A brand mention is not evidence of infringement. Flag only explicit ownership notices.
PROPRIETARY_MARKERS = re.compile(
    r"(?i)(?:©|copyright|все права защищены)\s*"
    r"(?:ооо\s*)?(?:консультант\s*плюс|consultantplus|гарант|garant)"
    r"|(?:консультант\s*плюс|consultantplus|гарант|garant)[\s—–:,-]*"
    r"(?:©|copyright|все права защищены)"
)


def main() -> int:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    )
    violations: list[str] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        name = raw.decode("utf-8", errors="replace")
        path = pathlib.PurePosixPath(name)
        if path.parent == QUARANTINED_CORPUS_DIR and path.name != "manifest.json":
            violations.append(f"Quarantined statutory extract must not be tracked: {name}")
            continue
        if path.suffix.lower() in BINARY_EXTENSIONS:
            violations.append(f"Tracked document/archive requires rights review: {name}")
            continue
        if any(part.lower() in RESTRICTED_PATH_PARTS for part in path.parts):
            violations.append(f"Tracked restricted data folder: {name}")
            continue
        file = ROOT / name
        if file.is_file() and file.stat().st_size < 2_000_000 and file.suffix.lower() in {".md", ".txt", ".json", ".csv"}:
            try:
                body = file.read_text(encoding="utf-8")
            except (UnicodeError, OSError):
                continue
            if PROPRIETARY_MARKERS.search(body):
                violations.append(f"Possible proprietary content requires manual review: {name}")
    if violations:
        print("\n".join(violations), file=sys.stderr)
        print("BLOCKED: review licensing and remove unauthorized content.", file=sys.stderr)
        return 1
    print("Repository copyright hygiene check passed (current tracked files only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
