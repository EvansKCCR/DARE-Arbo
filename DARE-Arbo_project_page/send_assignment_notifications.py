"""Poll workbook assignments. Read-only dry run by default; --send delivers queued mail."""
import argparse
import os
from workbook_tasks import WorkbookTasks
from assignment_notifications import configured, process_notifications
from workspace_storage import SupabaseWorkspaceService


def run(send=False):
    owner=os.environ.get('TASK_ADMIN_EMAIL','evansasamoahadu@gmail.com').strip().casefold()
    approved={e.strip().casefold() for e in os.environ['TASK_ALLOWED_EMAILS'].split(',') if e.strip()}
    if owner not in approved:
        raise ValueError('The administrator must be an approved collaborator.')
    storage={'url':os.environ['SUPABASE_URL'],'secret_key':os.environ['SUPABASE_SECRET_KEY'],
        'bucket':os.environ.get('SUPABASE_BUCKET','dare-arbo-private')}
    email={'from_address':os.environ.get('MAIL_FROM',owner),'app_url':os.environ.get('TASK_APP_URL',''),
        'smtp_host':os.environ.get('SMTP_HOST','smtp.gmail.com'),'smtp_port':os.environ.get('SMTP_PORT','587'),
        'username':os.environ.get('SMTP_USERNAME',os.environ.get('MAIL_FROM',owner)),
        'password':os.environ.get('SMTP_PASSWORD','')}
    if send and not configured(email):
        raise ValueError('Configure MAIL_FROM and TASK_APP_URL before enabling delivery.')
    actor=lambda:dict(email=owner,is_logged_in=True)
    service=SupabaseWorkspaceService(storage)
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
