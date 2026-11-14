"""Compare a quarantined OCR draft with an independently obtained official text.

Usage: python scripts/compare_constitution_text.py OCR.txt OFFICIAL.txt
Outputs machine-readable differences. Never changes admission flags.
"""
import difflib
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

ARTICLE = re.compile(r"(?im)^\s*статья\s+(\d+(?:\.\d+)?)\s*$")

def normalize(value):
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\u00ad", "").replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", value).strip().lower()

def article_sections(value):
    matches = list(ARTICLE.finditer(value))
    result = {}
    for i, match in enumerate(matches):
        number = match.group(1)
        if number in result:
            raise ValueError("duplicate article heading: " + number)
        end = matches[i+1].start() if i+1 < len(matches) else len(value)
        result[number] = normalize(value[match.end():end])
    return result

def compare(ocr, official):
    left = article_sections(ocr)
    right = article_sections(official)
    common = sorted(set(left) & set(right), key=lambda x: [int(v) for v in x.split(".")])
    mismatched = [k for k in common if left[k] != right[k]]
    return {
        "ocr_article_count": len(left),
        "official_article_count": len(right),
        "missing_in_ocr": sorted(set(right)-set(left)),
        "extra_in_ocr": sorted(set(left)-set(right)),
        "mismatched_articles": mismatched,
        "sample_differences": {
            k: list(difflib.unified_diff(right[k].split(), left[k].split(), fromfile="official", tofile="ocr", lineterm=""))[:24]
            for k in mismatched[:5]
        },
        "exact_normalized_match": bool(right) and left == right,
        "approved_for_legal_search": False,
        "note": "Text comparison alone cannot establish current edition, licensing, or official PDF authenticity."
    }

def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/compare_constitution_text.py OCR.txt OFFICIAL.txt", file=sys.stderr)
        return 2
    try:
        ocr = Path(sys.argv[1]).read_text(encoding="utf-8")
        official = Path(sys.argv[2]).read_text(encoding="utf-8")
        report = compare(ocr, official)
        report["ocr_sha256"] = hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest()
        report["official_sha256"] = hashlib.sha256(Path(sys.argv[2]).read_bytes()).hexdigest()
    except (OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "approved_for_legal_search": False}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["exact_normalized_match"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
