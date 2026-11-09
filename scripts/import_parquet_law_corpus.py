#!/usr/bin/env python3
"""Stream local Parquet shards into an isolated, unverified SQLite corpus.

Install optional dependency: pip install pyarrow
No network calls. All imported records remain verified=0.
"""
import argparse
from check_external_corpus_manifest import approved_files
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",required=True,type=Path,help="Local .parquet file or directory")
    p.add_argument("--manifest",required=True,type=Path)
    p.add_argument("--db",required=True,type=Path)
    p.add_argument("--source-id",required=True)
    p.add_argument("--text-column",required=True)
    p.add_argument("--title-column")
    p.add_argument("--url-column")
    p.add_argument("--batch-size",type=int,default=128)
    p.add_argument("--max-rows",type=int,default=0,help="0 means all")
    p.add_argument("--dry-run",action="store_true")
    a=p.parse_args()
    if a.batch_size<1 or a.max_rows<0: p.error("Invalid limits")
    try:
        import pyarrow.parquet as pq
    except ImportError:
        p.error("Install optional dependency: pip install pyarrow")
    paths=sorted(a.input.glob("*.parquet")) if a.input.is_dir() else [a.input]
    if not paths or any(not x.is_file() or x.suffix!=".parquet" for x in paths):
        p.error("No valid local Parquet files")
    approved_files(a.manifest,paths,a.source_id)
    db=None
    if not a.dry_run:
        a.db.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(a.db)
        db.executescript("""
        CREATE TABLE IF NOT EXISTS documents(
          id INTEGER PRIMARY KEY,source_id TEXT NOT NULL,source_path TEXT NOT NULL,
          source_url TEXT,title TEXT,document_number TEXT,content_sha256 TEXT NOT NULL,
          retrieved_at TEXT NOT NULL,verified INTEGER NOT NULL DEFAULT 0,
          effective_from TEXT,effective_to TEXT,body TEXT NOT NULL,
          UNIQUE(source_id,content_sha256));
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
          title,body,content='documents',content_rowid='id',tokenize='unicode61');
        CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
          INSERT INTO documents_fts(rowid,title,body) VALUES(new.id,new.title,new.body);
        END;
        """)
    stats={"files":len(paths),"read":0,"inserted":0,"duplicates":0,"rejected":0,"dry_run":a.dry_run}
    for path in paths:
        pf=pq.ParquetFile(path)
        columns=[a.text_column]+[x for x in (a.title_column,a.url_column) if x]
        if any(c not in pf.schema_arrow.names for c in columns):
            raise ValueError(f"{path}: missing columns; available: {pf.schema_arrow.names}")
        for batch in pf.iter_batches(batch_size=a.batch_size,columns=list(dict.fromkeys(columns))):
            for record in batch.to_pylist():
                if a.max_rows and stats["read"]>=a.max_rows: break
                stats["read"]+=1
                body=record.get(a.text_column)
                if not isinstance(body,str) or not body.strip():
                    stats["rejected"]+=1;continue
                sha=hashlib.sha256(body.encode("utf-8")).hexdigest()
                title=str(record.get(a.title_column) or path.stem) if a.title_column else path.stem
                url=str(record.get(a.url_column) or "") if a.url_column else None
                if db:
                    cur=db.execute("""INSERT OR IGNORE INTO documents
                      (source_id,source_path,source_url,title,content_sha256,retrieved_at,body)
                      VALUES(?,?,?,?,?,?,?)""",
                      (a.source_id,path.name,url,title,sha,dt.datetime.now(dt.timezone.utc).isoformat(),body))
                    stats["inserted" if cur.rowcount else "duplicates"]+=1
                else: stats["inserted"]+=1
            if db: db.commit()
            if a.max_rows and stats["read"]>=a.max_rows: break
        if a.max_rows and stats["read"]>=a.max_rows: break
    if db: db.close()
    print(json.dumps(stats,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
