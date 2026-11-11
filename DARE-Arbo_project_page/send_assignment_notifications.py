"""Poll workbook assignments. Read-only dry run by default; --send delivers queued mail."""
import argparse
import os
import time
from drive_store import GraphClient, refresh_access_token
from workbook_tasks import WorkbookTasks
from assignment_notifications import configured, process_notifications


def run(send=False):
    owner=os.environ.get('TASK_ADMIN_EMAIL','evansasamoahadu@gmail.com').strip().casefold()
    approved={e.strip().casefold() for e in os.environ['TASK_ALLOWED_EMAILS'].split(',') if e.strip()}
    if owner not in approved:
        raise ValueError('The administrator must be an approved collaborator.')
    microsoft={'client_id':os.environ['MS_CLIENT_ID'],'client_secret':os.environ.get('MS_CLIENT_SECRET',''),
        'refresh_token':os.environ['MS_REFRESH_TOKEN'],'drive_id':os.environ.get('MS_DRIVE_ID','5e3948114b4b5428')}
    email={'from_address':os.environ.get('MAIL_FROM',owner),'app_url':os.environ.get('TASK_APP_URL','')}
    if send and not configured(email):
        raise ValueError('Configure MAIL_FROM and TASK_APP_URL before enabling delivery.')
    actor=lambda:dict(email=owner,is_logged_in=True,tid='9188040d-6c67-4c5b-b112-36a304b66dad',iss='https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0',exp=time.time()+300)
    service=GraphClient(refresh_access_token(microsoft),microsoft)
    store=WorkbookTasks(service,actor,lambda:approved,lambda:[owner])
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
