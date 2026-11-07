import unittest
from unittest.mock import patch

import api
from api import SessionStore
import test_api


class SessionDeletionTests(unittest.TestCase):
    def test_delete_preserves_other_sessions_and_is_idempotent(self):
        store = SessionStore()
        store.remember('a', 'private', 'answer')
        store.remember('b', 'other', 'answer')
        self.assertTrue(store.delete('a'))
        self.assertFalse(store.delete('a'))
        self.assertEqual(store.context('a'), '')
        self.assertIn('other', store.context('b'))

    def test_inflight_update_cannot_restore_deleted_or_recreated_session(self):
        store = SessionStore()
        _, old_identity = store.begin('a')
        store.delete('a')
        store.remember('a', 'old secret', 'answer', expected_session=old_identity)
        self.assertEqual(store.context('a'), '')
        _, new_identity = store.begin('a')
        store.remember('a', 'new question', 'answer', expected_session=new_identity)
        store.remember('a', 'old secret', 'answer', expected_session=old_identity)
        self.assertIn('new question', store.context('a'))
        self.assertNotIn('old secret', store.context('a'))

    def test_expired_session_does_not_accept_stale_request(self):
        store = SessionStore(ttl_seconds=1)
        with patch('api.time.time', return_value=100):
            _, identity = store.begin('a')
        with patch('api.time.time', return_value=102):
            store.remember('a', 'stale', 'answer', expected_session=identity)
            self.assertEqual(store.context('a'), '')


class DeleteEndpointTests(test_api.ApiTestCase):
    api_token = 'test-delete-token'

    def test_authenticated_delete_removes_history(self):
        self.server.sessions.remember('a', 'private', 'answer')
        status, payload = self.request('POST', '/sessions/delete', {'session_id': 'a'}, token=self.api_token)
        self.assertEqual(status, 200)
        self.assertTrue(payload['deleted'])
        self.assertEqual(self.server.sessions.context('a'), '')
        status, payload = self.request('POST', '/sessions/delete', {'session_id': 'a'}, token=self.api_token)
        self.assertFalse(payload['deleted'])

    def test_unauthorized_delete_has_no_effect(self):
        self.server.sessions.remember('a', 'private', 'answer')
        status, _ = self.request('POST', '/sessions/delete', {'session_id': 'a'})
        self.assertEqual(status, 401)
        self.assertIn('private', self.server.sessions.context('a'))

    def test_invalid_id_does_not_delete(self):
        self.server.sessions.remember('a', 'private', 'answer')
        for value in (None, '', 'a\n', '../a', 1):
            status, _ = self.request('POST', '/sessions/delete', {'session_id': value}, token=self.api_token)
            self.assertEqual(status, 400)
        self.assertIn('private', self.server.sessions.context('a'))

    def test_next_research_receives_no_deleted_context(self):
        self.server.sessions.remember('a', 'private previous question', 'answer')
        self.request('POST', '/sessions/delete', {'session_id': 'a'}, token=self.api_token)
        with patch('api.run', wraps=api.run) as run:
            status, _ = self.request('POST', '/ask', {'question': 'Новый вопрос', 'session_id': 'a', 'demo': True}, token=self.api_token)
        self.assertEqual(status, 200)
        self.assertEqual(run.call_args.kwargs['session_context'], '')
