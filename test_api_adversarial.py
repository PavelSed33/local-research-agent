"""Adversarial API regression suite: run offline against a local demo server."""
import concurrent.futures
import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from api import create_server


class AdversarialApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.server = create_server("127.0.0.1", 0, Path(self.directory.name), "test-secret")
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.directory.cleanup)

    def post(self, payload, token="test-secret"):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/ask",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_invalid_research_does_not_allocate_sessions_or_jobs(self):
        invalid = [
            {"question": "Тест", "demo": "yes"},
            {"question": "Тест", "queries": 0},
            {"question": "Тест", "sources": 11},
            {"question": "Тест", "as_of": "2026-02-30"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                status, _ = self.post(payload)
                self.assertEqual(status, 400)
                self.assertFalse(self.server.sessions._sessions)
                self.assertFalse(self.server.jobs.active)

    def test_invalid_request_id_does_not_create_session(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/ask",
            data=json.dumps({"question": "Тест", "demo": True}).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer test-secret",
                     "X-Request-ID": "../bad"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 400)
        self.assertFalse(self.server.sessions._sessions)
        self.assertFalse(self.server.jobs.active)

    def test_authentication_matrix(self):
        for token in (None, "", "incorrect", "test-secret-extra"):
            with self.subTest(token=token):
                status, _ = self.post({"question": "Тест", "demo": True}, token)
                self.assertEqual(status, 401)
        status, _ = self.post({"question": "Тест", "demo": True})
        self.assertEqual(status, 200)

    def test_input_boundary_matrix(self):
        cases = [
            ({"question": "", "demo": True}, 400),
            ({"question": "x" * 8001, "demo": True}, 400),
            ({"question": "Тест", "demo": "true"}, 400),
            ({"question": "Тест", "queries": 0, "demo": True}, 400),
            ({"question": "Тест", "sources": 11, "demo": True}, 400),
            ({"question": "Тест", "session_id": "guessable", "demo": True}, 400),
        ]
        for payload, expected in cases:
            with self.subTest(payload=str(payload)[:100]):
                status, _ = self.post(payload)
                self.assertEqual(status, expected)

    def test_parallel_sessions_are_unique(self):
        def create(index):
            return self.post({"question": f"Дело {index}", "demo": True})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(create, range(16)))
        # The server deliberately caps concurrent jobs; overload must be rejected,
        # not silently admitted beyond the configured capacity.
        self.assertTrue(all(status in (200, 429) for status, _ in results))
        accepted = [payload for status, payload in results if status == 200]
        rejected = [payload for status, payload in results if status == 429]
        self.assertTrue(accepted)
        self.assertTrue(all(payload.get("code") == "request_capacity" for payload in rejected))
        ids = [payload.get("session_id") for payload in accepted]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(isinstance(sid, str) and len(sid) == 48 for sid in ids))

    def test_reject_ambiguous_framing(self):
        body = b'{"question":"x","demo":true}'
        for extra_headers in (
            b"Content-Length: 28\r\nContent-Length: 28\r\n",
            b"Transfer-Encoding: chunked\r\n",
        ):
            with self.subTest(extra_headers=extra_headers):
                request = (
                    b"POST /ask HTTP/1.1\r\nHost: localhost\r\n"
                    b"Authorization: Bearer test-secret\r\n"
                    + extra_headers
                    + b"Connection: close\r\n\r\n"
                    + body
                )
                with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
                    sock.settimeout(5)
                    sock.sendall(request)
                    response = sock.recv(4096)
                self.assertIn(b" 400 ", response)


if __name__ == "__main__":
    unittest.main()
