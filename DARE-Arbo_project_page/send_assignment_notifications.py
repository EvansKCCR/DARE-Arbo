"""Poll workbook assignments. Read-only dry run by default; --send delivers queued mail."""
import argparse
import os
import time
from drive_store import create_service
from workbook_tasks import WorkbookTasks
from assignment_notifications import configured, process_notifications


def run(send=False):
    owner=os.environ.get('TASK_ADMIN_EMAIL','evansasamoahadu@gmail.com').strip().casefold()
    approved={e.strip().casefold() for e in os.environ['TASK_ALLOWED_EMAILS'].split(',') if e.strip()}
    if owner not in approved:
        raise ValueError('The administrator must be an approved collaborator.')
    drive={key:os.environ['DRIVE_'+key.upper()] for key in ('client_id','client_secret','refresh_token')}
    email={key:os.environ.get(env,'') for key,env in [('host','SMTP_HOST'),('port','SMTP_PORT'),('username','SMTP_USERNAME'),('password','SMTP_PASSWORD'),('from_address','SMTP_FROM'),('app_url','TASK_APP_URL')]}
    email['port']=email['port'] or 587
    if send and not configured(email):
        raise ValueError('Configure the SMTP sender before enabling delivery.')
    actor=lambda:dict(email=owner,is_logged_in=True,email_verified=True,iss='https://accounts.google.com',exp=time.time()+300)
    store=WorkbookTasks(create_service(drive),actor,lambda:approved,lambda:[owner])
    if send:
        store.sync_assignments()
    result=process_notifications(store,email,send=send)
    print(result)
    if result['uncertain']:
        raise RuntimeError('Delivery needs review. Claims are retained to prevent duplicate attempts.')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send',action='store_true')
    try:
        run(parser.parse_args().send)
    except Exception as exc:
        raise SystemExit('Assignment notification run failed ('+type(exc).__name__+'). Check configuration and delivery status; no credentials have been printed.') from None
