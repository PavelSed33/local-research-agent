"""Explicitly classify unavailable quarantined primary-source archives.

Never reconstruct missing originals from extracted text or relax hash validation.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "primary_sources"


def inspect(root=ROOT):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    missing = sorted({
        row[field] for row in manifest["sources"]
        for field in ("original_file", "text_file")
        if not (root / row[field]).is_file()
    })
    return {
        "status": "blocked_missing_originals" if missing else "files_present_unverified",
        "source_count": len(manifest["sources"]),
        "missing_file_count": len(missing),
        "missing_files": missing,
        "legal_acceptance": False,
        "recovery_policy": "restore only independently authorized original files with verified SHA-256",
    }


if __name__ == "__main__":
    result = inspect()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(2 if result["missing_file_count"] else 0)
