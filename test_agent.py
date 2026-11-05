import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import AgentError, OpenAICompatibleModel, Source, deduplicate, extract_json, run


class JsonParsingTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(extract_json('{"queries": ["тема"]}'), {"queries": ["тема"]})

    def test_markdown_json(self):
        self.assertEqual(extract_json('```json\n{"queries": ["тема"]}\n```'), {"queries": ["тема"]})

    def test_invalid_json(self):
        with self.assertRaises(AgentError):
            extract_json("не JSON")


class SourceTests(unittest.TestCase):
    def test_deduplicates_and_limits(self):
        sources = [
            Source("A", "https://example.org/a", "1", "q"),
            Source("A copy", "https://example.org/a/", "2", "q"),
            Source("B", "https://example.org/b", "3", "q"),
        ]
        result = deduplicate(sources, 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, "A")


class ConfigurationTests(unittest.TestCase):
    def test_model_requires_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AgentError):
                OpenAICompatibleModel()


class DemoRunTests(unittest.TestCase):
    def test_complete_demo_run(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path, run_path = run("Тестовая тема", 3, 6, Path(directory), demo=True)
            report = report_path.read_text(encoding="utf-8")
            journal = json.loads(run_path.read_text(encoding="utf-8"))
            self.assertIn("# Тестовая тема", report)
            self.assertIn("Демонстрационный режим", report)
            self.assertIn("## Источники", report)
            self.assertTrue(journal["demo"])
            self.assertEqual(journal["queries"][0], "Тестовая тема")
            self.assertEqual(len(journal["queries"]), 3)
            self.assertEqual(len(journal["sources"]), 2)


if __name__ == "__main__":
    unittest.main()
