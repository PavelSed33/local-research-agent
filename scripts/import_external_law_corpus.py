#!/usr/bin/env python3
"""Import locally obtained Russian-law XML/JSON/JSONL/TXT files into a quarantined SQLite FTS index.

No network calls. Source texts are never represented as verified current editions.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
import xml.etree.ElementTree as ET

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
 id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, source_path TEXT NOT NULL,
 source_url TEXT, title TEXT, document_number TEXT, content_sha256 TEXT NOT NULL,
 retrieved_at TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
 effective_from TEXT, effective_to TEXT, body TEXT NOT NULL,
 UNIQUE(source_id, content_sha256)
);
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
 title, body, content='documents', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
 INSERT INTO documents_fts(rowid,title,body) VALUES(new.id,new.title,new.body);
END;
"""
def xml_text(path):
    # No entity expansion or external network access; reject DTD explicitly.
    data = path.read_bytes()
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('DTD/entities prohibited')
    root = ET.fromstring(data)
    return ' '.join(s.strip() for s in root.itertext() if s and s.strip())

def load(path):
    suffix = path.suffix.lower()
    if suffix == '.xml':
        return xml_text(path), path.stem, None
    if suffix in ('.json', '.jsonl'):
        data = json.loads(path.read_text(encoding='utf-8-sig')) if suffix == '.json' else [
            json.loads(x) for x in path.read_text(encoding='utf-8-sig').splitlines() if x.strip()]
        if isinstance(data, list):
            data = {'text': '\n'.join(str(x.get('text', x)) if isinstance(x, dict) else str(x) for x in data)}
        if not isinstance(data, dict):
            raise ValueError('Expected JSON object/list')
        body = data.get('text') or data.get('body') or data.get('content')
        if not isinstance(body, str):
            raise ValueError('Missing text/body/content string')
        return body, str(data.get('title') or path.stem), data.get('source_url')
    return path.read_text(encoding='utf-8-sig'), path.stem, None

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', required=True, type=pathlib.Path)
    p.add_argument('--db', required=True, type=pathlib.Path)
    p.add_argument('--source-id', required=True)
    p.add_argument('--source-url', default=None)
    p.add_argument('--max-files', type=int, default=1000)
    p.add_argument('--max-bytes', type=int, default=5_000_000)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    if a.max_files < 1 or a.max_bytes < 1:
        p.error('limits must be positive')
    root = a.input.resolve()
    if not root.is_dir():
        p.error('input must be a directory')
    files = sorted(f for f in root.rglob('*') if f.is_file() and f.suffix.lower() in ('.xml','.json','.jsonl','.txt'))
    if len(files) > a.max_files:
        p.error(f'{len(files)} files exceed --max-files={a.max_files}; run in smaller batches')
    con = None
    if not a.dry_run:
        a.db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(a.db)
        con.executescript(SCHEMA)
    report = {'scanned':len(files),'inserted':0,'duplicates':0,'rejected':[],'dry_run':a.dry_run}
    for f in files:
        try:
            if f.is_symlink() or f.stat().st_size > a.max_bytes:
                raise ValueError('symlink or size limit exceeded')
            body,title,url = load(f)
            if not body.strip():
                raise ValueError('empty document')
            sha = hashlib.sha256(body.encode('utf-8')).hexdigest()
            if con is not None:
                cursor = con.execute('INSERT OR IGNORE INTO documents(source_id,source_path,source_url,title,content_sha256,retrieved_at,body) VALUES(?,?,?,?,?,?,?)',
                    (a.source_id,str(f.relative_to(root)),url or a.source_url,title,sha,dt.datetime.now(dt.timezone.utc).isoformat(),body))
                report['inserted' if cursor.rowcount else 'duplicates'] += 1
            else:
                report['inserted'] += 1
        except (ValueError, UnicodeError, OSError, ET.ParseError, json.JSONDecodeError) as e:
            report['rejected'].append({'path':str(f.relative_to(root)),'reason':str(e)})
    if con is not None:
        con.commit()
        con.close()
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__ == '__main__':
    main()
