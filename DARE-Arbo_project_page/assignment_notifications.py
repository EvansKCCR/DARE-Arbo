"""Workbook assignment queue and SMTP delivery. No separate persistent store."""
from datetime import datetime, timezone
from uuid import uuid4
import re
import smtplib
import ssl
from collections import Counter
from email.message import EmailMessage

TRACKING = 'Assignment Tracking'
QUEUE = 'Assignment Notifications'
TRACK_HEADERS = ['Activity ID', 'Assignee emails', 'Updated at']
EVENT_HEADERS = ['Event ID', 'Activity ID', 'Recipient', 'Created at', 'State', 'Updated at']


def addresses(value):
    return {s.strip().casefold() for s in re.split(r'[,;\n]', value) if s.strip()}


def table(book, name, headers):
    if name not in book.sheetnames:
        sheet = book.create_sheet(name)
        sheet.append(headers)
    else:
        sheet = book[name]
        if [sheet.cell(1,i+1).value for i in range(len(headers))] != headers:
            raise ValueError(f'{name} headers do not match the notification schema.')
    return sheet


def append_literals(sheet, values):
    sheet.append(values)
    for cell in sheet[sheet.max_row]:
        if isinstance(cell.value,str):
            cell.data_type='s'


def initialize_tracking(book, tasks):
    """Baseline existing assignments without sending historical notifications."""
    if TRACKING in book.sheetnames:
        return
    sheet = table(book, TRACKING, TRACK_HEADERS)
    now = datetime.now(timezone.utc).isoformat()
    counts=Counter(t['id'] for t in tasks)
    for task in tasks:
        if task['id'] != 'Not recorded' and counts[task['id']]==1:
            append_literals(sheet,[task['id'], ', '.join(sorted(addresses(task['assignee']))), now])


def record_assignment(book, activity_id, assignee, previous_assignee=None):
    sheet = table(book, TRACKING, TRACK_HEADERS)
    matches = [row for row in sheet.iter_rows(min_row=2) if str(row[0].value) == str(activity_id)]
    if len(matches) > 1:
        raise ValueError('Duplicate activity IDs in assignment tracking. Resolve them before assigning tasks.')
    now = datetime.now(timezone.utc).isoformat()
    tracked = addresses(str(matches[0][1].value or '')) if matches else set()
    old = addresses(previous_assignee) if previous_assignee is not None else tracked
    new = addresses(assignee)
    if old == new and tracked == new and matches:
        return []
    if matches:
        sheet.cell(matches[0][0].row, 2, ', '.join(sorted(new)))
        sheet.cell(matches[0][0].row, 3, now)
    else:
        append_literals(sheet,[str(activity_id), ', '.join(sorted(new)), now])
    queue = table(book, QUEUE, EVENT_HEADERS)
    for row in queue.iter_rows(min_row=2):
        if str(row[1].value)==str(activity_id) and row[4].value=='queued' and str(row[2].value) not in new:
            row[4].value='skipped'
            row[5].value=now
    events = []
    for recipient in sorted(new-old):
        if any(str(row[1].value)==str(activity_id) and str(row[2].value)==recipient and row[4].value=='queued' for row in queue.iter_rows(min_row=2)):
            continue
        event_id = str(uuid4())
        append_literals(queue,[event_id, str(activity_id), recipient, now, 'queued', now])
        events.append(event_id)
    return events


def read_events(book):
    if QUEUE not in book.sheetnames:
        return []
    sheet = book[QUEUE]
    if [sheet.cell(1,i+1).value for i in range(len(EVENT_HEADERS))] != EVENT_HEADERS:
        raise ValueError('Assignment Notifications headers do not match the expected schema.')
    return [dict(zip(['event_id','activity_id','recipient','created_at','state','updated_at'],
                     [str(v or '') for v in row[:6]]))
            for row in sheet.iter_rows(min_row=2, max_col=6, values_only=True) if row[0]]


def configured(config):
    return all(config.get(k) for k in ('host','username','password','from_address','app_url'))


def assignment_message(task, recipient, config):
    from activity_calendar import calendar_bytes, schedule
    message = EmailMessage()
    message['From'] = config['from_address']
    message['To'] = recipient
    message['Subject'] = 'DARE-Arbo: a study activity has been assigned to you'
    start = task['start'].strftime('%d %b %Y') if task['start'] else 'Not recorded'
    end = task['end'].strftime('%d %b %Y') if task['end'] else 'Not recorded'
    message.set_content(f"Activity: {task['activity']}\nWorkstream: {task['workstream']}\n"
                        f"Responsible owner: {task['owner']}\nStart: {start}\nDeadline: {end}\n"
                        f"Status: {task['status']}\n\nExpected output / milestone:\n{task['output'] or 'Not recorded'}\n\n"
                        f"Sign in to Project Management → My Tasks to report progress.\n{config['app_url']}\n")
    if schedule(task):
        message.add_attachment(calendar_bytes([task]), maintype='text', subtype='calendar', filename='DARE-Arbo_activity.ics')
    return message


def process_notifications(store, config, send=False, event_ids=None):
    """SMTP acceptance is recorded as sent; uncertain attempts are never retried automatically."""
    if not store.is_admin():
        raise PermissionError('Administrator access is required for notification delivery.')
    events = [e for e in store.notification_events() if e['state'] == 'queued'
              and (event_ids is None or e['event_id'] in event_ids)]
    result = dict(queued=len(events), sent=0, skipped=0, uncertain=0)
    if not send or not configured(config):
        return result
    # Fail before claiming if SMTP configuration is malformed.
    port = int(config.get('port',587))
    for event in events:
        claimed = store.claim_assignment(event['event_id'])
        if not claimed:
            result['skipped'] += 1
            continue
        try:
            message = assignment_message(claimed['task'], claimed['recipient'], config)
            with smtplib.SMTP(config['host'], port, timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(config['username'], config['password'])
                smtp.send_message(message)
        except Exception:
            result['uncertain'] += 1
            state = 'failed_or_uncertain'
        else:
            result['sent'] += 1
            state = 'sent'
        try:
            store.finish_assignment(event['event_id'], state)
        except Exception:
            # The durable claim remains, so another worker cannot duplicate it.
            result['uncertain'] += 1
    return result
