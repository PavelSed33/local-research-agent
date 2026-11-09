#!/usr/bin/env python3
"""Download explicitly identified official publications into a quarantined archive.

Only publication.pravo.gov.ru over HTTPS. No third-party code execution.
No implicit publication-to-current-edition promotion.
"""
import argparse
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE="https://publication.pravo.gov.ru"
MAX_BYTES=12_000_000
EO=re.compile(r"^[0-9]{8,24}$")
def fetch(path, eo):
    url=BASE+path+"?"+urllib.parse.urlencode({"eoNumber":eo})
    req=urllib.request.Request(url,headers={"User-Agent":"LocalResearchAgent/1.0 (official-law-archive)"})
    with urllib.request.urlopen(req,timeout=15) as response:
        if urllib.parse.urlsplit(response.geturl()).hostname!="publication.pravo.gov.ru":
            raise ValueError("Cross-host redirect rejected")
        data=response.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES: raise ValueError("Response exceeds size limit")
        return data,url,response.headers.get("Content-Type","")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--eo-number",action="append",required=True,help="Official publication number; repeat for each document")
    p.add_argument("--output",type=Path,default=Path("data/official_publications_quarantine"))
    p.add_argument("--execute",action="store_true",help="Opt-in network download")
    a=p.parse_args()
    if len(a.eo_number)>20: p.error("Max 20 documents per invocation")
    for eo in a.eo_number:
        if not EO.fullmatch(eo): p.error("Invalid eo-number: "+eo)
    if not a.execute:
        print(json.dumps({"dry_run":True,"count":len(a.eo_number),"numbers":a.eo_number},ensure_ascii=False));return
    a.output.mkdir(parents=True,exist_ok=True)
    report=[]
    for eo in a.eo_number:
        item={"eo_number":eo,"source":"publication.pravo.gov.ru","verified_current_edition":False}
        try:
            for kind,path in (("metadata","/api/Document"),("text","/api/DocumentText")):
                raw,url,content_type=fetch(path,eo)
                if kind=="metadata":
                    parsed=json.loads(raw.decode("utf-8-sig"))
                    if not isinstance(parsed,dict): raise ValueError("Metadata is not an object")
                    item["metadata"]=parsed
                else:
                    if "html" in content_type.lower(): raise ValueError("Unexpected HTML response")
                suffix="json" if kind=="metadata" else "txt"
                dest=a.output/(eo+"."+kind+"."+suffix)
                dest.write_bytes(raw)
                item[kind+"_sha256"]=hashlib.sha256(raw).hexdigest()
                item[kind+"_url"]=url
            item["status"]="downloaded_unverified"
        except Exception as e:
            item["status"]="error"
            item["error"]=str(e)
        item["retrieved_at"]=datetime.now(timezone.utc).isoformat()
        report.append(item)
        time.sleep(1)
    (a.output/"manifest.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"downloaded":sum(x["status"]=="downloaded_unverified" for x in report),"errors":sum(x["status"]=="error" for x in report),"manifest":str(a.output/"manifest.json")},ensure_ascii=False))
if __name__=="__main__":main()
