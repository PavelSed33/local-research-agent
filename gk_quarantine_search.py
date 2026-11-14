"""Read-only search in an unverified Civil Code article index."""
import json
from pathlib import Path
import re

class QuarantinedCorpusError(ValueError):
    pass

def search(directory, query, limit=10):
    if not isinstance(query, str) or not query.strip() or len(query)>500:
        raise ValueError("Invalid query")
    if type(limit) is not int or not 1<=limit<=50:
        raise ValueError("Invalid limit")
    base=Path(directory)
    manifest=json.loads((base/"manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("status")!="quarantine_unverified" or
        manifest.get("legal_review_approved") is not False or
        manifest.get("source_claim_verified") is not False):
        raise QuarantinedCorpusError("Unverified quarantined index required")
    match=re.fullmatch(r"(?:статья|ст\.?|article)?\s*(\d+(?:\.\d+)?)",query.strip().casefold())
    results=[]
    with (base/"articles.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            record=json.loads(line)
            if not all(isinstance(record.get(k),str) for k in ("id","part","article","heading","text")):
                raise QuarantinedCorpusError("Invalid article")
            title=record["heading"].casefold()
            body=record["text"].casefold()
            needle=query.strip().casefold()
            if match:
                if record["article"]!=match.group(1): continue
                score=100
            elif needle in title: score=10
            elif needle in body: score=1
            else: continue
            results.append((score,record))
    results.sort(key=lambda item:(-item[0],item[1]["part"],item[1]["article"]))
    return {"status":"quarantine_unverified","verified":False,"warning":manifest.get("warning","Unverified legal text"),"total_matches":len(results),"results":[{"id":r["id"],"part":r["part"],"article":r["article"],"heading":r["heading"],"text":r["text"][:1500],"verified":False} for _,r in results[:limit]]}
