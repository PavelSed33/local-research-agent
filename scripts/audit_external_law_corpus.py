#!/usr/bin/env python3
"""Audit quarantined legislation: coverage by declared inventory and edition dates.

Does not claim official verification or derive effective dates automatically.
"""
import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path

def assess(db_path, inventory_path, on_date):
    inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
    if not isinstance(inventory, list):
        raise ValueError("Inventory must be a JSON array")
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""SELECT source_id,title,document_number,content_sha256,
                                     effective_from,effective_to,verified,source_url
                              FROM documents""").fetchall()
    results = []
    for item in inventory:
        if not isinstance(item, dict) or not item.get("title"):
            raise ValueError("Every inventory item needs a title")
        title = item["title"]
        matches = [r for r in rows if r["title"] == title]
        verified = [r for r in matches if r["verified"] == 1 and
                    r["effective_from"] and r["effective_from"] <= on_date and
                    (not r["effective_to"] or on_date < r["effective_to"])]
        status = ("verified_for_date" if len(verified) == 1 else
                  "ambiguous_verified_editions" if len(verified) > 1 else
                  "present_unverified" if matches else "missing")
        results.append({"title":title,"status":status,"records":len(matches),
                        "verified_candidates":len(verified),
                        "required_articles":item.get("required_articles",[]),
                        "article_coverage":"not_measured"})
    return {"as_of":on_date,"inventory_count":len(results),
            "counts":{k:sum(x["status"]==k for x in results) for k in
                      ("missing","present_unverified","verified_for_date","ambiguous_verified_editions")},
            "documents":results,
            "warning":"Title matching is exact; chapter/article completeness and official edition validation are not implemented."}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",required=True)
    p.add_argument("--inventory",required=True)
    p.add_argument("--date",default=dt.date.today().isoformat())
    p.add_argument("--output")
    args=p.parse_args()
    dt.date.fromisoformat(args.date)
    result=assess(args.db,args.inventory,args.date)
    data=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output: Path(args.output).write_text(data+"\n",encoding="utf-8")
    print(data)
if __name__=="__main__": main()
