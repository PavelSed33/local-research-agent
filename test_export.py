import base64
import unittest

from doctext import extract_docx_text
from export import ExportError, build_docx, handle_export, markdown_to_body_xml, safe_filename


SAMPLE = (
    "# ПРЕТЕНЗИЯ\n"
    "\n"
    "## Описание ситуации\n"
    "По договору № 12 от 01.02.2026 арендатор не оплатил аренду.\n"
    "\n"
    "## Расчёт задолженности\n"
    "| Месяц | Сумма |\n"
    "| Сентябрь | 100 000 руб. |\n"
    "| Октябрь | 100 000 руб. |\n"
    "\n"
    "## Требования\n"
    "- оплатить долг;\n"
    "- оплатить неустойку.\n"
)


class BuildDocxTests(unittest.TestCase):
    def test_roundtrip_through_doctext(self):
        text = extract_docx_text(build_docx(SAMPLE))
        self.assertIn("ПРЕТЕНЗИЯ", text)
        self.assertIn("Описание ситуации", text)
        self.assertIn("По договору № 12 от 01.02.2026 арендатор не оплатил аренду.", text)
        self.assertIn("| Месяц | Сумма |", text)
        self.assertIn("| Сентябрь | 100 000 руб. |", text)
        self.assertIn("• оплатить долг;", text)
        self.assertIn("• оплатить неустойку.", text)

    def test_special_characters_are_escaped(self):
        text = extract_docx_text(build_docx("Сумма > 100 & < 200 «кавычки»"))
        self.assertIn("Сумма > 100 & < 200 «кавычки»", text)

    def test_table_row_parsing(self):
        xml = markdown_to_body_xml("| a | b |\n| c | d |")
        self.assertIn("<w:tbl>", xml)
        self.assertIn("<w:t xml:space=\"preserve\">a</w:t>", xml)

    def test_adjacent_table_rows_form_one_table(self):
        xml = markdown_to_body_xml("| a |\n| b |")
        self.assertEqual(xml.count("<w:tbl>"), 1)

    def test_empty_document_is_rejected(self):
        with self.assertRaises(ExportError):
            build_docx("   \n  ")

    def test_oversize_document_is_rejected(self):
        with self.assertRaises(ExportError):
            build_docx("x" * 500_001)


class SafeFilenameTests(unittest.TestCase):
    def test_plain_title(self):
        self.assertEqual(safe_filename("Претензия по договору 12"), "Претензия по договору 12.docx")

    def test_dangerous_characters_removed(self):
        self.assertEqual(safe_filename("../../etc/passwd"), "etcpasswd.docx")

    def test_empty_title_falls_back(self):
        self.assertEqual(safe_filename("!!!"), "документ.docx")


class HandleExportTests(unittest.TestCase):
    def test_export_returns_base64_docx(self):
        result = handle_export({"title": "Претензия", "document": SAMPLE})
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["filename"], "Претензия.docx")
        data = base64.b64decode(result["docx_base64"])
        self.assertTrue(data.startswith(b"PK\x03\x04"))
        self.assertEqual(result["size_bytes"], len(data))
        text = extract_docx_text(data)
        self.assertIn("ПРЕТЕНЗИЯ", text)

    def test_missing_document_is_rejected(self):
        with self.assertRaises(ExportError):
            handle_export({"title": "Пусто"})

    def test_non_dict_payload_is_rejected(self):
        with self.assertRaises(ExportError):
            handle_export("не словарь")


if __name__ == "__main__":
    unittest.main()
