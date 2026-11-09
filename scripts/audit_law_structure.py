#!/usr/bin/env python3
"""Conservative article/part/chapter coverage audit against a curated reference manifest.

This tool never infers that a legal text is current or legally verified.
"""
import argparse
import json
import re
import sqlite3
from pathlib import Path

ARTICLE=re.compile(r"(?im)^\\s*Статья\\s+(\\d+(?:\\.\\d+)?)\\s*(?:[.。:]|\\s|$)")
CHAPTER=re.compile(r"(?im)^\\s*Глава\\s+(\\d+(?:\\.\\d+)?)\\s*(?:[.。:]|\\s|$)")
PART=re.compile(r"(?im)^\\s*Часть\\s+((?:[IVXLC]+)|(?:\\d+)|(?:первая|вторая|третья|четвертая))\\s*(?:[.。:]|\\s|$)")
def extract(body):
    return {name:sorted(set(pattern.findall(body))) for name,pattern in
            (("articles",ARTICLE),("chapters",CHAPTER),("parts",PART))}
def audit(db,reference):
    items=json.loads(Path(reference).read_text(encoding="utf-8"))
    if not isinstance(items,list): raise ValueError("reference must be a list")
    results=[]
    with sqlite3.connect(db) as conn:
        conn.row_factory=sqlite3.Row
        for item in items:
            if not isinstance(item,dict) or not isinstance(item.get("title"),str):
                raise ValueError("each reference needs title")
            expected={k:set(map(str,item.get(k,[]))) for k in ("articles","chapters","parts")}
            if not any(expected.values()):
                results.append({"title":item["title"],"status":"no_reference","missing":{}});continue
            docs=conn.execute("SELECT body,verified FROM documents WHERE title=?",(item["title"],)).fetchall()
            if not docs:
                results.append({"title":item["title"],"status":"document_missing",
                                "missing":{k:sorted(v) for k,v in expected.items()}});continue
            # Do not join distinct editions: that can conceal gaps in individual versions.
            candidates=[]
            for doc in docs:
                found=extract(doc["body"])
                missing={k:sorted(expected[k]-set(found[k])) for k in expected}
                candidates.append({"missing":missing,"verified":bool(doc["verified"]),
                                   "complete":not any(missing.values())})
            complete=[c for c in candidates if c["complete"]]
            results.append({"title":item["title"],"status":"structurally_complete_unverified" if complete else "gaps_detected",
                            "candidate_editions":len(docs),"missing":min(candidates,key=lambda c:sum(map(len,c["missing"].values())))["missing"],
                            "warning":"Regex-based structural check; legal validity not established"})
    return {"results":results,"note":"Only checks enumerated reference identifiers. Missing numbers may be repealed or intentionally absent; no authoritative validation."}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",required=True)
    p.add_argument("--reference",required=True)
    p.add_argument("--output")
    args=p.parse_args()
    result=audit(args.db,args.reference)
    data=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output: Path(args.output).write_text(data+"\\n",encoding="utf-8")
    print(data)
if __name__=="__main__":main()
