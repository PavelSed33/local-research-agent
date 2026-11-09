#!/usr/bin/env python3
"""Fail-closed preflight for third-party legal corpus manifests. No downloads/execution."""
import argparse
import json
import hashlib
from pathlib import Path

BLOCKED = ("ruslawod", "irlspbru/ruslawod", "irlcode/ruslawod")
def check(item):
    if not isinstance(item, dict): return "record must be an object"
    required=("source_url","license","provenance","sha256","approved_for_import")
    if any(not item.get(k) for k in required): return "missing required provenance/license/hash/approval"
    provenance=item["provenance"]
    if not isinstance(provenance,list) or not provenance or not all(isinstance(x,str) for x in provenance):
        return "provenance must be a nonempty string list"
    text=" ".join([str(item.get("source_url","")),str(item.get("license",""))]+provenance).lower()
    if any(x in text for x in BLOCKED): return "blocked RusLawOD provenance"
    if item["approved_for_import"] is not True: return "explicit approval required"
    if not isinstance(item["sha256"],str) or len(item["sha256"])!=64 or any(c not in "0123456789abcdef" for c in item["sha256"].lower()):
        return "invalid SHA-256"
    if str(item["license"]).lower() in ("unknown","unverified","cc by-nc 4.0","cc-by-nc-4.0"):
        return "license not cleared"
    return None
def approved_files(manifest_path, paths, source_id):
    """Validate every input file against an explicit, per-file SHA-256 manifest."""
    entries=json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(entries,list):
        raise ValueError("manifest must be a list")
    by_path={}
    for item in entries:
        if not isinstance(item,dict) or not isinstance(item.get("path"),str):
            raise ValueError("each manifest entry needs path")
        key=item["path"]
        if key in by_path:
            raise ValueError("duplicate manifest path: "+key)
        by_path[key]=item
    allowed=[]
    for file in paths:
        file=Path(file)
        key=file.name
        if key not in by_path:
            raise ValueError("missing manifest entry: "+key)
        item=by_path[key]
        reason=check(item)
        if reason: raise ValueError(key+": "+reason)
        if item.get("source_id")!=source_id:
            raise ValueError(key+": source_id mismatch")
        digest=hashlib.file_digest(file.open("rb"),"sha256").hexdigest()
        if digest.lower()!=item["sha256"].lower():
            raise ValueError(key+": SHA-256 mismatch")
        allowed.append(item)
    if len(allowed)!=len(by_path):
        raise ValueError("manifest contains files not in import batch")
    return allowed

def main():
    p=argparse.ArgumentParser()
    p.add_argument("manifest",type=Path)
    args=p.parse_args()
    data=json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(data,list): p.error("manifest must be a JSON list")
    problems=[{"index":i,"reason":reason} for i,x in enumerate(data) if (reason:=check(x))]
    print(json.dumps({"records":len(data),"approved":len(data)-len(problems),"rejected":problems,
        "note":"Metadata gate only: does not verify source authenticity, rights, package safety or file hash against downloaded bytes."},ensure_ascii=False,indent=2))
    if problems or not data: raise SystemExit(1)
if __name__=="__main__": main()
