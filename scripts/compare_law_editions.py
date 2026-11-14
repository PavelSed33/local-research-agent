"""Compare two locally extracted legal texts by article headings.

This is a candidate diff for review, not proof of a currently effective law.
Never auto-approve source material based on a text comparison.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

ARTICLE = re.compile(r"(?m)^\s*Статья\s+(\d+(?:\.\d+)?)\s*[.．]?\s*(.*)$")

def parse_articles(text):
    matches = list(ARTICLE.finditer(text))
    out = {}
    for i, match in enumerate(matches):
        key = match.group(1)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        normalized = " ".join(text[match.start():end].split())
        out.setdefault(key, []).append(hashlib.sha256(normalized.encode("utf-8")).hexdigest())
    return out

def compare(old_text, new_text):
    old, new = parse_articles(old_text), parse_articles(new_text)
    common = old.keys() & new.keys()
    added = sorted(new.keys() - old.keys(), key=article_sort)
    removed = sorted(old.keys() - new.keys(), key=article_sort)
    changed = sorted((k for k in common if old[k] != new[k]), key=article_sort)
    duplicates = sorted((k for k, v in new.items() if len(v) > 1), key=article_sort)
    return {"added": added, "removed": removed, "changed": changed,
            "duplicate_article_numbers_new": duplicates,
            "old_heading_count": sum(map(len, old.values())),
            "new_heading_count": sum(map(len, new.values())),
            "status": "requires_legal_review",
            "approved_for_legal_search": False}

def article_sort(value):
    return tuple(int(n) for n in value.split("."))

def main():
    p = argparse.ArgumentParser()
    p.add_argument("old_text")
    p.add_argument("new_text")
    p.add_argument("--output", required=True)
    args = p.parse_args()
    old = Path(args.old_text).read_text(encoding="utf-8")
    new = Path(args.new_text).read_text(encoding="utf-8")
    result = compare(old, new)
    result["old_source_sha256"] = hashlib.sha256(old.encode("utf-8")).hexdigest()
    result["new_source_sha256"] = hashlib.sha256(new.encode("utf-8")).hexdigest()
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: len(result[k]) for k in ("added", "removed", "changed", "duplicate_article_numbers_new")}))

if __name__ == "__main__":
    main()
