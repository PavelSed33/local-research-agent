#!/usr/bin/env python3
"""Fail-closed validation of uploaded legal-document inventory metadata.

This tool never reads, exports or indexes copyrighted publisher PDFs.
"""
import argparse
import json
from pathlib import Path


def validate(inventory, registry):
    errors = []
    warnings = []
    acts = registry.get("acts", [])
    records = inventory.get("registry_acts", [])
    if inventory.get("candidate_registry_count") != len(acts):
        errors.append("registry count mismatch")
    expected = {x["id"]: x for x in acts}
    actual = {x["id"]: x for x in records}
    if len(actual) != len(records) or set(expected) != set(actual):
        errors.append("registry IDs missing, duplicated or unexpected")
    for act_id, row in actual.items():
        if act_id not in expected:
            continue
        original = expected[act_id]
        for key in ("title", "number", "signed_on"):
            if row.get(key) != original.get(key):
                errors.append(f"{act_id}: {key} differs from registry")
        if row.get("full_text_imported") or row.get("edition_verified"):
            errors.append(f"{act_id}: unverified inventory claims acceptance")
    seen = set()
    for evidence in inventory.get("uploaded_evidence", []):
        key = (evidence.get("act"), evidence.get("file"))
        if key in seen:
            errors.append(f"duplicate inventory evidence: {key}")
        seen.add(key)
        if evidence.get("production_approved") is not False:
            errors.append(f"unverified evidence approved: {key}")
        if evidence.get("availability") not in ("available_library", "audit_record_only"):
            errors.append(f"unknown availability: {key}")
    for pair in inventory.get("duplicate_candidates", []):
        if len(pair) != 2:
            errors.append("malformed duplicate candidate pair")
        elif not all(any(e["file"] == name for e in inventory["uploaded_evidence"]) for name in pair):
            errors.append(f"duplicate candidate missing evidence: {pair}")
        else:
            warnings.append(f"SHA-256 comparison required before deduplication: {pair}")
    idx = inventory.get("historical_index", {})
    if idx.get("production_approved") or idx.get("edition_verified"):
        errors.append("historical index incorrectly accepted")
    return errors, warnings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", default="fixtures/uploaded_legal_inventory_2026-11-15.json")
    parser.add_argument("--registry", default="fixtures/official_source_candidates.json")
    args = parser.parse_args()
    inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    errors, warnings = validate(inventory, registry)
    print(json.dumps({"valid": not errors, "registry_acts": len(registry["acts"]),
                      "evidence_records": len(inventory["uploaded_evidence"]),
                      "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
