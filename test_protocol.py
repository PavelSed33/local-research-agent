import json
import unittest

from agent import AgentError
from protocol import (
    DemoProtocolModel,
    collect_disputed,
    handle_protocol,
    make_protocol,
    protocol_table,
    validate_protocol,
    ProtocolError,
)

DOC_A = (
    "ДОГОВОР АРЕНДЫ № 12\n"
    "1. Арендная плата составляет 100 000 руб. в месяц.\n"
    "2. Арендатор вправе расторгнуть договор в любой момент без уведомления.\n"
    "3. Споры разрешаются в Арбитражном суде города Москвы.\n"
)
DOC_B = (
    "ДОГОВОР АРЕНДЫ № 12\n"
    "1. Арендная плата составляет 120 000 руб. в месяц.\n"
    "3. Споры разрешаются в Арбитражном суде города Москвы.\n"
    "4. Арендодатель вправе в одностороннем порядке изменить плату.\n"
)


def valid_rows() -> dict:
    return {
        "rows": [
            {
                "point": "п. 1",
                "edition_a": "1. Арендная плата составляет 100 000 руб. в месяц.",
                "edition_b": "1. Арендная плата составляет 120 000 руб. в месяц.",
                "comment": "Согласовать индексацию не чаще раза в год.",
            },
            {
                "point": "п. 2",
                "edition_a": "2. Арендатор вправе расторгнуть договор в любой момент без уведомления.",
                "edition_b": "",
                "comment": "Настаиваем на сохранении: иначе кабальное условие.",
            },
        ]
    }


class FakeModel:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.responses.pop(0)


class CollectDisputedTests(unittest.TestCase):
    def test_changed_removed_added(self):
        changes = [
            {"type": "changed", "old": "было", "new": "стало"},
            {"type": "removed", "text": "удалено"},
            {"type": "added", "text": "добавлено"},
        ]
        disputed = collect_disputed(changes)
        self.assertEqual([item["kind"] for item in disputed], ["changed", "removed", "added"])
        self.assertEqual(disputed[1]["edition_b"], "")
        self.assertEqual(disputed[2]["edition_a"], "")


class ValidateTests(unittest.TestCase):
    def test_valid_rows_pass(self):
        self.assertEqual(validate_protocol(valid_rows(), DOC_A, DOC_B), [])

    def test_fabricated_edition_a_is_rejected(self):
        result = valid_rows()
        result["rows"][0]["edition_a"] = "выдуманный пункт, которого нет в договоре"
        self.assertTrue(any("редакция А" in issue for issue in validate_protocol(result, DOC_A, DOC_B)))

    def test_fabricated_edition_b_is_rejected(self):
        result = valid_rows()
        result["rows"][0]["edition_b"] = "другая выдуманная редакция пункта"
        self.assertTrue(any("редакция Б" in issue for issue in validate_protocol(result, DOC_A, DOC_B)))

    def test_empty_comment_is_rejected(self):
        result = valid_rows()
        result["rows"][0]["comment"] = ""
        self.assertTrue(any("комментарий" in issue for issue in validate_protocol(result, DOC_A, DOC_B)))

    def test_both_editions_empty_is_rejected(self):
        result = valid_rows()
        result["rows"][0]["edition_a"] = ""
        result["rows"][0]["edition_b"] = ""
        self.assertTrue(any("обе редакции" in issue for issue in validate_protocol(result, DOC_A, DOC_B)))

    def test_empty_rows_is_rejected(self):
        self.assertTrue(validate_protocol({"rows": []}, DOC_A, DOC_B))


class MakeProtocolTests(unittest.TestCase):
    def test_valid_first_answer_needs_no_repair(self):
        disputed = collect_disputed([
            {"type": "changed", "old": "1. Арендная плата составляет 100 000 руб. в месяц.", "new": "1. Арендная плата составляет 120 000 руб. в месяц."},
        ])
        model = FakeModel([json.dumps(valid_rows())])
        result, repaired = make_protocol(model, disputed, DOC_A, DOC_B)
        self.assertFalse(repaired)
        self.assertEqual(model.calls, 1)

    def test_invalid_answer_is_repaired_once(self):
        disputed = collect_disputed([
            {"type": "changed", "old": "1. Арендная плата составляет 100 000 руб. в месяц.", "new": "1. Арендная плата составляет 120 000 руб. в месяц."},
        ])
        broken = valid_rows()
        broken["rows"][0]["edition_a"] = "выдуманная редакция"
        model = FakeModel([json.dumps(broken), json.dumps(valid_rows())])
        result, repaired = make_protocol(model, disputed, DOC_A, DOC_B)
        self.assertTrue(repaired)
        self.assertEqual(model.calls, 2)

    def test_second_invalid_answer_raises(self):
        disputed = collect_disputed([
            {"type": "changed", "old": "1. Арендная плата составляет 100 000 руб. в месяц.", "new": "1. Арендная плата составляет 120 000 руб. в месяц."},
        ])
        broken = valid_rows()
        broken["rows"][0]["edition_b"] = "выдумка"
        model = FakeModel([json.dumps(broken), json.dumps(broken)])
        with self.assertRaises(AgentError):
            make_protocol(model, disputed, DOC_A, DOC_B)


class ProtocolTableTests(unittest.TestCase):
    def test_table_structure(self):
        table = protocol_table(valid_rows()["rows"], "Арендодатель", "Арендатор")
        self.assertIn("# ПРОТОКОЛ РАЗНОГЛАСИЙ", table)
        self.assertIn("| Пункт | Редакция «Арендодатель» | Редакция «Арендатор» | Предложение |", table)
        self.assertIn("100 000 руб.", table)
        self.assertIn("«Арендодатель»: [должность, ФИО]", table)

    def test_pipes_in_text_are_neutralized(self):
        rows = [{"point": "п. 1", "edition_a": "a | b", "edition_b": "", "comment": "комментарий"}]
        table = protocol_table(rows, "А", "Б")
        data_lines = [line for line in table.splitlines() if line.startswith("| п. 1")]
        self.assertEqual(len(data_lines), 1)
        self.assertNotIn("a | b", data_lines[0])


class HandleProtocolTests(unittest.TestCase):
    def test_demo_protocol(self):
        result = handle_protocol({"document_a": DOC_A, "document_b": DOC_B, "demo": True})
        self.assertEqual(result["status"], "draft")
        self.assertTrue(result["rows"])
        self.assertIn("ПРОТОКОЛ РАЗНОГЛАСИЙ", result["protocol_markdown"])
        self.assertEqual(result["summary"]["changed"], 1)
        self.assertEqual(result["summary"]["removed"], 1)
        self.assertEqual(result["summary"]["added"], 1)

    def test_identical_documents_are_rejected(self):
        with self.assertRaises(ProtocolError):
            handle_protocol({"document_a": DOC_A, "document_b": DOC_A, "demo": True})

    def test_party_names_are_used(self):
        result = handle_protocol({
            "document_a": DOC_A, "document_b": DOC_B,
            "party_a": "Арендодатель", "party_b": "Арендатор", "demo": True,
        })
        self.assertIn("Редакция «Арендодатель»", result["protocol_markdown"])

    def test_missing_document_is_rejected(self):
        with self.assertRaises(ValueError):
            handle_protocol({"document_a": DOC_A, "demo": True})

    def test_demo_rows_pass_validation(self):
        # Демо-модель должна сама проходить код-валидацию без исправления.
        result = handle_protocol({"document_a": DOC_A, "document_b": DOC_B, "demo": True})
        self.assertFalse(result["protocol_repaired"])


if __name__ == "__main__":
    unittest.main()
