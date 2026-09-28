"""Run daily on a persistent scheduler. Dry-run by default; --send sends email."""
import argparse
import os
import smtplib
import ssl
from datetime import date, timedelta
from email.message import EmailMessage
from task_store import Database


def eligible(task, approved, today):
    return (task['assignee'] in approved and task['status'] != 'Completed'
            and task['start_date'] <= today.isoformat()
            and task['due_date'] <= (today + timedelta(days=3)).isoformat())


def run(send=False):
    db = Database(os.environ['TASK_DATABASE_URL'])
    approved = {e.strip().casefold() for e in os.environ['TASK_ALLOWED_EMAILS'].split(',') if e.strip()}
    today = date.today()
    db.initialize()
    with db.connect() as c:
        tasks = [dict(t) for t in c.execute('SELECT * FROM project_tasks').fetchall()]
    count = 0
    for task in tasks:
        if not eligible(task, approved, today):
            continue
        if not send:
            count += 1
            continue
        # Commit a unique claim before sending: overlapping jobs cannot duplicate it.
        with db.connect() as c:
            claimed = db.execute(c, 'INSERT INTO task_reminders(task_id,reminder_day,state) VALUES(?,?,?) ON CONFLICT(task_id,reminder_day) DO NOTHING', (task['id'], today.isoformat(), 'claimed')).rowcount
        if not claimed:
            continue
        try:
            message = EmailMessage()
            message['From'] = os.environ['SMTP_FROM']
            message['To'] = task['assignee']
            message['Subject'] = 'DARE-Arbo task reminder'
            message.set_content(f"Task: {task['title']}\nDeadline: {task['due_date']}\nStatus: {task['status']}\nProgress: {task['progress']}%\n\nPlease sign in to Project management to update your task.\n{os.environ['TASK_APP_URL']}\n")
            with smtplib.SMTP(os.environ['SMTP_HOST'], int(os.environ.get('SMTP_PORT', '587')), timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(os.environ['SMTP_USERNAME'], os.environ['SMTP_PASSWORD'])
                smtp.send_message(message)
            state = 'sent'
            count += 1
        except Exception:
            # An uncertain SMTP response might mean delivery succeeded. Do not auto-retry.
            state = 'failed_or_uncertain'
        with db.connect() as c:
            db.execute(c, 'UPDATE task_reminders SET state=? WHERE task_id=? AND reminder_day=?', (state, task['id'], today.isoformat()))
        if state != 'sent':
            raise RuntimeError('Reminder delivery failed or is uncertain. Check the mail provider before retrying; the daily claim is retained.') from None
    print(f'{count} reminders ' + ('sent.' if send else 'eligible (dry run; no email sent).'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true')
    run(parser.parse_args().send)
