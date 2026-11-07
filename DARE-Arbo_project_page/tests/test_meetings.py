from datetime import datetime,date,time,timedelta,timezone
from hashlib import sha256
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch
from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest
import test_workbook_tasks as fixtures
from meeting_store import MeetingStore,MEETINGS,INVITATIONS,local_instant,clean_fields,meeting_calendar,invitation_message,send_invitations


class MeetingTests(TestCase):
    def setUp(self):
        fixtures.WorkbookTaskTests.setUp(self)
        original=self.store
        self.store=MeetingStore(self.service,original.identity,original.allowed,original.admins)
        self.store.read=original.read;self.store.version_service=self.service
        start=(datetime.now(timezone.utc)+timedelta(days=2)).replace(microsecond=0)
        self.fields=dict(title='Pilot review',agenda='Discuss framework\nQuestions and next steps',start=start.isoformat(),end=(start+timedelta(hours=1)).isoformat(),timezone='Africa/Accra',link='https://meet.google.com/abc-defg-hij',attendees='one@example.org, partner@external.org')
        self.config=dict(host='smtp.test',port=587,username='admin@example.org',password='fake-test-password',from_address='admin@example.org',app_url='https://example.org')

    def create(self,fields=None):
        return self.store.save_meeting(fields or self.fields,sha256(self.content).hexdigest(),'admin@example.org')

    def test_atomic_creation_preserves_workbook_and_allows_external_invitees(self):
        self.assertEqual([],self.store.snapshot()['meetings'])
        self.assertEqual([],self.store.snapshot()['invitations'])
        self.service.files.return_value.update.assert_not_called()
        created=self.create({**self.fields,'agenda':'=literal notes'})
        self.assertEqual(2,len(created['event_ids']))
        snapshot=self.store.snapshot()
        self.assertEqual(['one@example.org','partner@external.org'],snapshot['meetings'][0]['attendees'])
        self.assertNotIn('partner@external.org',self.people)
        book=load_workbook(BytesIO(self.content))
        self.assertEqual('=1+1',book['Gantt']['A1'].value)
        self.assertEqual(2,book['Activity Register'].max_row)
        self.assertEqual('s',book[MEETINGS]['C2'].data_type)
        book.close()

    def test_read_only_snapshot_does_not_request_legacy_etag(self):
        original=self.store.read
        self.store.read=lambda require_etag=True: (_ for _ in ()).throw(RuntimeError('legacy ETag unavailable')) if require_etag else (self.content,None)
        snapshot=self.store.snapshot()
        self.assertEqual([],snapshot['meetings'])
        self.assertEqual([],snapshot['invitations'])
        self.store.read=original

    def test_collaborators_only_see_their_meetings_and_cannot_manage(self):
        created=self.create();mid=created['meeting']['id']
        self.person['email']='one@example.org'
        self.assertEqual(1,len(self.store.snapshot()['meetings']))
        self.assertEqual([],self.store.snapshot()['invitations'])
        for action in [lambda:self.create(),lambda:self.store.save_meeting({},sha256(self.content).hexdigest(),'admin@example.org',mid,cancel=True),lambda:self.store.claim_invitation(created['event_ids'][0],'admin@example.org'),lambda:send_invitations(self.store,self.config,mid)]:
            with self.assertRaises(PermissionError):action()
        self.person['email']='two@example.org'
        self.assertEqual([],self.store.snapshot()['meetings'])
        self.person['email_verified']=False
        with self.assertRaises(PermissionError):self.store.snapshot()

    def test_stale_version_and_missing_etag_prevent_writes(self):
        stale=sha256(self.content).hexdigest();created=self.create()
        with self.assertRaisesRegex(ValueError,'workbook changed'):
            self.store.save_meeting({**self.fields,'title':'Changed'},stale,'admin@example.org',created['meeting']['id'])
        self.store.read.side_effect=lambda require_etag=True:(self.content,None)
        with self.assertRaisesRegex(ValueError,'version tag'):self.create({**self.fields,'title':'Second meeting'})
        self.assertEqual(1,len(self.store.snapshot()['meetings']))

    def test_rescheduling_cancels_removed_guest_and_keeps_stable_calendar_identity(self):
        created=self.create();meeting=created['meeting'];mid=meeting['id']
        self.store.save_meeting({**self.fields,'attendees':'one@example.org','title':'New title'},sha256(self.content).hexdigest(),'admin@example.org',mid)
        updated=self.store.save_meeting({**self.fields,'attendees':'one@example.org','title':'Final title'},sha256(self.content).hexdigest(),'admin@example.org',mid)
        self.assertEqual(mid,updated['meeting']['id']);self.assertEqual(2,updated['meeting']['sequence'])
        pending=[e for e in self.store.snapshot()['invitations'] if e['state']=='queued']
        self.assertEqual({('one@example.org','REQUEST'),('partner@external.org','CANCEL')},{(e['recipient'],e['method']) for e in pending})
        self.assertIsNone(self.store.claim_invitation(created['event_ids'][0],'admin@example.org'))
        removed=next(e for e in pending if e['method']=='CANCEL')
        self.assertEqual('CANCEL',self.store.claim_invitation(removed['event_id'],'admin@example.org')['method'])

    def test_cancellation_queues_notices_and_does_not_send(self):
        meeting=self.create()['meeting']
        with patch('meeting_store.smtplib.SMTP') as smtp:
            result=self.store.save_meeting({},sha256(self.content).hexdigest(),'admin@example.org',meeting['id'],cancel=True)
            smtp.assert_not_called()
        self.assertEqual('Cancelled',result['meeting']['status'])
        events=[e for e in self.store.snapshot()['invitations'] if e['state']=='queued']
        self.assertEqual(['CANCEL','CANCEL'],[e['method'] for e in events])
        claimed=self.store.claim_invitation(events[0]['event_id'],'admin@example.org')
        self.assertIn(b'METHOD:CANCEL',meeting_calendar(claimed['meeting'],claimed['recipient'],'CANCEL'))

    def test_delivery_claims_once_and_never_retries_uncertain_mail(self):
        meeting=self.create()['meeting']
        with patch('meeting_store.smtplib.SMTP') as smtp:
            result=send_invitations(self.store,self.config,meeting['id'])
            self.assertEqual(2,result['sent'])
            self.assertEqual(2,smtp.return_value.__enter__.return_value.send_message.call_count)
            send_invitations(self.store,self.config,meeting['id'])
            self.assertEqual(2,smtp.return_value.__enter__.return_value.send_message.call_count)
        new=self.create({**self.fields,'title':'Another meeting'})['meeting']
        with patch('meeting_store.smtplib.SMTP') as smtp:
            smtp.return_value.__enter__.return_value.send_message.side_effect=TimeoutError('private SMTP response')
            result=send_invitations(self.store,self.config,new['id'])
            self.assertEqual(1,result['uncertain'])
            states=[e['state'] for e in self.store.snapshot()['invitations'] if e['meeting_id']==new['id']]
            self.assertEqual(['failed_or_uncertain','queued'],states)
            send_invitations(self.store,self.config,new['id'])
            self.assertEqual(2,smtp.return_value.__enter__.return_value.send_message.call_count)
            send_invitations(self.store,self.config,new['id'])
            self.assertEqual(2,smtp.return_value.__enter__.return_value.send_message.call_count)

    def test_stale_queue_and_wrong_sender_never_send(self):
        created=self.create();mid=created['meeting']['id']
        with patch('meeting_store.smtplib.SMTP') as smtp:
            with self.assertRaises(ValueError):send_invitations(self.store,{**self.config,'from_address':'different@example.org'},mid)
            smtp.assert_not_called()
        self.assertTrue(all(e['state']=='queued' for e in self.store.snapshot()['invitations']))
        created=self.create({**self.fields,'title':'Another meeting'});mid=created['meeting']['id']
        book=load_workbook(BytesIO(self.content));book[MEETINGS]['B3']='Direct sheet edit'
        out=BytesIO();book.save(out);book.close();self.content=out.getvalue()
        with patch('meeting_store.smtplib.SMTP') as smtp:
            self.assertEqual(2,send_invitations(self.store,self.config,mid)['skipped'])
            smtp.return_value.__enter__.return_value.send_message.assert_not_called()

    def test_authentication_failure_preserves_the_queue(self):
        created=self.create()
        with patch('meeting_store.smtplib.SMTP') as smtp:
            smtp.return_value.__enter__.return_value.login.side_effect=RuntimeError('fake auth failure')
            with self.assertRaises(RuntimeError):send_invitations(self.store,self.config,created['meeting']['id'])
            smtp.return_value.__enter__.return_value.send_message.assert_not_called()
        self.assertTrue(all(e['state']=='queued' for e in self.store.snapshot()['invitations']))

    def test_calendar_invitation_is_private_and_supports_updates(self):
        meeting=self.create()['meeting']
        message=invitation_message(meeting,'one@example.org','REQUEST',self.config)
        self.assertEqual('one@example.org',message['To']);self.assertIsNone(message['Cc'])
        self.assertNotIn('partner@external.org',message.as_string())
        part=next(p for p in message.walk() if p.get_content_type()=='text/calendar')
        self.assertEqual('REQUEST',part.get_param('method'))
        data=part.get_payload(decode=True)
        self.assertIn(b'METHOD:REQUEST',data);self.assertIn(b'SEQUENCE:0',data)
        self.assertIn(b'URL:https://meet.google.com/abc-defg-hij',data)
        self.assertIn(b'RSVP=TRUE',data)
        self.assertTrue(all(len(line)<=75 for line in meeting_calendar({**meeting,'title':'α'*150}).split(b'\r\n')))
        self.assertIn(('UID:'+meeting['id']+'@dare-arbo').encode(),meeting_calendar({**meeting,'sequence':1}))

    def test_timezones_dst_and_input_validation(self):
        self.assertEqual('2026-11-05T14:00:00+00:00',local_instant(date(2026,11,5),time(14),'Africa/Accra'))
        self.assertEqual('2026-11-05T19:00:00+00:00',local_instant(date(2026,11,5),time(14),'America/New_York'))
        for day,hour in [(date(2026,3,8),time(2,30)),(date(2026,11,1),time(1,30))]:
            with self.assertRaisesRegex(ValueError,'daylight-saving'):local_instant(day,hour,'America/New_York')
        for change in [dict(link='javascript:alert(1)'),dict(link='https://user:password@example.org'),dict(title='Bad\r\nBcc: x@example.org'),dict(attendees='bad-email'),dict(end=self.fields['start']),dict(timezone='not/a/zone')]:
            with self.assertRaises(ValueError):clean_fields({**self.fields,**change})

    def app(self):
        app=AppTest.from_string('''
from meeting_scheduler import show_meetings
import streamlit as st
show_meetings(lambda:{'drive':dict(client_id='fake',client_secret='fake',refresh_token='fake'),
'project_management':{'admin_emails':['admin@example.org']}},lambda:st.session_state['person'],
lambda:['admin@example.org','one@example.org','two@example.org'])
''',default_timeout=30)
        app.session_state['person']=self.person.copy()
        return app

    def test_admin_ui_saves_without_email_and_preserves_draft_until_reload(self):
        app=self.app()
        with patch('meeting_scheduler.store_for',return_value=self.store),patch('meeting_store.smtplib.SMTP') as smtp:
            app.run();self.assertFalse(app.exception)
            next(x for x in app.text_input if x.label=='Meeting title').set_value('Working draft')
            app.run()
            self.assertEqual('Working draft',next(x for x in app.text_input if x.label=='Meeting title').value)
            next(x for x in app.text_input if x.label=='Meeting link').set_value(self.fields['link'])
            next(x for x in app.multiselect if x.label=='Invite approved collaborators').set_value(['one@example.org'])
            next(x for x in app.button if x.label=='Save meeting').click().run()
            self.assertFalse(app.exception)
            self.assertEqual('Working draft',self.store.snapshot()['meetings'][0]['title'])
            self.assertTrue(any(x.label=='Send queued invitations' for x in app.button))
            smtp.assert_not_called()

    def test_collaborator_ui_has_no_scheduler_and_visitor_sees_nothing(self):
        self.create();self.person['email']='one@example.org';app=self.app()
        with patch('meeting_scheduler.store_for',return_value=self.store):
            app.run();self.assertFalse(app.exception)
            self.assertFalse(any(x.label=='Meeting title' for x in app.text_input))
            self.assertTrue(app.get('download_button'))
            self.assertFalse(any('partner@external.org' in x.value for x in app.text))
            self.person['email_verified']=False;app.session_state['person']=self.person.copy();app.run()
            self.assertFalse(app.get('download_button'));self.assertFalse(app.button)

    def test_project_management_integrates_the_meetings_tab(self):
        app=AppTest.from_string('''
from project_management import show_project_management
import streamlit as st
show_project_management(lambda:{'drive':dict(client_id='fake',client_secret='fake',refresh_token='fake'),
'project_management':{'admin_emails':['admin@example.org']}},lambda:st.session_state['person'],
lambda:['admin@example.org','one@example.org','two@example.org'])
''',default_timeout=30)
        app.session_state['person']=self.person.copy()
        with patch('project_management.create_service'),patch('sheet_dashboard.create_service'),patch('sheet_dashboard.DriveStore.download',side_effect=lambda *args:('file.xlsx','',self.content)),patch('project_management.WorkbookTasks',return_value=self.store),patch('meeting_scheduler.store_for',return_value=self.store):
            app.run();self.assertFalse(app.exception)
            self.assertIn('Meetings',[tab.label for tab in app.tabs])
            self.assertTrue(any(x.label=='Save meeting' for x in app.button))
