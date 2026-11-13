"""Report missing external legal source artifacts without restoring removed documents.

This diagnostic is read-only and does not change test outcomes.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "fixtures" / "official_source_candidates.json"
MANIFEST = ROOT / "fixtures" / "statute_corpus" / "manifest.json"

def main():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    acts = registry.get("acts", [])
    imported = sum(bool(a.get("full_text_imported")) for a in acts)
    reviewed = sum(bool(a.get("legal_reviewed")) for a in acts)
    print(f"Official source candidates: {len(acts)}")
    print(f"Full texts reported imported: {imported}")
    print(f"Acts reported legally reviewed: {reviewed}")
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        print(f"Statute manifest status: {manifest.get('status', 'unspecified')}")
    if not imported:
        print("BLOCKER: no official full-text legal corpus is accepted")
    if not reviewed:
        print("BLOCKER: no legally reviewed statutes in candidate registry")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
