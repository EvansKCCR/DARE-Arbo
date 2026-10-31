from datetime import date, datetime, timezone
from io import BytesIO
from unittest import TestCase
from unittest.mock import MagicMock, patch
from openpyxl import Workbook, load_workbook
from assignment_notifications import process_notifications, assignment_message
from activity_calendar import calendar_bytes, calendar_html, month_activities
import test_workbook_tasks as task_tests


class AssignmentQueueTests(TestCase):
    setUp = task_tests.WorkbookTaskTests.setUp
    # Reuse the in-memory Drive adapter; every write still asserts If-Match.
    def test_new_assignment_queues_only_new_recipient(self):
        task=self.store.list()[0]
        result=self.store.save({'assignee':'one@example.org,two@example.org'},2,task['version'])
        self.assertEqual(1,len(result['notification_events']))
        self.assertEqual('two@example.org',self.store.notification_events()[0]['recipient'])
        self.assertEqual([],self.store.save({'note':'Updated'},2,self.store.list()[0]['version'])['notification_events'])
        event=result['notification_events'][0]
        self.assertIsNotNone(self.store.claim_assignment(event))
        self.assertIsNone(self.store.claim_assignment(event))
        self.store.finish_assignment(event,'sent')
        self.assertEqual('sent',self.store.notification_events()[0]['state'])

    def test_removed_or_unapproved_assignee_cannot_receive(self):
        result=self.store.save({'assignee':'two@example.org'},2,self.store.list()[0]['version'])
        self.people.remove('two@example.org')
        self.assertIsNone(self.store.claim_assignment(result['notification_events'][0]))
        self.assertEqual('skipped',self.store.notification_events()[0]['state'])

    def test_direct_sheet_changes_are_detected_after_baseline(self):
        self.assertEqual([],self.store.sync_assignments())
        self.assertEqual([],self.store.notification_events())
        book=load_workbook(BytesIO(self.content))
        book['Activity Register']['I2']='two@example.org'
        out=BytesIO();book.save(out);book.close();self.content=out.getvalue()
        self.assertEqual(1,len(self.store.sync_assignments()))
        self.assertEqual([],self.store.sync_assignments())
        self.assertEqual(1,len(self.store.notification_events()))

    def test_collaborator_cannot_claim_notifications(self):
        self.person['email']='one@example.org'
        for action in (self.store.sync_assignments,self.store.notification_events,lambda:self.store.claim_assignment('event')):
            with self.assertRaises(PermissionError):
                action()

    def test_calendar_view_updates_and_respects_assignment_filter(self):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_string('''
from project_management import show_project_management
import streamlit as st
show_project_management(lambda:{'drive':dict(client_id='test',client_secret='test',refresh_token='test'),
'project_management':{'admin_emails':['admin@example.org']}},lambda:st.session_state['person'],
lambda:['admin@example.org','one@example.org'])
''',default_timeout=30)
        app.session_state['person']=self.person.copy()
        with patch('project_management.create_service'),patch('sheet_dashboard.create_service'),patch('sheet_dashboard.DriveStore.download',side_effect=lambda *args:('file.xlsx','',self.content)),patch('project_management.WorkbookTasks',return_value=self.store):
            app.run()
            next(r for r in app.radio if r.label=='Schedule view').set_value('Activity calendar').run()
            self.assertFalse(app.exception)
            next(s for s in app.selectbox if s.label=='Calendar month').set_value(10)
            next(n for n in app.number_input if n.label=='Calendar year').set_value(2026)
            app.run()
            self.assertTrue(app.get('download_button'))
            next(c for c in app.checkbox if c.label=='Only activities assigned to me').check().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.get('download_button'))
            self.assertTrue(any('No scheduled activities match' in item.value for item in app.info))


class CalendarAndDeliveryTests(TestCase):
    def setUp(self):
        self.task=dict(id='A',record_key='A',activity='Review, methods; α\nBEGIN:BAD',workstream='Pilot',
                       owner='EAA',output='Dataset',status='In progress',progress=None,
                       assignee='one@example.org',start=date(2026,10,30),end=date(2026,11,2))
        self.config=dict(host='smtp.test',port=587,username='admin@example.org',password='test',
                         from_address='admin@example.org',app_url='https://example.org')

    def test_calendar_inclusive_dates_stable_uid_and_safe_escaping(self):
        now=datetime(2026,10,30,tzinfo=timezone.utc)
        data=calendar_bytes([self.task],now)
        self.assertIn(b'DTSTART;VALUE=DATE:20261030',data)
        self.assertIn(b'DTEND;VALUE=DATE:20261103',data)
        self.assertNotIn(b'\r\nBEGIN:BAD',data)
        self.assertIn(b'\\,',data)
        self.assertIn(b'\\;',data)
        self.assertEqual(data,calendar_bytes([self.task],now))
        self.assertTrue(all(len(line)<=75 for line in calendar_bytes([{**self.task,'activity':'α'*100}],now).split(b'\r\n')))
        self.assertEqual(1,len(month_activities([self.task],2026,11)))
        self.assertEqual([],month_activities([self.task],2026,12))

    def test_calendar_escapes_html_and_missing_schedule(self):
        html=calendar_html([{**self.task,'activity':'<script>bad</script>'}],2026,10,date(2026,10,30))
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertEqual([],month_activities([{**self.task,'start':None,'end':None}],2026,10))

    def test_message_has_private_recipient_and_calendar_attachment(self):
        message=assignment_message(self.task,'one@example.org',self.config)
        self.assertEqual('one@example.org',message['To'])
        self.assertIsNone(message['Cc'])
        self.assertEqual('DARE-Arbo_activity.ics',list(message.iter_attachments())[0].get_filename())

    def test_delivery_dry_run_success_and_uncertain_failure(self):
        store=MagicMock()
        store.is_admin.return_value=True
        store.notification_events.return_value=[dict(event_id='1',state='queued')]
        store.claim_assignment.return_value=dict(task=self.task,recipient='one@example.org')
        with patch('assignment_notifications.smtplib.SMTP') as smtp:
            process_notifications(store,self.config,send=False)
            store.claim_assignment.assert_not_called();smtp.assert_not_called()
            result=process_notifications(store,self.config,send=True)
            self.assertEqual(1,result['sent'])
            store.finish_assignment.assert_called_with('1','sent')
            smtp.return_value.__enter__.return_value.send_message.side_effect=TimeoutError()
            result=process_notifications(store,self.config,send=True)
            self.assertEqual(1,result['uncertain'])
            store.finish_assignment.assert_called_with('1','failed_or_uncertain')
