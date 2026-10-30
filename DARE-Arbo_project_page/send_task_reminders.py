"""Run daily on a persistent scheduler. Dry-run by default; --send sends email."""
import argparse
import os
import smtplib
import ssl
import time
from datetime import date, timedelta
from email.message import EmailMessage
from drive_store import create_service
from workbook_tasks import WorkbookTasks, emails


def eligible(task, approved, today):
    return (bool(emails(task['assignee']) & set(approved)) and task['status'] != 'Completed' and task.get('progress') != 100
            and bool(task['start_date']) and bool(task['due_date'])
            and task['start_date'] <= today.isoformat()
            and task['due_date'] <= (today + timedelta(days=3)).isoformat())


def run(send=False):
    approved = {e.strip().casefold() for e in os.environ['TASK_ALLOWED_EMAILS'].split(',') if e.strip()}
    today = date.today()
    owner = os.environ.get('TASK_ADMIN_EMAIL', 'evansasamoahadu@gmail.com').strip().casefold()
    if owner not in approved:
        raise ValueError('The reminder administrator must be in TASK_ALLOWED_EMAILS.')
    config = {key: os.environ['DRIVE_' + key.upper()] for key in ('client_id', 'client_secret', 'refresh_token')}
    # This trusted scheduler uses the owner's existing grant; no browser session is needed.
    identity = lambda: dict(email=owner, is_logged_in=True, email_verified=True,
                            iss='https://accounts.google.com', exp=time.time()+300)
    store = WorkbookTasks(create_service(config), identity, lambda: approved, lambda: [owner])
    tasks = store.list()
    count = 0
    for task in tasks:
        if not eligible(task, approved, today):
            continue
        if not send:
            count += 1
            continue
        # A conditional workbook update records the claim before SMTP delivery.
        claimed = store.claim_reminder(task['id'], today.isoformat())
        if not claimed:
            continue
        try:
            message = EmailMessage()
            message['From'] = os.environ['SMTP_FROM']
            message['To'] = ', '.join(sorted(emails(task['assignee']) & approved))
            message['Subject'] = 'DARE-Arbo task reminder'
            message.set_content(f"Task: {task['title']}\nDeadline: {task['due_date']}\nStatus: {task['status']}\nProgress: {task['progress']}%\n\nPlease sign in to Project management to update your task.\n{os.environ['TASK_APP_URL']}\n")
            with smtplib.SMTP(os.environ['SMTP_HOST'], int(os.environ.get('SMTP_PORT', '587')), timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(os.environ['SMTP_USERNAME'], os.environ['SMTP_PASSWORD'])
                smtp.send_message(message)
            count += 1
        except Exception:
            # The workbook claim is retained to avoid duplicate uncertain deliveries.
            raise RuntimeError('Reminder delivery failed or is uncertain. Check the mail provider before retrying; the daily claim is retained.') from None
    print(f'{count} reminders ' + ('sent.' if send else 'eligible (dry run; no email sent).'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true')
    try:
        run(parser.parse_args().send)
    except Exception as exc:
        raise SystemExit('Reminder run failed (' + type(exc).__name__ + '). Check configuration and provider logs; no credentials have been printed.') from None
