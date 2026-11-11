import json
import unittest

from agent import AgentError
from draft import (
    DOCUMENT_TYPES,
    DemoDraftModel,
    DraftError,
    draft_document,
    handle_draft,
    validate_draft,
)

FACTS = (
    "По договору аренды № 12 от 01.02.2026 арендатор ООО «Арендатор» не оплатил "
    "арендную плату за сентябрь и октябрь 2026 года на общую сумму 200 000 руб. "
    "Нужна претензия с требованием оплатить долг в течение 10 календарных дней."
)
CONTRACT = (
    "ДОГОВОР АРЕНДЫ № 12 от 01.02.2026. ООО «Арендодатель» и ООО «Арендатор» заключили договор. "
    "Арендная плата составляет 100 000 руб. в месяц и вносится не позднее 10 числа месяца. "
    "Споры рассматриваются в Арбитражном суде города Москвы."
)

VALID_RESULT = {
    "title": "Претензия о взыскании задолженности по аренде",
    "document": (
        "# ПРЕТЕНЗИЯ\n\n"
        "Кому: ООО «Арендатор»\n\nОт: ООО «Арендодатель»\n\n"
        "По договору аренды № 12 от 01.02.2026 арендная плата вносится ежемесячно. "
        "За сентябрь и октябрь 2026 года платежи не поступили, задолженность составляет 200 000 руб.\n\n"
        "Требуем: в течение 10 календарных дней оплатить задолженность в размере 200 000 руб. "
        "В случае неисполнения требования в указанный срок будем вынуждены обратиться в суд "
        "для взыскания задолженности, неустойки и судебных расходов.\n\n"
        "Приложение: расчёт задолженности.\n\n"
        "[должность, ФИО]  ______________  [дата]"
    ),
    "references": [{"quote": "Арендная плата составляет 100 000 руб. в месяц и вносится не позднее 10 числа месяца."}],
}


class ValidateDraftTests(unittest.TestCase):
    def test_valid_result_passes(self):
        self.assertEqual(validate_draft(VALID_RESULT, "claim", CONTRACT), [])

    def test_missing_required_block_flagged(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["document"] = (
            result["document"]
            .replace("Требуем", "Просим")
            .replace("неисполнения требования", "неисполнения обязательства")
        )
        issues = validate_draft(result, "claim", CONTRACT)
        self.assertTrue(any("треб" in issue for issue in issues))

    def test_bad_reference_flagged(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["references"] = [{"quote": "Такой фразы в договоре не существует."}]
        issues = validate_draft(result, "claim", CONTRACT)
        self.assertTrue(any("цитата не найдена" in issue for issue in issues))

    def test_references_without_contract_flagged(self):
        issues = validate_draft(VALID_RESULT, "claim", "")
        self.assertTrue(any("договор не передан" in issue for issue in issues))

    def test_no_signature_flagged(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["document"] = result["document"].replace("[должность, ФИО]  ______________  [дата]", "Конец.")
        issues = validate_draft(result, "claim", CONTRACT)
        self.assertTrue(any("подпис" in issue for issue in issues))

    def test_each_type_has_required_blocks(self):
        for doc_type, meta in DOCUMENT_TYPES.items():
            self.assertTrue(meta["required_blocks"])
            self.assertTrue(meta["title"])


class RepairingDraftModel:
    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        result = json.loads(json.dumps(VALID_RESULT))
        if self.calls == 1:
            result["references"] = [{"quote": "Выдуманная цитата из ниоткуда."}]
        return json.dumps(result, ensure_ascii=False)


class AlwaysBrokenDraftModel:
    def complete(self, system, user):
        result = json.loads(json.dumps(VALID_RESULT))
        result["document"] = "Коротко."
        return json.dumps(result, ensure_ascii=False)


class DraftRepairTests(unittest.TestCase):
    def test_repairs_once(self):
        model = RepairingDraftModel()
        result, repaired = draft_document(model, "claim", FACTS, CONTRACT)
        self.assertTrue(repaired)
        self.assertEqual(validate_draft(result, "claim", CONTRACT), [])

    def test_fails_after_bad_repair(self):
        with self.assertRaises(AgentError):
            draft_document(AlwaysBrokenDraftModel(), "claim", FACTS, CONTRACT)


class HandleDraftTests(unittest.TestCase):
    def test_demo_draft(self):
        result = handle_draft({"type": "claim", "facts": FACTS, "contract": CONTRACT, "demo": True})
        self.assertTrue(result["demo"])
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["doc_type"], "claim")
        self.assertTrue(result["document"])
        self.assertEqual(len(result["references"]), 1)
        self.assertTrue(result["warnings"])  # есть плейсхолдеры
        self.assertIn("не индивидуальная юридическая консультация", result["disclaimer"])

    def test_demo_without_contract(self):
        result = handle_draft({"type": "notification", "facts": FACTS, "demo": True})
        self.assertEqual(result["references"], [])

    def test_unknown_type_rejected(self):
        with self.assertRaises(DraftError):
            handle_draft({"type": "иск", "facts": FACTS, "demo": True})

    def test_short_facts_rejected(self):
        with self.assertRaises(DraftError):
            handle_draft({"type": "claim", "facts": "коротко", "demo": True})

    def test_demo_model_output_valid(self):
        result = json.loads(DemoDraftModel().complete(
            "Ты юрист-драфтер, готовящий документ по праву РФ: досудебная претензия контрагенту. ",
            f"Подготовь проект документа.\n\nОписание ситуации:\n{FACTS}\n\nТекст договора (основание):\n\n{CONTRACT}",
        ))
        self.assertEqual(validate_draft(result, "claim", CONTRACT), [])


if __name__ == "__main__":
    unittest.main()
