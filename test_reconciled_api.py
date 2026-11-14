"""Regression tests for privacy-safe API and truthful auth metadata."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from agent import AgentError
from api import create_server, handle_ask


class ReconciledApiTests(unittest.TestCase):
    def test_temporary_request_directory_is_new_and_cleaned_after_failure(self):
        captured = []
        def simulated_failure(**kwargs):
            directory = kwargs["output_dir"]
            self.assertFalse(directory.exists())
            directory.mkdir(parents=True)
            (directory / "private.txt").write_text("sensitive")
            captured.append(directory)
            raise AgentError("downstream failure")
        with patch("api.run", side_effect=simulated_failure), self.assertRaises(AgentError):
            handle_ask({"question": "private"}, Path("."), persist_results=False)
        self.assertFalse(captured[0].exists())

    def test_openapi_auth_is_consistent_with_running_server(self):
        for token in ("", "test-token"):
            with self.subTest(token=bool(token)):
                server = create_server("127.0.0.1", 0, Path("."), token)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f"http://127.0.0.1:{server.server_port}/openapi.json"
                    with urllib.request.urlopen(url, timeout=3) as response:
                        spec = json.load(response)
                    self.assertEqual(spec["components"]["securitySchemes"]["ApiToken"]["scheme"], "bearer")
                    self.assertEqual(spec["paths"]["/ask"]["post"]["security"],
                                     [{"ApiToken": []}] if token else [])
                    self.assertEqual(spec["paths"]["/health"]["get"]["security"], [])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
