"""Read-only search over quarantined GK/NK/TK/KoAP/KAS/GRK article indexes (never legal verification)."""
import json
import re
from pathlib import Path

class CorpusError(ValueError):
    pass

def search(corpus_root, code, query, limit=10):
    if code not in ("gk", "nk", "tk", "koap", "kas", "grk"):
        raise CorpusError("Only gk/nk/tk/koap/kas/grk are supported")
    if not isinstance(query, str) or not query.strip() or len(query)>500:
        raise CorpusError("Query must be 1-500 characters")
    if type(limit) is not int or not 1<=limit<=50:
        raise CorpusError("Limit must be 1-50")
    root=Path(corpus_root).resolve()
    folder=root/code
    if folder.is_symlink() or not folder.is_dir():
        raise CorpusError("Corpus folder missing or linked")
    manifest_path=folder/"manifest.json"
    article_path=folder/"articles.jsonl"
    if manifest_path.is_symlink() or article_path.is_symlink():
        raise CorpusError("Symlinked corpus file refused")
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("status")!="quarantine_unverified" or
        manifest.get("legal_review_approved") is not False or
        manifest.get("source_claim_verified") is not False):
        raise CorpusError("Only quarantined unverified corpus is supported")
    needle=query.strip().casefold()
    article=re.fullmatch(r"(?:статья|ст\.?|article)?\s*(\d+(?:\.\d+)?)",needle)
    matches=[]
    with article_path.open(encoding="utf-8") as stream:
        for line in stream:
            r=json.loads(line)
            if not all(isinstance(r.get(k),str) for k in ("id","part","article","heading","text")):
                raise CorpusError("Malformed article record")
            if article:
                if r["article"]!=article.group(1): continue
                rank=100
            elif needle in r["heading"].casefold(): rank=10
            elif needle in r["text"].casefold(): rank=1
            else: continue
            matches.append((rank,r))
    matches.sort(key=lambda p:(-p[0],p[1]["part"],p[1]["article"]))
    return {"code":code,"status":"quarantine_unverified","verified":False,
        "warning":manifest.get("warning","Unverified legal edition"),
        "total_matches":len(matches),
        "results":[{"id":r["id"],"part":r["part"],"article":r["article"],
                    "heading":r["heading"],"excerpt":r["text"][:1500],
                    "verified":False} for _,r in matches[:limit]]}
