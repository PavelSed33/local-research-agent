#!/usr/bin/env python3
"""Register locally acquired official legal texts with provenance and hash; fail closed.

This script does not download texts or assert that a consolidated edition is current.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

ALLOWED={"pravo.gov.ru","www.pravo.gov.ru","publication.pravo.gov.ru","pravo.minjust.ru"}
def register(file,source_url,title,law_number,edition_date,output):
    source=urlparse(source_url)
    if source.scheme!="https" or source.hostname not in ALLOWED or source.username or source.password:
        raise ValueError("official HTTPS source URL required")
    dt.date.fromisoformat(edition_date)
    file=Path(file)
    if not file.is_file() or file.is_symlink() or file.stat().st_size==0:
        raise ValueError("nonempty regular input file required")
    if file.suffix.lower() not in (".pdf",".txt",".xml",".json"):
        raise ValueError("unsupported input type")
    with file.open("rb") as stream:
        sha=hashlib.file_digest(stream,"sha256").hexdigest()
    record={"path":file.name,"source_id":"official_ru_pending_review",
            "source_url":source_url,"title":title,"law_number":law_number,
            "edition_date_declared":edition_date,"sha256":sha,
            "license":"rights-review-required","provenance":[source_url],
            "approved_for_import":False,"verification_status":"pending_manual_source_and_edition_review",
            "bytes":file.stat().st_size,"recorded_at":dt.datetime.now(dt.timezone.utc).isoformat()}
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps([record],ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return record
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--file",required=True)
    p.add_argument("--source-url",required=True)
    p.add_argument("--title",required=True)
    p.add_argument("--law-number",required=True)
    p.add_argument("--edition-date",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    result=register(a.file,a.source_url,a.title,a.law_number,a.edition_date,a.output)
    print(json.dumps({"sha256":result["sha256"],"status":result["verification_status"],
                      "approved_for_import":False},ensure_ascii=False))
if __name__=="__main__":main()
