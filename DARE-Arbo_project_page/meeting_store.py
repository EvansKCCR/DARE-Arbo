"""Meeting schedules and durable invitation delivery in the Supabase workbook."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4, UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from email.message import EmailMessage
from openpyxl import load_workbook
from workbook_tasks import WorkbookTasks
from assignment_notifications import table, append_literals, configured
from smtp_mailer import SMTPMailer

MEETINGS = 'Meetings'
INVITATIONS = 'Meeting Invitations'
FIELDS = ['id','title','agenda','start','end','timezone','link','attendees','organizer','status','sequence','updated_at','updated_by']
HEADERS = ['Meeting ID','Title','Agenda','Start UTC','End UTC','Time zone','Meeting link','Invitee emails','Organizer email','Status','Sequence','Updated at','Updated by']
EVENT_FIELDS = ['event_id','meeting_id','recipient','method','snapshot','state','created_at','updated_at']
EVENT_HEADERS = ['Event ID','Meeting ID','Recipient','Method','Meeting snapshot','State','Created at','Updated at']
EDIT_FIELDS = {'title','agenda','start','end','timezone','link','attendees'}


def email_address(value):
    value=str(value).strip().casefold()
    if len(value)>254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}",value) or '..' in value:
        raise ValueError('Enter valid email addresses separated by commas, semicolons or new lines.')
    return value


def invitees(value):
    parts=re.split(r'[,;\n]',value) if isinstance(value,str) else value
    result=sorted({email_address(p) for p in parts if str(p).strip()})
    if not result or len(result)>50:
        raise ValueError('Choose between 1 and 50 invitee email addresses.')
    return result


def instant(value):
    try:
        dt=datetime.fromisoformat(str(value))
        if dt.tzinfo is None or dt.utcoffset() is None:raise ValueError()
        return dt.astimezone(timezone.utc)
    except (ValueError,TypeError,OverflowError):
        raise ValueError('Meeting start and end must include a time zone.') from None


def local_instant(day, clock, zone):
    """Reject nonexistent/ambiguous wall times instead of guessing at DST changes."""
    try:tz=ZoneInfo(zone)
    except (ZoneInfoNotFoundError,ValueError):raise ValueError('Choose a valid IANA time zone, for example Africa/Accra.') from None
    wall=datetime.combine(day,clock).replace(tzinfo=None)
    possibilities={}
    for fold in (0,1):
        utc=wall.replace(tzinfo=tz,fold=fold).astimezone(timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None)==wall:possibilities[utc]=True
    if len(possibilities)!=1:
        raise ValueError('This local time is skipped or repeated by a daylight-saving change. Choose another time, or enter the equivalent UTC time using the UTC zone.')
    return next(iter(possibilities)).isoformat()


def clean_fields(fields):
    if set(fields)!=EDIT_FIELDS:raise ValueError('Use the supported meeting fields.')
    clean={k:str(fields[k]).strip() for k in EDIT_FIELDS-{'attendees'}}
    if not clean['title'] or len(clean['title'])>200 or any(ord(c)<32 for c in clean['title']):
        raise ValueError('Enter a meeting title of up to 200 characters on one line.')
    if len(clean['agenda'])>5000 or any(ord(c)<32 and c not in '\n\t' for c in clean['agenda']):
        raise ValueError('The agenda must contain at most 5,000 characters of readable text.')
    link=clean['link']
    try:
        url=urlsplit(link)
        if url.scheme!='https' or not url.hostname or '.' not in url.hostname or url.username or url.password or len(link)>2000 or any(c.isspace() or ord(c)<32 for c in link):raise ValueError()
        _=url.port
    except ValueError:raise ValueError('Paste the full HTTPS meeting link.') from None
    try:ZoneInfo(clean['timezone'])
    except (ZoneInfoNotFoundError,ValueError):raise ValueError('Choose a valid IANA time zone, for example Africa/Accra.') from None
    start,end=instant(clean['start']),instant(clean['end'])
    if not timedelta(0)<end-start<=timedelta(days=7):raise ValueError('The meeting must end after it starts and last no longer than seven days.')
    clean.update(start=start.isoformat(),end=end.isoformat(),attendees=invitees(fields['attendees']))
    return clean


def read_meetings(book):
    if MEETINGS not in book.sheetnames:return []
    sheet=table(book,MEETINGS,HEADERS)
    result=[]
    for row in sheet.iter_rows(min_row=2,max_col=len(FIELDS),values_only=True):
        if not any(v is not None for v in row):continue
        item=dict(zip(FIELDS,row))
        clean=clean_fields({k:item[k] for k in EDIT_FIELDS})
        try:
            UUID(str(item['id']))
            if item['status'] not in ('Scheduled','Cancelled') or int(item['sequence'])!=item['sequence'] or item['sequence']<0:raise ValueError()
        except (ValueError,TypeError):raise ValueError('The Meetings tab has an invalid meeting ID, status or sequence.') from None
        result.append({**item,**clean,'sequence':int(item['sequence']),'organizer':email_address(item['organizer'])})
    if len({m['id'] for m in result})!=len(result):raise ValueError('Meeting IDs must be unique.')
    return result


def read_invitations(book):
    if INVITATIONS not in book.sheetnames:return []
    sheet=table(book,INVITATIONS,EVENT_HEADERS)
    return [dict(zip(EVENT_FIELDS,row)) for row in sheet.iter_rows(min_row=2,max_col=len(EVENT_FIELDS),values_only=True) if row[0]]


def meeting_calendar(meeting, recipient=None, method='PUBLISH', now=None):
    from activity_calendar import ics_escape,fold_line
    now=now or datetime.now(timezone.utc)
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//DARE-Arbo//Meetings//EN','CALSCALE:GREGORIAN','METHOD:'+method,
           'BEGIN:VEVENT','UID:'+str(meeting['id'])+'@dare-arbo','SEQUENCE:'+str(meeting['sequence']),
           'DTSTAMP:'+now.strftime('%Y%m%dT%H%M%SZ'),'DTSTART:'+instant(meeting['start']).strftime('%Y%m%dT%H%M%SZ'),
           'DTEND:'+instant(meeting['end']).strftime('%Y%m%dT%H%M%SZ'),'SUMMARY:'+ics_escape(meeting['title']),
           'DESCRIPTION:'+ics_escape(meeting['agenda']+'\nMeeting link: '+meeting['link']+'\nScheduled time zone: '+meeting['timezone']),
           'LOCATION:'+ics_escape(meeting['link']),'URL:'+meeting['link'],'ORGANIZER:mailto:'+email_address(meeting['organizer']),
           'STATUS:'+('CANCELLED' if method=='CANCEL' or meeting['status']=='Cancelled' else 'CONFIRMED')]
    if recipient:lines.append('ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:'+email_address(recipient))
    lines+=['END:VEVENT','END:VCALENDAR']
    return ('\r\n'.join(fold_line(line) for line in lines)+'\r\n').encode('utf-8')


def invitation_message(meeting,recipient,method,config):
    message=EmailMessage()
    message['From']=config['from_address'];message['To']=email_address(recipient)
    prefix='Meeting cancelled' if method=='CANCEL' else 'Meeting invitation / update'
    message['Subject']='DARE-Arbo: '+prefix+' — '+meeting['title']
    tz=ZoneInfo(meeting['timezone'])
    start=instant(meeting['start']).astimezone(tz);end=instant(meeting['end']).astimezone(tz)
    message.set_content(f"{prefix}: {meeting['title']}\nStart: {start:%d %b %Y %H:%M %z}\nEnd: {end:%d %b %Y %H:%M %z}\nTime zone: {meeting['timezone']}\nOrganizer: {meeting['organizer']}\n\nMeeting link: {meeting['link']}\n\nAgenda:\n{meeting['agenda'] or 'No agenda supplied.'}\n\nUse the calendar invitation to add or update this meeting in your calendar. This email does not grant access to the project workspace.\n")
    # One recipient per message and calendar; no other guest addresses are disclosed.
    message.add_alternative(meeting_calendar(meeting,recipient,method).decode('utf-8'),subtype='calendar',params={'method':method,'charset':'utf-8'})
    return message


class MeetingStore(WorkbookTasks):
    def require_admin(self):
        if not self.is_admin():raise PermissionError('Only administrators can schedule meetings or send invitations.')

    def snapshot(self):
        actor=self.actor();admin=self.is_admin();content,_=self.read(require_etag=False)
        book=load_workbook(BytesIO(content),read_only=True,data_only=True)
        try:
            meetings=read_meetings(book)
            return dict(meetings=[m for m in meetings if admin or actor in m['attendees']],
                        invitations=[{k:v for k,v in e.items() if k!='snapshot'} for e in read_invitations(book)] if admin else [],
                        version=sha256(content).hexdigest())
        finally:book.close()

    def save_meeting(self,fields,version,organizer,meeting_id=None,cancel=False,now=None):
        self.require_admin();now=now or datetime.now(timezone.utc)
        content,etag=self.read()
        if sha256(content).hexdigest()!=version:raise ValueError('The workbook changed. Reload meetings before saving; your changes have not been published.')
        book=load_workbook(BytesIO(content),read_only=True,data_only=True)
        try:meetings=read_meetings(book);history=read_invitations(book)
        finally:book.close()
        old=next((m for m in meetings if m['id']==meeting_id),None)
        if meeting_id and not old:raise ValueError('This meeting no longer exists. Reload meetings.')
        if old and old['status']=='Cancelled':raise ValueError('This meeting is cancelled. Schedule a new meeting instead.')
        if cancel and not old:raise ValueError('Choose an existing meeting to cancel.')
        clean={k:old[k] for k in EDIT_FIELDS} if cancel else clean_fields(fields)
        if not cancel and instant(clean['start'])<=now:raise ValueError('Schedule the meeting for a future date and time.')
        if old and not cancel and all(old[k]==clean[k] for k in EDIT_FIELDS):return dict(meeting=old,event_ids=[])
        meeting={**clean,'id':meeting_id or str(uuid4()),'organizer':old['organizer'] if old else email_address(organizer),
                 'status':'Cancelled' if cancel else 'Scheduled','sequence':old['sequence']+1 if old else 0,
                 'updated_at':now.isoformat(),'updated_by':self.actor()}
        previous=set(old['attendees'] if old else [])|{e['recipient'] for e in history if e['meeting_id']==meeting['id'] and e['method']=='REQUEST'}
        current=set() if cancel else set(meeting['attendees'])
        events=[(recipient,'REQUEST') for recipient in sorted(current)]+[(recipient,'CANCEL') for recipient in sorted(previous-current)]
        ids=[]
        def change(register,column,header_row):
            self.require_admin()
            sheet=table(register.parent,MEETINGS,HEADERS)
            row=next((r[0].row for r in sheet.iter_rows(min_row=2) if r[0].value==meeting['id']),sheet.max_row+1)
            for col,key in enumerate(FIELDS,1):
                value=', '.join(meeting[key]) if key=='attendees' else meeting[key]
                cell=sheet.cell(row,col,value)
                if isinstance(value,str):cell.data_type='s'
            queue=table(register.parent,INVITATIONS,EVENT_HEADERS)
            for cells in queue.iter_rows(min_row=2):
                if cells[1].value==meeting['id'] and cells[5].value=='queued':
                    cells[5].value='skipped';cells[7].value=now.isoformat()
            for recipient,method in events:
                event_id=str(uuid4());ids.append(event_id)
                append_literals(queue,[event_id,meeting['id'],recipient,method,json.dumps(meeting,ensure_ascii=False),'queued',now.isoformat(),now.isoformat()])
        self.write(content,etag,change)
        return dict(meeting=meeting,event_ids=ids)

    def claim_invitation(self,event_id,sender,now=None):
        self.require_admin();sender=email_address(sender);now=now or datetime.now(timezone.utc)
        content,etag=self.read();book=load_workbook(BytesIO(content),read_only=True,data_only=True)
        try:history=read_invitations(book);meetings=read_meetings(book)
        finally:book.close()
        matches=[e for e in history if e['event_id']==event_id]
        if len(matches)!=1:raise ValueError('Invitation event IDs must be unique.')
        event=matches[0]
        if event['state']!='queued':return None
        meeting=next((m for m in meetings if m['id']==event['meeting_id']),None)
        try:
            payload=json.loads(event['snapshot'])
            recipient=email_address(event['recipient'])
            valid=meeting is not None and payload==meeting and sender==meeting['organizer'] and event['method'] in ('REQUEST','CANCEL')
            valid=valid and (meeting['status']=='Scheduled' and recipient in meeting['attendees'] and instant(meeting['end'])>now if event['method']=='REQUEST' else meeting['status']=='Cancelled' or recipient not in meeting['attendees'])
        except (ValueError,TypeError,KeyError):valid=False
        def change(register,column,header_row):
            self.require_admin();sheet=register.parent[INVITATIONS]
            for cells in sheet.iter_rows(min_row=2):
                if cells[0].value==event_id:
                    cells[5].value='claimed' if valid else 'skipped';cells[7].value=now.isoformat()
        self.write(content,etag,change)
        return dict(meeting=meeting,recipient=recipient,method=event['method']) if valid else None

    def finish_invitation(self,event_id,state):
        self.require_admin()
        if state not in ('sent','failed_or_uncertain'):raise ValueError('Invalid delivery result.')
        content,etag=self.read()
        def change(register,column,header_row):
            self.require_admin()
            for cells in register.parent[INVITATIONS].iter_rows(min_row=2):
                if cells[0].value==event_id and cells[5].value=='claimed':
                    cells[5].value=state;cells[7].value=datetime.now(timezone.utc).isoformat()
        self.write(content,etag,change)


def send_invitations(store,config,meeting_id,event_ids=None):
    store.require_admin()
    if not configured(config):raise ValueError('Configure the SMTP sender address, app URL and password before sending invitations.')
    snapshot=store.snapshot()
    meeting=next((m for m in snapshot['meetings'] if m['id']==meeting_id),None)
    if not meeting or email_address(config['from_address'])!=meeting['organizer']:
        raise ValueError('Use the meeting’s original organizer email to send its invitations.')
    events=[e for e in snapshot['invitations'] if e['meeting_id']==meeting_id and e['state']=='queued' and (event_ids is None or e['event_id'] in event_ids)]
    result=dict(sent=0,skipped=0,uncertain=0)
    if not events:return result
    # Sender mismatch or SMTP login failures leave invitations queued.
    mailer=SMTPMailer(config)
    mailer.verify_mail_sender(config['from_address'])
    for event in events:
        claimed=store.claim_invitation(event['event_id'],config['from_address'])
        if not claimed:result['skipped']+=1;continue
        try:
            message=invitation_message(claimed['meeting'],claimed['recipient'],claimed['method'],config)
            store.require_admin();mailer.send_message(message)
        except Exception:state='failed_or_uncertain';result['uncertain']+=1
        else:state='sent';result['sent']+=1
        try:store.finish_invitation(event['event_id'],state)
        except Exception:result['uncertain']+=1
        if state=='failed_or_uncertain':break  # Preserve unattempted recipients in the queue.
    return result
