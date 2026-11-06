import base64
import io
import unittest
import zipfile

from doctext import decode_document_base64, extract_docx_text, read_document_file, resolve_document
from extract import handle_extract
from review import handle_review

DOCX_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
<w:p><w:r><w:t>ДОГОВОР АРЕНДЫ № 12 от 01.02.2026.</w:t></w:r></w:p>
<w:p><w:r><w:t>ООО «Арендодатель», ИНН 7707083893, </w:t></w:r><w:r><w:t>заключило договор с ООО «Арендатор».</w:t></w:r></w:p>
<w:p><w:r><w:t>Арендная плата составляет 100 000 руб. в месяц.</w:t></w:r></w:p>
</w:body>
</w:document>
"""

EXPECTED_TEXT = (
    "ДОГОВОР АРЕНДЫ № 12 от 01.02.2026.\n"
    "ООО «Арендодатель», ИНН 7707083893, заключило договор с ООО «Арендатор».\n"
    "Арендная плата составляет 100 000 руб. в месяц."
)


def make_docx(xml: str = DOCX_XML) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


class DocxTextTests(unittest.TestCase):
    def test_extracts_paragraphs_and_joins_runs(self):
        self.assertEqual(extract_docx_text(make_docx()), EXPECTED_TEXT)

    def test_broken_zip_is_rejected(self):
        with self.assertRaises(ValueError):
            extract_docx_text(b"PK\x03\x04" + "мусор".encode("utf-8") * 100)

    def test_zip_without_document_xml_is_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "не docx")
        with self.assertRaises(ValueError):
            extract_docx_text(buffer.getvalue())

    def test_pdf_is_rejected_with_clear_error(self):
        with self.assertRaises(ValueError) as ctx:
            read_document_file(b"%PDF-1.4 " + "мусор".encode("utf-8"), "договор.pdf")
        self.assertIn("PDF не поддерживается", str(ctx.exception))

    def test_plain_text_file_passes_through(self):
        self.assertEqual(read_document_file("текст договора".encode("utf-8"), "договор.txt"), "текст договора")

    def test_base64_docx_is_decoded(self):
        encoded = base64.b64encode(make_docx()).decode("ascii")
        self.assertEqual(decode_document_base64(encoded), EXPECTED_TEXT)

    def test_invalid_base64_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_document_base64("это не base64!!!")

    def test_resolve_document_prefers_text(self):
        self.assertEqual(resolve_document({"document": "  текст  "}), "текст")

    def test_resolve_document_reads_base64(self):
        encoded = base64.b64encode(make_docx()).decode("ascii")
        self.assertEqual(resolve_document({"document_base64": encoded}), EXPECTED_TEXT)

    def test_resolve_document_requires_input(self):
        with self.assertRaises(ValueError):
            resolve_document({})


class DocxPipelineTests(unittest.TestCase):
    def test_review_accepts_docx_base64(self):
        encoded = base64.b64encode(make_docx()).decode("ascii")
        result = handle_review({"document_base64": encoded, "demo": True})
        self.assertEqual(result["status"], "draft")
        self.assertEqual(len(result["risks"]), 1)

    def test_extract_accepts_docx_base64(self):
        encoded = base64.b64encode(make_docx()).decode("ascii")
        result = handle_extract({"document_base64": encoded, "demo": True})
        self.assertEqual(result["parties"][0]["inn"], "7707083893")


if __name__ == "__main__":
    unittest.main()
