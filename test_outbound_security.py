import io
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from agent import AgentError, post_json, validate_service_url


class OutboundTransportTests(unittest.TestCase):
    def test_external_http_and_credential_urls_are_rejected_before_network(self):
        for url in ('http://example.org/api', 'https://user:pass@example.org/api',
                    'https://example.org/api#other', 'file:///tmp/secret', 'https://[broken'):
            with self.subTest(url=url), patch('agent.open_json_request') as open_request:
                with self.assertRaises(AgentError):
                    post_json(url, {'document': 'private'}, {})
                open_request.assert_not_called()

    def test_https_and_local_development_urls_are_allowed(self):
        for url in ('https://example.org/v1', 'http://127.0.0.1:1234/v1',
                    'http://localhost:8080/v1', 'http://[::1]:8080/v1'):
            validate_service_url(url)

    def test_real_redirects_do_not_forward_credentials_or_document(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(self.path)
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.send_response(int(self.path.strip('/')))
                self.send_header('Location', '/sink')
                self.send_header('Content-Length', '0')
                self.end_headers()
            def do_GET(self):
                received.append(self.path)
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for code in (301, 302, 303, 307, 308):
                with self.subTest(code=code), self.assertRaisesRegex(AgentError, 'Перенаправление'):
                    post_json(f'http://127.0.0.1:{server.server_port}/{code}',
                              {'document': 'private'}, {'Authorization': 'Bearer private-token'})
            self.assertEqual(received, ['/301', '/302', '/303', '/307', '/308'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_api_token_comparison_accepts_unicode_without_crashing(self):
        from api import LegalApiHandler
        handler = object.__new__(LegalApiHandler)
        handler.server = type('Server', (), {'api_token': 'локальный-токен'})()
        handler.headers = {'Authorization': 'Bearer локальный-токен'}
        self.assertTrue(handler._authorized())
        handler.headers = {'Authorization': 'Bearer неправильный'}
        self.assertFalse(handler._authorized())

    def test_corrupt_utf8_and_timeouts_are_sanitized(self):
        with patch('agent.open_json_request', return_value=io.BytesIO(b'\xff')), self.assertRaisesRegex(AgentError, 'JSON'):
            post_json('https://example.org', {}, {})
        with patch('agent.open_json_request', side_effect=TimeoutError('PRIVATE')), patch('agent.time.sleep'), self.assertRaises(AgentError) as caught:
            post_json('https://example.org', {}, {})
        self.assertNotIn('PRIVATE', str(caught.exception))
