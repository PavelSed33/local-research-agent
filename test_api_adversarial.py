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
            status, payload = self.post({"question": f"Дело {index}", "demo": True})
            return status, payload.get("session_id")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(create, range(16)))
        self.assertTrue(all(status == 200 for status, _ in results))
        ids = [session_id for _, session_id in results]
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
