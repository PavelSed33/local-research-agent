"""Create a local, quarantined article index from an uploaded Civil Code text file.

Input must be extracted locally from the user's PDF. Output is NOT admitted
to the authoritative legal corpus and must not be used for legal answers.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

ARTICLE = re.compile(r"(?m)^\s*Статья\s+(\d+(?:\.\d+)?)\s*[.．]\s*(.*)$")

def build_index(text):
    matches = list(ARTICLE.finditer(text))
    records = []
    for i, match in enumerate(matches):
        end = matches[i+1].start() if i+1 < len(matches) else len(text)
        records.append({
            "article": match.group(1),
            "heading": match.group(2).strip(),
            "start_offset": match.start(),
            "end_offset": end,
            "text_sha256": hashlib.sha256(text[match.start():end].encode("utf-8")).hexdigest(),
            "approved_for_legal_search": False,
        })
    return records

def main():
    p = argparse.ArgumentParser()
    p.add_argument("extracted_text")
    p.add_argument("output_json")
    args = p.parse_args()
    text = Path(args.extracted_text).read_text(encoding="utf-8")
    records = build_index(text)
    output = {"status": "quarantined", "approved_for_legal_search": False,
              "article_heading_count": len(records),
              "unique_article_numbers": len({r["article"] for r in records}),
              "source_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
              "articles": records}
    Path(args.output_json).write_text(json.dumps(output, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"article_heading_count": len(records),
                      "unique_article_numbers": output["unique_article_numbers"],
                      "approved_for_legal_search": False}))
if __name__ == "__main__":
    main()
