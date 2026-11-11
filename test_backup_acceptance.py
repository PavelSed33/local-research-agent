import unittest
from scripts.backup_acceptance import run_acceptance

class BackupHttpAcceptanceTests(unittest.TestCase):
    def test_recovered_workspace_keeps_authorization_and_rejects_old_credentials(self):
        result=run_acceptance()
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['http_checks'],9)
        self.assertTrue(result['old_credentials_rejected'])
        self.assertTrue(result['viewer_and_outsider_acl_preserved'])
        self.assertFalse(result['credentials_in_report'])
