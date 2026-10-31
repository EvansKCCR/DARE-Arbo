from copy import deepcopy
from unittest import TestCase
from assignment_notifications import sender_secret_status,configured


class SenderSecretStatusTests(TestCase):
    def test_checks_presence_without_returning_or_mutating_password(self):
        settings={'email':{'password':'private-test-value','app_password':'alternate-test-value'},'drive':{'refresh_token':'private-drive-value'}}
        original=deepcopy(settings)
        result=sender_secret_status(settings)
        self.assertEqual(settings,original)
        self.assertEqual('email.password is present (value hidden)',result['password'])
        self.assertEqual(['email.app_password'],result['alternate_keys'])
        self.assertNotIn('private-test-value',str(result))
        self.assertNotIn('alternate-test-value',str(result))
        self.assertNotIn('private-drive-value',str(result))

    def test_distinguishes_missing_empty_and_misplaced(self):
        self.assertEqual('Missing email.password key',sender_secret_status({})['password'])
        self.assertEqual('email.password is empty',sender_secret_status({'email':{'password':'  '}})['password'])
        self.assertIn('quoted string',sender_secret_status({'email':{'password':123}})['password'])
        settings={'email':{},'SMTP_PASSWORD':'hidden'}
        self.assertEqual(['SMTP_PASSWORD'],sender_secret_status(settings)['alternate_keys'])
        self.assertEqual({'email':{},'SMTP_PASSWORD':'hidden'},settings)
        config=dict(host='smtp.test',username='test',password='  ',from_address='test@example.org',app_url='https://example.org')
        self.assertFalse(configured(config))
        config['password']=123
        self.assertFalse(configured(config))
