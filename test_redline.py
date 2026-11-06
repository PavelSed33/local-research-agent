import unittest

from redline import diff_documents, handle_diff

OLD = (
    "ДОГОВОР АРЕНДЫ № 12 от 01.02.2026.\n"
    "Арендная плата составляет 100 000 руб. в месяц.\n"
    "Договор действует до 31.12.2026.\n"
    "Споры рассматриваются в Арбитражном суде города Москвы."
)
NEW = (
    "ДОГОВОР АРЕНДЫ № 12 от 01.02.2026.\n"
    "Арендная плата составляет 120 000 руб. в месяц.\n"
    "Договор действует до 31.12.2026.\n"
    "Споры рассматриваются в Арбитражном суде города Москвы.\n"
    "Договор автоматически продлевается на один год."
)


class DiffDocumentsTests(unittest.TestCase):
    def test_identical_documents_have_no_changes(self):
        result = diff_documents(OLD, OLD)
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["summary"]["unchanged"], 4)
        self.assertEqual(result["summary"]["added"], 0)

    def test_changed_and_added_lines(self):
        result = diff_documents(OLD, NEW)
        summary = result["summary"]
        self.assertEqual(summary["changed"], 1)
        self.assertEqual(summary["added"], 1)
        self.assertEqual(summary["removed"], 0)
        self.assertEqual(summary["unchanged"], 3)
        changed = [item for item in result["changes"] if item["type"] == "changed"]
        self.assertEqual(changed[0]["old"], "Арендная плата составляет 100 000 руб. в месяц.")
        self.assertEqual(changed[0]["new"], "Арендная плата составляет 120 000 руб. в месяц.")
        added = [item for item in result["changes"] if item["type"] == "added"]
        self.assertEqual(added[0]["text"], "Договор автоматически продлевается на один год.")

    def test_removed_lines(self):
        result = diff_documents(NEW, OLD)
        self.assertEqual(result["summary"]["removed"], 1)
        removed = [item for item in result["changes"] if item["type"] == "removed"]
        self.assertEqual(removed[0]["text"], "Договор автоматически продлевается на один год.")

    def test_unified_diff_present(self):
        result = diff_documents(OLD, NEW)
        self.assertIn("версия A", result["diff"])
        self.assertIn("-Арендная плата составляет 100 000 руб. в месяц.", result["diff"])
        self.assertIn("+Арендная плата составляет 120 000 руб. в месяц.", result["diff"])


class HandleDiffTests(unittest.TestCase):
    def test_handle_diff_returns_draft(self):
        result = handle_diff({"document_a": OLD, "document_b": NEW})
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["summary"]["changed"], 1)
        self.assertTrue(result["checked_on"])
        self.assertIn("не индивидуальная юридическая консультация", result["disclaimer"])

    def test_missing_side_is_rejected(self):
        with self.assertRaises(ValueError):
            handle_diff({"document_a": OLD})

    def test_short_document_is_rejected(self):
        with self.assertRaises(ValueError):
            handle_diff({"document_a": "коротко", "document_b": NEW})


if __name__ == "__main__":
    unittest.main()
