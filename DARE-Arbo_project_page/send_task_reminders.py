"""Run daily on a persistent scheduler. Dry-run by default; --send sends email."""
import argparse
import os
from datetime import date, timedelta
from email.message import EmailMessage
from workbook_tasks import WorkbookTasks, emails
from workspace_storage import SupabaseWorkspaceService
from smtp_mailer import SMTPMailer


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
    config = {'url':os.environ['SUPABASE_URL'],'secret_key':os.environ['SUPABASE_SECRET_KEY'],
              'bucket':os.environ.get('SUPABASE_BUCKET','dare-arbo-private')}
    identity = lambda: dict(email=owner, is_logged_in=True)
    service = SupabaseWorkspaceService(config)
    store = WorkbookTasks(service, identity, lambda: approved, lambda: [owner])
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
            message['From'] = os.environ.get('MAIL_FROM', owner)
            message['To'] = ', '.join(sorted(emails(task['assignee']) & approved))
            message['Subject'] = 'DARE-Arbo task reminder'
            message.set_content(f"Task: {task['title']}\nDeadline: {task['due_date']}\nStatus: {task['status']}\nProgress: {task['progress']}%\n\nPlease sign in to Project management to update your task.\n{os.environ['TASK_APP_URL']}\n")
            email_config={'from_address':os.environ.get('MAIL_FROM',owner),'app_url':os.environ['TASK_APP_URL'],
                'smtp_host':os.environ.get('SMTP_HOST','smtp.gmail.com'),'smtp_port':os.environ.get('SMTP_PORT','587'),
                'username':os.environ.get('SMTP_USERNAME',os.environ.get('MAIL_FROM',owner)),
                'password':os.environ['SMTP_PASSWORD']}
            SMTPMailer(email_config).send_message(message)
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
