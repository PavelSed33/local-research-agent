"""Fail-closed admission of a user-supplied official PDF to the legal search corpus.

Run: python scripts/verify_constitution_import.py PDF OCR_TXT
Does not publish or index a document without independent review.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "fixtures/official_uploads/constitution_2022_intake.json"
QA = ROOT / "fixtures/official_uploads/constitution_2022_qa_report.json"

def verify(pdf_path, text_path):
    intake = json.loads(INTAKE.read_text(encoding="utf-8"))
    qa = json.loads(QA.read_text(encoding="utf-8"))
    pdf = Path(pdf_path).read_bytes()
    text = Path(text_path).read_bytes()
    actual_pdf = hashlib.sha256(pdf).hexdigest()
    actual_text = hashlib.sha256(text).hexdigest()
    checks = {
        "pdf_magic": pdf.startswith(b"%PDF-"),
        "pdf_sha256": actual_pdf == intake["sha256"] == qa["pdf_sha256"],
        "pdf_size": len(pdf) == intake["size_bytes"],
        "ocr_sha256": actual_text == qa["ocr_sha256"],
        "ocr_nonempty": bool(text.strip()),
        "independent_edition_review": bool(intake.get("edition_verified")) and bool(qa["checks"].get("edition_current_verified")),
        "human_ocr_review": bool(intake.get("ocr_text_verified")) and bool(qa["checks"].get("human_ocr_review")),
        "rights_review": bool(intake.get("legal_reviewed")) and bool(intake.get("commercial_use_allowed")),
        "official_original_verified": bool(qa["checks"].get("official_pdf_byte_match")),
    }
    return {"document_id": intake["source_document_id"], "checks": checks,
            "approved_for_legal_search": all(checks.values()),
            "pdf_sha256": actual_pdf, "ocr_sha256": actual_text}

def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/verify_constitution_import.py PDF OCR_TXT", file=sys.stderr)
        return 2
    try:
        result = verify(sys.argv[1], sys.argv[2])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"approved_for_legal_search": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["approved_for_legal_search"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
