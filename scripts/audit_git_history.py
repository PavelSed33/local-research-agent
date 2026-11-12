#!/usr/bin/env python3
"""Inventory potentially licensed files in *all reachable Git history*.

Read-only audit. This does not determine ownership or rewrite Git history.
Run after 'git fetch --all --tags' in a full (non-shallow) clone.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

BLOCKED_EXTENSIONS = {".pdf", ".doc", ".docx", ".rtf", ".zip", ".rar", ".7z", ".epub"}
SENSITIVE_PARTS = {"legal_uploads", "legal_corpus", "case_files", "downloads"}
NAME_MARKERS = ("consultantplus", "consultant", "консультант", "garant", "гарант")


def audit_objects() -> list[tuple[str, str]]:
    listing = subprocess.run(
        ["git", "rev-list", "--objects", "--all"],
        check=True, capture_output=True,
    ).stdout
    findings: list[tuple[str, str]] = []
    seen: set[str] = set()
    # Git rev-list emits newline-delimited records; unusual filenames need
    # separate manual inspection if they contain line breaks.
    for record in listing.splitlines():
        if b" " not in record:
            continue
        oid, raw_name = record.split(b" ", 1)
        name = raw_name.decode("utf-8", errors="replace")
        path = pathlib.PurePosixPath(name)
        if (path.suffix.lower() in BLOCKED_EXTENSIONS
                or any(part.lower() in SENSITIVE_PARTS for part in path.parts)
                or any(marker in name.casefold() for marker in NAME_MARKERS)):
            key = oid.decode("ascii", errors="replace") + ":" + name
            if key not in seen:
                seen.add(key)
                findings.append((oid.decode("ascii", errors="replace"), name))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-on-findings", action="store_true")
    args = parser.parse_args()
    findings = audit_objects()
    print("HISTORICAL FILE INVENTORY (manual rights review required)")
    for oid, name in findings:
        print(f"{oid}  {name}")
    print(f"Flagged historical paths: {len(findings)}")
    print("A zero count does not prove copyright clearance: textual content, "
          "submodules, LFS, releases and external artifacts need separate review.")
    return 1 if findings and args.fail_on_findings else 0


if __name__ == "__main__":
    sys.exit(main())
