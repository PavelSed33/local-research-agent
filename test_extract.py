import json
import unittest

from agent import AgentError
from extract import (
    checksum_warnings,
    extract_entities,
    handle_extract,
    inn_checksum_ok,
    ogrn_checksum_ok,
    validate_extraction,
)

DOCUMENT = """ДОГОВОР АРЕНДЫ № 12

г. Москва                                                    01.02.2026

ООО «Арендодатель», ИНН 7707083893, ОГРН 1027700132195, и ООО «Арендатор» заключили настоящий договор о нижеследующем.

1. ПРЕДМЕТ ДОГОВОРА
1.1. Арендодатель передаёт Арендатору помещение площадью 50 кв. м по адресу: г. Москва, ул. Примерная, д. 1.

2. ЦЕНА
2.1. Арендная плата составляет 100 000 руб. в месяц.

3. СРОК ДЕЙСТВИЯ
3.1. Договор действует до 31.12.2026.

7. СПОРЫ
7.1. Споры рассматриваются в Арбитражном суде города Москвы.
"""

VALID_RESULT = {
    "parties": [
        {
            "role": "арендодатель",
            "name": "ООО «Арендодатель»",
            "inn": "7707083893",
            "ogrn": "1027700132195",
            "quote": "ООО «Арендодатель», ИНН 7707083893, ОГРН 1027700132195, и ООО «Арендатор» заключили настоящий договор о нижеследующем.",
        }
    ],
    "amounts": [
        {
            "label": "арендная плата",
            "value": "100 000 руб. в месяц",
            "currency": "RUB",
            "quote": "Арендная плата составляет 100 000 руб. в месяц.",
        }
    ],
    "dates": [
        {
            "label": "окончание действия",
            "date": "31.12.2026",
            "quote": "Договор действует до 31.12.2026.",
        }
    ],
    "penalties": [],
    "term": {"duration": "до 31.12.2026", "quote": "Договор действует до 31.12.2026."},
    "jurisdiction": {
        "court": "Арбитражный суд города Москвы",
        "quote": "Споры рассматриваются в Арбитражном суде города Москвы.",
    },
}


class ChecksumTests(unittest.TestCase):
    def test_valid_inn_10(self):
        self.assertTrue(inn_checksum_ok("7707083893"))

    def test_valid_inn_12(self):
        self.assertTrue(inn_checksum_ok("500100732259"))

    def test_invalid_inn_checksum(self):
        self.assertFalse(inn_checksum_ok("7707083894"))

    def test_wrong_inn_length(self):
        self.assertFalse(inn_checksum_ok("770708389"))

    def test_valid_ogrn_13(self):
        self.assertTrue(ogrn_checksum_ok("1027700132195"))

    def test_invalid_ogrn_checksum(self):
        self.assertFalse(ogrn_checksum_ok("1027700132196"))


class ExtractionValidationTests(unittest.TestCase):
    def test_valid_result_passes(self):
        self.assertEqual(validate_extraction(VALID_RESULT, DOCUMENT), [])

    def test_quote_not_in_document_is_flagged(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["amounts"][0]["quote"] = "Такой фразы в договоре точно не существует."
        issues = validate_extraction(result, DOCUMENT)
        self.assertTrue(any("цитата не найдена" in issue for issue in issues))

    def test_inn_not_in_document_is_flagged(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["parties"][0]["inn"] = "500100732259"
        issues = validate_extraction(result, DOCUMENT)
        self.assertTrue(any("ИНН" in issue and "не найден" in issue for issue in issues))

    def test_malformed_inn_is_flagged(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["parties"][0]["inn"] = "abc"
        issues = validate_extraction(result, DOCUMENT)
        self.assertTrue(any("не похож на ИНН" in issue for issue in issues))

    def test_bad_jurisdiction_quote_is_flagged(self):
        result = json.loads(json.dumps(VALID_RESULT))
        result["jurisdiction"]["quote"] = "Выдуманная подсудность."
        issues = validate_extraction(result, DOCUMENT)
        self.assertTrue(any("jurisdiction" in issue for issue in issues))

    def test_checksum_warning_for_broken_inn_in_document(self):
        document = DOCUMENT.replace("7707083893", "7707083891")
        result = json.loads(json.dumps(VALID_RESULT))
        result["parties"][0]["inn"] = "7707083891"
        result["parties"][0]["quote"] = "ООО «Арендодатель», ИНН 7707083891, ОГРН 1027700132195, и ООО «Арендатор» заключили настоящий договор о нижеследующем."
        self.assertEqual(validate_extraction(result, document), [])
        warnings = checksum_warnings(result)
        self.assertEqual(len(warnings), 1)
        self.assertIn("7707083891", warnings[0])


class RepairingExtractModel:
    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        result = json.loads(json.dumps(VALID_RESULT))
        if self.calls == 1:
            result["amounts"][0]["quote"] = "Выдуманная формулировка, которой нет в тексте."
        return json.dumps(result, ensure_ascii=False)


class AlwaysBrokenExtractModel:
    def complete(self, system, user):
        result = json.loads(json.dumps(VALID_RESULT))
        result["dates"][0]["quote"] = "Выдуманная формулировка, которой нет в тексте."
        return json.dumps(result, ensure_ascii=False)


class ExtractRepairTests(unittest.TestCase):
    def test_repairs_extraction_once(self):
        model = RepairingExtractModel()
        result, repaired = extract_entities(model, DOCUMENT)
        self.assertTrue(repaired)
        self.assertEqual(model.calls, 2)
        self.assertEqual(validate_extraction(result, DOCUMENT), [])

    def test_fails_after_unsuccessful_repair(self):
        with self.assertRaises(AgentError):
            extract_entities(AlwaysBrokenExtractModel(), DOCUMENT)


class HandleExtractTests(unittest.TestCase):
    def test_demo_extract_returns_structured_result(self):
        result = handle_extract({"document": DOCUMENT, "demo": True})
        self.assertTrue(result["demo"])
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["parties"][0]["inn"], "7707083893")
        self.assertEqual(result["parties"][0]["ogrn"], "1027700132195")
        self.assertEqual(result["amounts"][0]["value"], "100 000 руб.")
        self.assertIn("31.12.2026", [entry["date"] for entry in result["dates"]])
        self.assertEqual(result["warnings"], [])
        self.assertTrue(result["checked_on"])
        self.assertIn("не индивидуальная юридическая консультация", result["disclaimer"])
        self.assertFalse(result["extraction_repaired"])

    def test_missing_document_is_rejected(self):
        with self.assertRaises(ValueError):
            handle_extract({"document": "", "demo": True})

    def test_short_document_is_rejected(self):
        with self.assertRaises(ValueError):
            handle_extract({"document": "слишком коротко", "demo": True})


if __name__ == "__main__":
    unittest.main()
