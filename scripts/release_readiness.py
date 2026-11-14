"""Fail-closed release evidence gate. This does not execute tests or grant legal approval.

Usage: python scripts/release_readiness.py [evidence.json]
Only explicit, externally verified PASS evidence counts. Missing/stale evidence fails.
"""
import json
from datetime import date, timedelta
from pathlib import Path
import sys

GATES = (
    "python_3_10", "python_3_11", "python_3_12", "security",
    "docker", "http", "archive", "legal_sources", "ocr",
    "backups", "legal_acceptance",
)
MAX_EVIDENCE_AGE_DAYS = 14


def assess(evidence, *, today=None):
    today = today or date.today()
    if not isinstance(evidence, dict) or set(evidence) != set(GATES):
        raise ValueError("Evidence must contain exactly all release gates")
    results = {}
    for gate in GATES:
        item = evidence[gate]
        passed = False
        reason = "missing or unverified evidence"
        if isinstance(item, dict):
            stamp = item.get("verified_on")
            proof = item.get("proof_url")
            if isinstance(stamp, str):
                try:
                    checked = date.fromisoformat(stamp)
                    recent = checked <= today and today - checked <= timedelta(days=MAX_EVIDENCE_AGE_DAYS)
                except ValueError:
                    recent = False
            else:
                recent = False
            if (item.get("status") == "pass" and recent
                    and isinstance(proof, str) and proof.startswith("https://")
                    and len(proof) > 12 and not any(c.isspace() for c in proof)):
                passed = True
                reason = "verified pass claimed; review linked evidence"
            elif item.get("status") == "fail":
                reason = "reported failure"
        results[gate] = {"passed": passed, "reason": reason}
    count = sum(item["passed"] for item in results.values())
    return {
        "passed": count, "total": len(GATES),
        "percentage": round(100 * count / len(GATES), 1),
        "at_least_90_percent": count * 10 >= len(GATES) * 9,
        "release_approved": False,
        "gates": results,
        "note": "Self-reported evidence is not independently verified; human legal acceptance is required.",
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) > 1:
        raise SystemExit("Usage: python scripts/release_readiness.py [evidence.json]")
    if argv:
        path = Path(argv[0])
        if not path.is_file() or path.stat().st_size > 65536:
            raise ValueError("Missing or oversized evidence file")
        evidence = json.loads(path.read_text(encoding="utf-8"))
    else:
        evidence = {name: None for name in GATES}
    result = assess(evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["at_least_90_percent"] and result["gates"]["legal_acceptance"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
