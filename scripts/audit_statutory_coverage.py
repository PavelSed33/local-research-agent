"""Read-only coverage audit for the quarantined statutory corpus.

This report does not grant legal acceptance or authorize commercial use.
"""
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from statute_corpus import corpus_report


def audit():
    registry = json.loads((ROOT / "fixtures/legal_coverage.json").read_text(encoding="utf-8"))
    cards = {card["id"]: card for card in registry["cards"]}
    report = corpus_report()
    received = {entry["card_id"] for entry in report["entries"]}
    missing = sorted(set(cards) - received)
    unexpected = sorted(received - set(cards))
    by_module = Counter(cards[card_id]["module_id"] for card_id in missing)
    manifest = json.loads((ROOT / "fixtures/statute_corpus/manifest.json").read_text(encoding="utf-8"))
    return {
        "status": "incomplete" if missing or unexpected or report["errors"] else "structurally_complete",
        "declared_cards": len(cards),
        "available_text_extracts": len(received),
        "missing_count": len(missing),
        "missing_card_ids": missing,
        "unexpected_card_ids": unexpected,
        "missing_by_module": dict(sorted(by_module.items())),
        "corpus_errors": report["errors"],
        "quarantine_gaps": manifest.get("gaps", []),
        "legal_acceptance": False,
        "commercial_use_allowed": False,
        "note": "Structural completeness is not proof of source authenticity, licensing, legal validity or edition history.",
    }


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
