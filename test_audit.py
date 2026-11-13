import csv
import tempfile
import unittest
from pathlib import Path

from audit import build_audit, build_row, write_csv, write_report

DOC = "Договор аренды с условиями оплаты, сроками и ответственностью сторон."


def make_folder(files: dict[str, str]) -> Path:
    folder = Path(tempfile.mkdtemp())
    for name, content in files.items():
        (folder / name).write_text(content, encoding="utf-8")
    return folder


class BuildAuditTests(unittest.TestCase):
    def test_all_supported_files_processed(self):
        folder = make_folder({"a.txt": DOC, "b.txt": DOC, "c.md": "пропустят"})
        rows, skipped = build_audit(folder, demo=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(skipped, [])
        self.assertEqual([row["файл"] for row in rows], ["a.txt", "b.txt"])
        self.assertEqual(rows[0]["оценка"], 65)  # demo: high-risk (-20) and missing clause (-15)

    def test_pdf_is_skipped_with_warning(self):
        folder = make_folder({"a.txt": DOC, "b.pdf": "%PDF мусор"})
        rows, skipped = build_audit(folder, demo=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(skipped), 1)
        self.assertIn("PDF", skipped[0])

    def test_error_in_file_does_not_stop_run(self):
        folder = make_folder({"a.txt": DOC, "битый.txt": "коротко"})
        rows, _ = build_audit(folder, demo=True)
        self.assertEqual(len(rows), 2)
        broken = next(row for row in rows if row["файл"] == "битый.txt")
        self.assertTrue(broken["ошибка"])
        self.assertEqual(broken["оценка"], "")


class RowTests(unittest.TestCase):
    def test_risk_counts_by_severity(self):
        scoring = {
            "score": 60,
            "verdict_color": "yellow",
            "risks": [
                {"severity": "high", "category": "a"},
                {"severity": "high", "category": "b"},
                {"severity": "low", "category": "c"},
            ],
            "missing_clauses": ["x"],
            "risk_deduction": 45,
            "missing_deduction": 15,
        }
        row = build_row(Path("договор.txt"), scoring)
        self.assertEqual(row["рисков_high"], 2)
        self.assertEqual(row["рисков_medium"], 0)
        self.assertEqual(row["рисков_low"], 1)
        self.assertEqual(row["нет_условий"], 1)
        self.assertEqual(row["вердикт"], "требует доработки")


class OutputTests(unittest.TestCase):
    def test_csv_and_report(self):
        folder = make_folder({"b.txt": DOC, "a.txt": DOC})
        rows, _ = build_audit(folder, demo=True)
        out = Path(tempfile.mkdtemp())
        write_csv(rows, out / "аудит.csv")
        write_report(rows, out / "аудит.md", "2026-11-06")

        with (out / "аудит.csv").open(encoding="utf-8-sig") as handle:
            parsed = list(csv.DictReader(handle, delimiter=";"))
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["оценка"], "65")
        self.assertEqual(parsed[0]["вердикт"], "требует доработки")

        report = (out / "аудит.md").read_text(encoding="utf-8")
        self.assertIn("# Аудит договорной базы", report)
        self.assertIn("Файлов проверено: 2", report)
        self.assertIn("Средняя оценка: 65/100", report)
        self.assertIn("## Самый проблемный договор", report)

    def test_report_lists_errors(self):
        rows = [build_row(Path("битый.txt"), {}, error="слишком короткий текст")]
        out = Path(tempfile.mkdtemp())
        write_report(rows, out / "аудит.md", "2026-11-06")
        report = (out / "аудит.md").read_text(encoding="utf-8")
        self.assertIn("## Ошибки обработки", report)
        self.assertIn("битый.txt", report)


if __name__ == "__main__":
    unittest.main()
