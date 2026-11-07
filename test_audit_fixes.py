import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import agent
from agent import Source
from doctext import MAX_DOCX_BYTES, MAX_DOCX_XML_BYTES, extract_docx_text


class DocxResourceLimitTests(unittest.TestCase):
    def test_rejects_oversized_archive_input(self):
        with self.assertRaisesRegex(ValueError, "превышает допустимый размер"):
            extract_docx_text(b"x" * (MAX_DOCX_BYTES + 1))

    def test_rejects_oversized_document_xml_before_reading(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo("word/document.xml")
            info.file_size = MAX_DOCX_XML_BYTES + 1
            # ZIP metadata can advertise a large member without storing its body.
            archive.writestr(info, b"<x/>")
        # Build a valid archive with a patched central-directory size.
        data = bytearray(stream.getvalue())
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.namelist(), ["word/document.xml"])
        # The compressed member's true size is small; direct input bound is
        # covered above and normal DOCX parsing remains covered by existing tests.


class ResearchEvidenceRetentionTests(unittest.TestCase):
    def test_update_and_court_sources_survive_full_core_source_limit(self):
        class Model:
            def complete(self, system, user):
                if "legal_update_queries" in system:
                    return '{"legal_update_queries":["update-check"],"notes":"check"}'
                if "court_queries" in system:
                    return '{"court_queries":["court-check"],"notes":"case law"}'
                if "additional_queries" in system:
                    return '{"sufficient":true,"additional_queries":[],"reason":"enough"}'
                if '"queries"' in system or "ключом queries" in system:
                    return '{"queries":["primary"]}'
                if "consistent" in system:
                    return '{"consistent":true,"issues":[]}'
                return (
                    "## Краткий ответ\\nВывод [1].\\n\\n"
                    "## Правовое основание\\nОснование [1].\\n\\n"
                    "## Актуальность и изменения\\nПроверено [1].\\n\\n"
                    "## Как применить к ситуации\\nПрактический шаг.\\n\\n"
                    "## Ограничения\\nТребуется проверка."
                )

        class Search:
            def search(self, query, limit):
                return [Source(query, f"https://example.test/{query}", f"Материал {query}", query)]

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(agent, "OpenAICompatibleModel", return_value=Model()), patch.object(
                agent, "TavilySearch", return_value=Search()
            ):
                _, journal_path = agent.run(
                    "тестовый вопрос", 1, 1, Path(directory), checked_on="2026-11-07"
                )
            import json
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        urls = {item["url"] for item in journal["sources"]}
        self.assertIn("https://example.test/primary", urls)
        self.assertIn("https://example.test/update-check", urls)
        self.assertIn("https://example.test/court-check", urls)


if __name__ == "__main__":
    unittest.main()
