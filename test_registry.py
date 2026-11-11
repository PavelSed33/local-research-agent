import csv
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from registry import build_row, find_expiring, parse_date, write_csv, write_expiring

CONTRACT = (
    "ДОГОВОР АРЕНДЫ № 12 от 01.02.2026. ООО «Арендодатель», ИНН 7707083893, "
    "ОГРН 1027700132195, и ООО «Арендатор» заключили договор. "
    "Арендная плата составляет 100 000 руб. в месяц. Договор действует до 31.12.2026."
)

EXTRACTION = {
    "parties": [{"role": "арендодатель", "name": "ООО «Арендодатель»", "inn": "7707083893", "ogrn": "1027700132195"}],
    "amounts": [{"label": "арендная плата", "value": "100 000 руб.", "currency": "RUB"}],
    "dates": [{"label": "окончание действия", "date": "31.12.2026"}],
    "penalties": [],
    "term": {"duration": "до 31.12.2026"},
    "jurisdiction": {"court": "Арбитражный суд города Москвы"},
    "warnings": [],
}


class ParseDateTests(unittest.TestCase):
    def test_valid_date(self):
        self.assertEqual(parse_date("31.12.2026"), date(2026, 12, 31))

    def test_date_inside_text(self):
        self.assertEqual(parse_date("до 05.06.2027 включительно"), date(2027, 6, 5))

    def test_invalid_date_returns_none(self):
        self.assertIsNone(parse_date("32.13.2026"))
        self.assertIsNone(parse_date("нет даты"))


class BuildRowTests(unittest.TestCase):
    def test_row_contains_key_fields(self):
        row = build_row(Path("договор.txt"), EXTRACTION)
        self.assertEqual(row["файл"], "договор.txt")
        self.assertIn("ООО «Арендодатель»", row["стороны"])
        self.assertEqual(row["инн"], "7707083893")
        self.assertIn("100 000 руб.", row["суммы"])
        self.assertEqual(row["срок_действия"], "до 31.12.2026")
        self.assertEqual(row["подсудность"], "Арбитражный суд города Москвы")
        self.assertEqual(row["ошибка"], "")

    def test_error_row_keeps_filename(self):
        row = build_row(Path("битый.txt"), {}, error="не удалось обработать")
        self.assertEqual(row["ошибка"], "не удалось обработать")
        self.assertEqual(row["файл"], "битый.txt")


class FindExpiringTests(unittest.TestCase):
    def test_date_in_window_is_found(self):
        rows = [build_row(Path("a.txt"), EXTRACTION)]
        result = find_expiring(rows, [EXTRACTION], date(2026, 12, 10), 30)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["дата"], "31.12.2026")
        self.assertEqual(result[0]["осталось_дней"], "21")

    def test_far_date_is_ignored(self):
        rows = [build_row(Path("a.txt"), EXTRACTION)]
        self.assertEqual(find_expiring(rows, [EXTRACTION], date(2026, 1, 1), 30), [])

    def test_results_sorted_by_date(self):
        extraction_a = {"dates": [{"label": "x", "date": "20.01.2027"}]}
        extraction_b = {"dates": [{"label": "y", "date": "05.01.2027"}]}
        rows = [build_row(Path("a.txt"), extraction_a), build_row(Path("b.txt"), extraction_b)]
        result = find_expiring(rows, [extraction_a, extraction_b], date(2027, 1, 1), 30)
        self.assertEqual([item["файл"] for item in result], ["b.txt", "a.txt"])


class WriteTests(unittest.TestCase):
    def test_csv_and_expiring_files(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [build_row(Path("договор.txt"), EXTRACTION)]
            csv_path = Path(directory) / "реестр.csv"
            write_csv(rows, csv_path)
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                parsed = list(csv.DictReader(handle, delimiter=";"))
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0]["инн"], "7707083893")

            expiring_path = Path(directory) / "истекающие.md"
            write_expiring([], expiring_path, 30, "2026-11-06")
            self.assertIn("Истекающих дат не найдено", expiring_path.read_text(encoding="utf-8"))


class RegistryCliTests(unittest.TestCase):
    def test_demo_run_over_folder(self):
        with tempfile.TemporaryDirectory() as contracts, tempfile.TemporaryDirectory() as output:
            Path(contracts, "договор.txt").write_text(CONTRACT, encoding="utf-8")
            Path(contracts, "скан.pdf").write_bytes(b"%PDF-1.4 fake")
            result = subprocess.run(
                [sys.executable, "registry.py", contracts, "--demo", "--output-dir", output, "--days", "90"],
                capture_output=True, text=True, cwd=Path(__file__).parent,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PDF не поддерживается", result.stderr)
            csv_text = Path(output, "реестр.csv").read_text(encoding="utf-8-sig")
            self.assertIn("7707083893", csv_text)
            self.assertIn("100 000 руб.", csv_text)
            expiring_text = Path(output, "истекающие-договоры.md").read_text(encoding="utf-8")
            self.assertIn("31.12.2026", expiring_text)


if __name__ == "__main__":
    unittest.main()
