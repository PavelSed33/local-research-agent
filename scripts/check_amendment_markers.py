"""Check article-level amendment markers in a *quarantined* legal corpus.

A match is evidence of an expected phrase, not confirmation of the current
edition, official provenance, validity period, or legal correctness.
"""
import argparse
import json
from pathlib import Path


def check_markers(corpus_root, code, article, must_contain, must_not_contain=()):
    if code not in {"gk", "uk"}:
        raise ValueError("Unsupported code for this verification checkpoint")
    base = Path(corpus_root) / code
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "quarantine_unverified":
        raise ValueError("Only unverified quarantine copies can be inspected")
    matches = []
    with (base / "articles.jsonl").open(encoding="utf-8", newline="\n") as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("article") == article:
                matches.append(record)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one article {article}; found {len(matches)}")
    body = matches[0]["text"]
    result = {
        "code": code, "article": article,
        "status": "local_phrase_check_only", "edition_verified": False,
        "source_claim_verified": False,
        "required_phrase_hits": {phrase: phrase in body for phrase in must_contain},
        "forbidden_phrase_absent": {phrase: phrase not in body for phrase in must_not_contain},
    }
    result["checks_passed"] = (all(result["required_phrase_hits"].values())
                               and all(result["forbidden_phrase_absent"].values()))
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("corpus_root")
    p.add_argument("--code", choices=("gk", "uk"), required=True)
    p.add_argument("--article", required=True)
    p.add_argument("--contains", action="append", required=True)
    p.add_argument("--absent", action="append", default=[])
    args = p.parse_args()
    print(json.dumps(check_markers(args.corpus_root, args.code, args.article,
                                   args.contains, args.absent), ensure_ascii=False, indent=2))
