#!/usr/bin/env python3
"""Build a reviewable Civil Code TOC from a locally supplied licensed text.

No scraping, downloading, or claims of current legal validity.
"""
import argparse
import json
import re
from pathlib import Path

HEADER=re.compile(r"^\s*(?P<kind>Раздел|Подраздел|Глава|Статья)\s+(?P<number>[IVXLC]+|\d+(?:[.\-]\d+)?)\s*[.。:]?\s*(?P<title>.*)$",re.I)
REPEALED=re.compile(r"утратил[ао]?\s+силу|исключен[ао]?",re.I)
def parse(text):
    entries=[]
    for line_no,line in enumerate(text.splitlines(),1):
        match=HEADER.match(line)
        if match:
            item=match.groupdict()
            item.update(line=line_no,status="repealed_marker_in_heading" if REPEALED.search(line) else "status_not_verified")
            entries.append(item)
    return entries
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",type=Path,required=True,help="Local legally obtained UTF-8 text of one Civil Code part")
    p.add_argument("--part",type=int,choices=(1,2,3,4),required=True)
    p.add_argument("--source-url",required=True)
    p.add_argument("--edition-date",required=True,help="YYYY-MM-DD; declared by operator, not independently verified")
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    from datetime import date
    date.fromisoformat(a.edition_date)
    data=a.input.read_text(encoding="utf-8-sig")
    entries=parse(data)
    if not entries: p.error("no recognizable headings; check input encoding/layout")
    result={"part":a.part,"source_url":a.source_url,"edition_date_declared":a.edition_date,
            "verification_status":"unverified_extracted_headings","entries":entries,
            "warning":"Heading extraction only. Repealed norms can be omitted from source; amendments and effectiveness require separate authoritative validation."}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"part":a.part,"headings":len(entries),"articles":sum(x["kind"].lower()=="статья" for x in entries)},ensure_ascii=False))
if __name__=="__main__":main()
