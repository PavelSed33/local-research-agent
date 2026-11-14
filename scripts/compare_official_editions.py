"""Offline, fail-closed comparison of local statutes with supplied official snapshots.

Never downloads a page, attests the authenticity of supplied material, or
promotes a quarantined index to verified legal status. Strict equality includes
headers and annotations: use the same extraction convention on both sides.
"""
from __future__ import annotations

import argparse
from datetime import date
import difflib
import hashlib
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlsplit


ALLOWED_HOSTS = frozenset({"pravo.gov.ru", "www.pravo.gov.ru", "publication.pravo.gov.ru"})
CODES = frozenset({"gk", "uk"})


def _sha(text: bytes) -> str:
    return hashlib.sha256(text).hexdigest()


def _normalize(text: str) -> str:
    # Only normalize line-ending encoding. No whitespace, punctuation or
    # wording changes are allowed to silently disappear.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _official_url(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        u = urlsplit(value)
        return (u.scheme == "https" and (u.hostname or "").lower() in ALLOWED_HOSTS
                and u.username is None and u.password is None and u.port in (None, 443)
                and not u.fragment and not u.path.startswith("//"))
    except ValueError:
        return False


def _safe_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("Snapshot relative path is required")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Snapshot must have a safe relative path")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved.is_symlink() or not resolved.is_file():
        raise ValueError("Snapshot outside allowed directory or missing")
    # Also disallow a symlink at any path component.
    current = root
    for segment in path.parts:
        current = current / segment
        if current.is_symlink():
            raise ValueError("Snapshot symlinks are not allowed")
    return resolved


def compare_batch(corpus_root: str | Path, snapshot_root: str | Path, specification: dict) -> dict:
    """Compare specified articles with offline files of *claimed* official provenance."""
    if not isinstance(specification, dict) or not isinstance(specification.get("checks"), list):
        raise ValueError("Expected checks list")
    checks = specification["checks"]
    if not checks or len(checks) > 100:
        raise ValueError("Batch must contain 1-100 checks")
    corpus = Path(corpus_root)
    snapshots = Path(snapshot_root)
    seen = set()
    indexes = {}
    output = []
    for item in checks:
        if not isinstance(item, dict):
            raise ValueError("Check must be an object")
        code, article = item.get("code"), item.get("article")
        if code not in CODES or not isinstance(article, str) or not article:
            raise ValueError("Unknown code or article")
        if (code, article) in seen:
            raise ValueError("Duplicate article in batch")
        seen.add((code, article))
        if not _official_url(item.get("official_url")):
            raise ValueError("Official URL must use approved government HTTPS host")
        issued = item.get("edition_date")
        try:
            if not isinstance(issued, str) or date.fromisoformat(issued).isoformat() != issued:
                raise ValueError()
        except ValueError as exc:
            raise ValueError("edition_date must be a real ISO date") from exc
        expected = item.get("snapshot_sha256")
        if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise ValueError("Snapshot SHA-256 must be supplied")
        if code not in indexes:
            folder = corpus / code
            meta = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            if meta.get("status") != "quarantine_unverified":
                raise ValueError("Comparison requires quarantined local index")
            rows = {}
            with (folder / "articles.jsonl").open(encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        record = json.loads(line)
                        number = record.get("article")
                        if number in rows:
                            raise ValueError("Duplicate article identifier in local index")
                        rows[number] = record
            indexes[code] = rows
        local = indexes[code].get(article)
        if not local or not isinstance(local.get("text"), str):
            raise ValueError("Missing local article: " + code + " " + article)
        path = _safe_file(snapshots, item.get("snapshot_file"))
        raw = path.read_bytes()
        if _sha(raw) != expected:
            raise ValueError("Official snapshot file SHA-256 mismatch")
        official_text = raw.decode("utf-8")
        old = _normalize(local["text"])
        new = _normalize(official_text)
        exact = old == new
        delta = list(difflib.unified_diff(
            old.splitlines(), new.splitlines(), fromfile="local", tofile="claimed_official",
            lineterm="", n=2))
        output.append({
            "code": code, "article": article,
            "official_url_claimed": item["official_url"], "edition_date_claimed": issued,
            "snapshot_sha256": expected, "local_text_sha256": _sha(local["text"].encode("utf-8")),
            "exact_text_match": exact, "diff_preview": "\n".join(delta[:120]),
            "diff_truncated": len(delta) > 120,
            "official_provenance_authenticated": False,
            "edition_verified": False,
            "review_required": True,
        })
    return {
        "status": "comparison_only_unverified",
        "compared": len(output), "exact_text_matches": sum(x["exact_text_match"] for x in output),
        "official_provenance_authenticated": False, "edition_verified": False,
        "warning": "Source URL is a claim only; snapshot origin and edition dates need independent confirmation.",
        "results": output,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("corpus_root")
    p.add_argument("snapshot_root")
    p.add_argument("specification_json")
    p.add_argument("--output", required=True)
    args = p.parse_args()
    spec = json.loads(Path(args.specification_json).read_text(encoding="utf-8"))
    result = compare_batch(args.corpus_root, args.snapshot_root, spec)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Compared {result['compared']} articles; exact text matches: {result['exact_text_matches']}; edition verified: false")


if __name__ == "__main__":
    main()
