"""Test-only availability guard for deliberately quarantined archive originals.

Never generates, downloads or substitutes missing PDFs. Integration tests requiring
real archived bytes are skipped explicitly; separate fail-closed tests still run.
"""
import json
from primary_sources import ROOT


def originals_available():
    try:
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        return bool(manifest["sources"]) and all(
            (ROOT / row[field]).is_file()
            for row in manifest["sources"]
            for field in ("original_file", "text_file")
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False
