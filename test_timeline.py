import json
import unittest

from agent import AgentError
from timeline import (
    DemoTimelineModel,
    TimelineError,
    extract_timeline,
    handle_timeline,
    sort_events,
    validate_materials,
    validate_timeline,
)

MATERIALS = [
    {
        "name": "письмо-1.txt",
        "text": "Уважаемые коллеги! 15.01.2026 мы направили вам счёт № 45 на сумму 200 000 руб. "
        "по договору аренды. Оплата до 20.01.2026 не поступила.",
    },
    {
        "name": "письмо-2.txt",
        "text": "Добрый день! Подтверждаем получение претензии от 01.02.2026. "
        "Платёж обещаем выполнить до конца недели, просим не обращаться в суд.",
    },
]

VALID_RESULT = {
    "events": [
        {
            "date": "15.01.2026",
            "event": "Выставлен счёт № 45 на 200 000 руб.",
            "source": "письмо-1.txt",
            "quote": "15.01.2026 мы направили вам счёт № 45 на сумму 200 000 руб.",
        },
        {
            "date": "01.02.2026",
            "event": "Контрагент подтвердил получение претензии",
            "source": "письмо-2.txt",
            "quote": "Подтверждаем получение претензии от 01.02.2026.",
        },
    ]
}


class ValidateMaterialsTests(unittest.TestCase):
    def test_valid_materials(self):
        normalized = validate_materials({"materials": MATERIALS})
        self.assertEqual([item["name"] for item in normalized], ["письмо-1.txt", "письмо-2.txt"])

    def test_empty_materials_rejected(self):
        with self.assertRaises(TimelineError):
            validate_materials({"materials": []})
        with self.assertRaises(TimelineError):
            validate_materials({})

    def test_duplicate_names_rejected(self):
        with self.assertRaises(TimelineError):
            validate_materials({"materials": [MATERIALS[0], MATERIALS[0]]})

    def test_short_material_rejected(self):
        with self.assertRaises(TimelineError):
            validate_materials({"materials": [{"name": "a.txt", "text": "коротко"}]})

    def test_default_name_assigned(self):
        normalized = validate_materials({"materials": [{"text": "Материал без имени, но достаточной длины."}]})
        self.assertEqual(normalized[0]["name"], "материал-1")


class ValidateTimelineTests(unittest.TestCase):
    def test_valid_result_passes(self):
        self.assertEqual(validate_timeline(VALID_RESULT, MATERIALS), [])

    def test_unknown_source_flagged(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["events"][0]["source"] = "несуществующий.txt"
        issues = validate_timeline(result, MATERIALS)
        self.assertTrue(any("неизвестный источник" in issue for issue in issues))

    def test_bad_date_flagged(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["events"][0]["date"] = "32.13.2026"
        issues = validate_timeline(result, MATERIALS)
        self.assertTrue(any("дата" in issue for issue in issues))

    def test_quote_from_wrong_source_flagged(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["events"][0]["quote"] = "Подтверждаем получение претензии от 01.02.2026."
        issues = validate_timeline(result, MATERIALS)
        self.assertTrue(any("цитата не найдена" in issue for issue in issues))

    def test_empty_date_allowed(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["events"][0]["date"] = ""
        self.assertEqual(validate_timeline(result, MATERIALS), [])


class SortEventsTests(unittest.TestCase):
    def test_sorted_by_date(self):
        events = list(reversed(VALID_RESULT["events"]))
        dated, undated = sort_events(events)
        self.assertEqual([item["date"] for item in dated], ["15.01.2026", "01.02.2026"])
        self.assertEqual(undated, [])

    def test_undated_separated(self):
        events = VALID_RESULT["events"] + [{"date": "", "event": "Без даты", "source": "x", "quote": "q"}]
        dated, undated = sort_events(events)
        self.assertEqual(len(dated), 2)
        self.assertEqual(len(undated), 1)
        self.assertEqual(undated[0]["event"], "Без даты")


class BrokenTimelineModel:
    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        result = json.loads(json.dumps(VALID_RESULT))
        if self.calls == 1:
            result["events"][0]["quote"] = "Выдуманная цитата, которой нет нигде."
        return json.dumps(result, ensure_ascii=False)


class AlwaysBrokenModel:
    def complete(self, system, user):
        result = json.loads(json.dumps(VALID_RESULT))
        result["events"][0]["source"] = "несуществующий.txt"
        return json.dumps(result, ensure_ascii=False)


class ExtractTimelineTests(unittest.TestCase):
    def test_repairs_once(self):
        model = BrokenTimelineModel()
        result, repaired = extract_timeline(model, MATERIALS)
        self.assertTrue(repaired)
        self.assertEqual(validate_timeline(result, MATERIALS), [])

    def test_fails_after_bad_repair(self):
        with self.assertRaises(AgentError):
            extract_timeline(AlwaysBrokenModel(), MATERIALS)


class DemoTimelineTests(unittest.TestCase):
    def test_demo_extracts_dates(self):
        model = DemoTimelineModel()
        result = json.loads(model.complete("", "=== Источник: письмо-1.txt ===\n" + MATERIALS[0]["text"]))
        self.assertEqual(result["events"][0]["date"], "15.01.2026")

    def test_handle_timeline_demo(self):
        result = handle_timeline({"materials": MATERIALS, "demo": True})
        self.assertTrue(result["demo"])
        self.assertEqual(result["status"], "draft")
        self.assertGreaterEqual(result["events_count"], 1)
        dates = [event["date"] for event in result["events"]]
        self.assertEqual(dates, sorted(dates, key=lambda d: (d[6:], d[3:5], d[:2])))
        self.assertIn("письмо-1.txt", result["materials"])


if __name__ == "__main__":
    unittest.main()
