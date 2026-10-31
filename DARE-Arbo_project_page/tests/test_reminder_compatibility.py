from datetime import date
from unittest import TestCase
from unittest.mock import MagicMock, patch
import send_task_reminders as reminders


class ReminderCompatibilityTests(TestCase):
    def test_dashboard_horizon_does_not_change_email_horizon(self):
        task = dict(assignee='one@example.org',status='In progress',progress=None,
                    start_date='2026-10-01',due_date='2026-11-06')
        self.assertFalse(reminders.eligible(task,['one@example.org'],date(2026,10,30)))
        self.assertTrue(reminders.eligible({**task,'due_date':'2026-11-02'},['one@example.org'],date(2026,10,30)))

    def test_dry_run_and_existing_claim_do_not_send_or_write(self):
        task = dict(id=2,title='Review',assignee='one@example.org',status='In progress',progress=None,
                    start_date='2000-01-01',due_date='2000-01-02')
        store=MagicMock()
        store.list.return_value=[task]
        env=dict(TASK_ALLOWED_EMAILS='admin@example.org,one@example.org',TASK_ADMIN_EMAIL='admin@example.org',
                 DRIVE_CLIENT_ID='test',DRIVE_CLIENT_SECRET='test',DRIVE_REFRESH_TOKEN='test')
        with patch.dict('os.environ',env), patch.object(reminders,'create_service'), patch.object(reminders,'WorkbookTasks',return_value=store), patch.object(reminders.smtplib,'SMTP') as smtp, patch('builtins.print'):
            reminders.run(False)
            store.claim_reminder.assert_not_called()
            smtp.assert_not_called()
            store.claim_reminder.return_value=False
            reminders.run(True)
            store.claim_reminder.assert_called_once()
            smtp.assert_not_called()
