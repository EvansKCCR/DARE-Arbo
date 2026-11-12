"""Research operations workspace backed by the private Supabase workbook."""
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from html import escape
import json
import logging
import mimetypes
from pathlib import Path
import re
import smtplib
import ssl
import streamlit as st
from access import has_full_access
from workspace_storage import SupabaseWorkspaceService
from supabase_storage import SupabaseStorage, SupabaseStorageError, configured as supabase_storage_configured
from workbook_tasks import WorkbookTasks, STATUSES, emails
from sheet_dashboard import show_sheet_dashboard, load_snapshot
from workspace_views import activity_key, detail, activity_table, format_date
from assignment_notifications import configured, process_notifications, verify_sender, sender_secret_status
from meeting_scheduler import show_meetings
from smtp_mailer import SMTPMailer

WORKSPACE_CSS = """<style>
.st-key-pm_workspace [data-testid="stHeading"] :is(h1,h2,h3){color:#24364B}
.st-key-pm_workspace [data-testid="stMetric"]{border:1px solid #E0E5EB;border-top:3px solid #477D83!important;border-radius:8px;padding:14px;background:#FFF}
.st-key-pm_workspace [data-testid="stMetricValue"]{font-size:1.8rem}
.st-key-pm_workspace [data-testid="stMetricLabel"]{font-size:.85rem;color:#526277}
.st-key-pm_workspace [data-baseweb="tab-list"]{gap:16px;border-bottom:1px solid #E0E5EB}
.st-key-pm_workspace [aria-selected="true"]{color:#286E77}
.st-key-pm_workspace [data-testid="stTable"] th{background:#F3F5F7;color:#24364B}
.st-key-pm_workspace [data-testid="stAlertContainer"]{background:#F1F5F7;color:#24364B;border-left-color:#477D83}
.st-key-pm_workspace .pm-gantt{font:14px/1.5 sans-serif;color:#24364B;margin:16px 0}
.st-key-pm_workspace .pm-gantt-axis,.st-key-pm_workspace .pm-gantt-row{display:grid;grid-template-columns:minmax(180px,34%) minmax(0,1fr);gap:18px;border-bottom:1px solid #E3E7EC;padding:12px 0;align-items:center}
.st-key-pm_workspace .pm-gantt-label{overflow-wrap:anywhere}
.st-key-pm_workspace .pm-gantt-label small{display:block;color:#526277;font-size:12px;margin-top:5px}
.st-key-pm_workspace .pm-ticks{height:25px;position:relative;font-size:12px}
.st-key-pm_workspace .pm-ticks span{position:absolute;white-space:nowrap;transform:translateX(-20%)}
.st-key-pm_workspace .pm-gantt-track{position:relative;height:32px;background:repeating-linear-gradient(to right,#F1F4F7 0,#F1F4F7 calc(12.5% - 1px),#DFE5EB calc(12.5% - 1px),#DFE5EB 12.5%);border-radius:4px}
.st-key-pm_workspace .pm-gantt-bar{position:absolute;top:7px;height:18px;border-radius:3px;min-width:2px}
.st-key-pm_workspace .pm-today{position:absolute;top:0;bottom:0;border-left:2px solid #24364B}
.st-key-pm_workspace .pm-calendar{width:100%;table-layout:fixed;border-collapse:collapse;font-size:13px;color:#24364B}
.st-key-pm_workspace .pm-calendar th{padding:10px 4px;background:#F3F5F7;border:1px solid #E0E5EB}
.st-key-pm_workspace .pm-calendar td{vertical-align:top;height:110px;padding:6px;border:1px solid #E0E5EB;overflow-wrap:anywhere}
.st-key-pm_workspace .pm-calendar time{display:block;font-weight:600;margin-bottom:6px}
.st-key-pm_workspace .pm-calendar-event{background:#F1F5F7;border-left:3px solid #367E87;padding:4px;margin:4px 0;line-height:1.4}
.st-key-pm_workspace .pm-calendar-event small{display:block;font-size:11px;color:#526277}
@media(max-width:650px){.st-key-pm_workspace .pm-gantt-row{grid-template-columns:1fr;gap:8px}.st-key-pm_workspace .pm-gantt-axis{grid-template-columns:1fr}.st-key-pm_workspace .pm-gantt-axis>div:first-child{display:none}}
</style>"""



def rerun_editor():
    try:
        st.rerun(scope='fragment')
    except st.errors.StreamlitAPIException:
        # Full-script execution (including AppTest) has no active fragment run.
        st.rerun()


def show_project_management(settings, identity, allowed):
    if not has_full_access(identity(), allowed()):
        st.title('Project Management')
        st.info('Sign in with an approved collaborator account to access project management.')
        return
    admins = settings().get('project_management', {}).get('admin_emails', ['evansasamoahadu@gmail.com'])
    admin = identity()['email'].strip().casefold() in {e.strip().casefold() for e in admins}
    with st.container(key='pm_workspace'):
        st.html(WORKSPACE_CSS)
        st.title('Project Management')
        st.write('Study coordination, milestones, deliverables and implementation progress')
        st.caption('Administrator workspace' if admin else 'Research collaborator workspace')
        st.caption('Collaborators sign in with an approved email code. Project documents and management data are stored in private Supabase storage.')
        # Native tabs switch without rerunning or unmounting an open editing form.
        tab_names = ['Overview', 'Activity Register', 'Study Timeline', 'Task Management' if admin else 'My Tasks', 'Meetings']
        if admin:
            tab_names.extend(['Email', 'Storage migration'])
        tabs = st.tabs(tab_names)
        for tab, view in zip(tabs[:3], ['Overview', 'Activity Register', 'Study Timeline']):
            with tab:
                show_sheet_dashboard(settings, identity, allowed, view)
        with tabs[3]:
            show_assignments(settings, identity, allowed)
        with tabs[4]:
            show_meetings(settings, identity, allowed)
        if admin:
            with tabs[5]:
                show_direct_email(settings, identity, allowed)
            with tabs[6]:
                show_storage_migration(settings)


def show_storage_migration(settings):
    st.subheader('Upload local project files to private Supabase Storage')
    config = settings()
    supabase = config.get('supabase_storage', {})
    destination_ready = supabase_storage_configured(supabase)
    if not destination_ready:
        st.info('Add the Supabase project URL, server-side Secret key and bucket name to Streamlit Secrets under [supabase_storage]. Keep the Secret key out of GitHub and chat.')
    if destination_ready:
        bucket = str(supabase.get('bucket'))
        st.caption(f'Destination bucket: {bucket} · uploaded files remain private. ZIPs may contain folders; supported formats include PDF, Office documents, CSV, TXT, HTML, JSON and images.')
    uploads = st.file_uploader(
        'Choose project files or a ZIP archive',
        type=['zip', 'pdf', 'docx', 'xlsx', 'pptx', 'csv', 'tsv', 'txt', 'png', 'jpg', 'jpeg', 'html', 'json'],
        accept_multiple_files=True, key='pm_supabase_local_uploads',
        disabled=not destination_ready,
        help='Maximum 50 MB per file; ZIP uploads and their uncompressed contents are limited to 250 MB.',
    )
    replace_existing = st.checkbox('Replace files in Supabase when the same path already exists', value=False,
                                   disabled=not destination_ready)
    progress_bar = st.progress(0.0, text='Ready to upload')
    status = st.empty()
    def update_progress(count, name):
        progress_bar.progress(min(count / max(len(uploads or []), 1), 0.95), text=f'Uploaded and verified {count} file(s) · {name}')
        status.caption('Each file is checked against its SHA-256 checksum. Keep a copy of the manifest for migration records.')
    if st.button('Upload and verify selected files', type='primary', disabled=not (destination_ready and uploads), key='pm_local_upload_to_supabase'):
        try:
            with st.spinner('Uploading files to private Supabase Storage and verifying checksums…'):
                manifest = SupabaseStorage(supabase).upload_local_files(
                    uploads, replace_existing=replace_existing, progress=update_progress)
        except (SupabaseStorageError, ValueError) as error:
            logging.getLogger(__name__).warning('Local project file upload to Supabase was rejected: %s', error)
            st.error(f'Upload stopped: {error}')
        except Exception:
            logging.getLogger(__name__).exception('Local project file upload to Supabase failed.')
            st.error('The upload did not finish. Review the app configuration and retry; existing source files on your computer are unchanged.')
        else:
            progress_bar.progress(1.0, text=f"Uploaded and checksum-verified {manifest['file_count']} files")
            st.success('Upload complete. Supabase contains a private, checksum-verified copy. Keep the source files until you confirm the document library and dashboard can open the migrated copies.')
            st.dataframe([{'File': item['path'], 'Bytes': item['size'], 'SHA-256': item['sha256']} for item in manifest['files']], hide_index=True, width='stretch')
            st.download_button('Download checksum manifest', json.dumps(manifest, indent=2),
                               file_name='dare_arbo_supabase_migration_manifest.json', mime='application/json')


def show_direct_email(settings, identity, allowed):
    """Administrator-only compose form using the configured project SMTP sender."""
    config = settings()
    admins = config.get('project_management', {}).get('admin_emails', ['evansasamoahadu@gmail.com'])
    if identity()['email'].strip().casefold() not in {str(email).strip().casefold() for email in admins}:
        st.info('Administrator access is required to send project email.')
        return

    email_config = config.get('email', {})
    st.subheader('Write and send project email')
    st.caption('Messages are sent from the configured project sender. Selected and additional recipients are BCC so their addresses remain private.')
    if not configured(email_config):
        st.warning('The project email sender is not configured. Set the existing [email] SMTP settings in Streamlit Secrets to enable sending.')

    directory = sorted({str(address).strip().casefold() for address in allowed() if str(address).strip()})
    with st.form('pm_direct_email_form', clear_on_submit=True):
        selected = st.multiselect('Collaborator email directory', directory,
                                  help='Choose approved collaborator addresses for this message.')
        additional = st.text_area('Additional partner email addresses', max_chars=5000,
                                  placeholder='partner@example.org, another@example.org',
                                  help='Optional. Separate addresses with commas, semicolons, or new lines.')
        subject = st.text_input('Subject', max_chars=250)
        body = st.text_area('Message', max_chars=20000, height=220)
        send = st.form_submit_button('Send email', type='primary', disabled=not configured(email_config))

    if not send:
        return

    recipients = list(dict.fromkeys(
        [str(address).strip().casefold() for address in selected]
        + [address.strip().casefold() for address in re.split(r'[,;\n]+', additional) if address.strip()]
    ))
    if not recipients:
        st.error('Choose at least one recipient from the directory or enter a partner email address.')
        return
    if len(recipients) > 25:
        st.error('Send to no more than 25 recipients at a time.')
        return
    email_pattern = re.compile(r'^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$')
    invalid = [address for address in recipients if not email_pattern.fullmatch(address)]
    if invalid:
        st.error('Check these email addresses: ' + ', '.join(invalid))
        return
    if not subject.strip() or any(char in subject for char in '\r\n'):
        st.error('Enter a subject on one line.')
        return
    if not body.strip():
        st.error('Enter a message before sending.')
        return

    message = EmailMessage()
    sender_address = parseaddr(str(email_config['from_address']))[1].strip()
    if not sender_address:
        st.error('The configured email.from_address is not a valid email address.')
        return
    message['From'] = formataddr(('DARE-Arbo Development Team', sender_address))
    message['To'] = sender_address
    message['Bcc'] = ', '.join(recipients)
    message['Subject'] = subject.strip()
    message.set_content(
        f"{body.strip()}\n\n—\nDARE-Arbo Development Team\n"
        "A collaborative project between Synergy NGS and GHID.\n\n"
        "DARE-Arbo: https://evanskccr.github.io/DARE-Arbo/\n"
        "GHID: https://kccr-ghana.org/research-impact/research-groups/global-health-infectious-diseases/\n"
        "SYNERGY-NGS 2025: https://www.linkedin.com/company/synergy-ngs-2025/?viewAsMember=true"
    )
    project_page = 'https://evanskccr.github.io/DARE-Arbo/'
    ghid_url = 'https://kccr-ghana.org/research-impact/research-groups/global-health-infectious-diseases/'
    synergy_url = 'https://www.linkedin.com/company/synergy-ngs-2025/?viewAsMember=true'
    html_body = f'''<!doctype html>
<html><body style="margin:0;padding:20px;color:#24364B;font-family:Arial,sans-serif;font-size:15px;line-height:1.55">
  <div>{escape(body.strip()).replace(chr(10), '<br>')}</div>
  <table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:24px;border-top:1px solid #D9E0E7;width:100%;max-width:620px">
    <tr><td colspan="2" style="padding:16px 0 10px;font-weight:700;color:#24364B">DARE-Arbo Development Team</td></tr>
    <tr><td colspan="2" style="padding:0 0 14px;color:#526277">A collaborative project between Synergy NGS and GHID.</td></tr>
    <tr>
      <td style="padding:0 18px 14px 0;vertical-align:middle">
        <a href="{ghid_url}" style="text-decoration:none"><img src="cid:ghid-logo" alt="GHID — Global Health and Infectious Diseases Research Group" width="88" style="display:block;width:88px;max-height:96px;object-fit:contain;border:0"></a>
      </td>
      <td style="padding:0 0 14px;vertical-align:middle">
        <a href="{synergy_url}" style="text-decoration:none"><img src="cid:synergy-logo" alt="SYNERGY-NGS 2025" width="104" style="display:block;width:104px;max-height:104px;object-fit:contain;border:0"></a>
      </td>
    </tr>
    <tr><td colspan="2" style="padding:0 0 6px"><a href="{project_page}" style="color:#176B73;font-weight:600">DARE-Arbo project page</a></td></tr>
    <tr><td style="padding:0 18px 4px 0"><a href="{ghid_url}" style="color:#176B73">GHID</a></td><td style="padding:0 0 4px"><a href="{synergy_url}" style="color:#176B73">SYNERGY-NGS 2025 on LinkedIn</a></td></tr>
  </table>
</body></html>'''
    message.add_alternative(html_body, subtype='html')
    html_part = message.get_payload()[-1]
    for filename, content_id in (('Logo.jpeg', 'ghid-logo'), ('Synergy_NGS2025.png', 'synergy-logo')):
        logo_path = Path(__file__).resolve().parent / filename
        if not logo_path.is_file():
            continue
        mime_type, _ = mimetypes.guess_type(logo_path.name)
        maintype, subtype = (mime_type or 'image/png').split('/', 1)
        html_part.add_related(logo_path.read_bytes(), maintype=maintype, subtype=subtype,
                              cid=content_id, filename=logo_path.name, disposition='inline')
    try:
        with st.spinner(f'Sending email to {len(recipients)} recipient(s)…'):
            SMTPMailer(email_config).send_message(message)
    except smtplib.SMTPAuthenticationError as error:
        logging.getLogger(__name__).exception('Project email SMTP authentication failed.')
        st.error(f'SMTP login was rejected (code {error.smtp_code}). Check email.username, email.password, and whether SMTP access is enabled for the sender mailbox.')
    except smtplib.SMTPRecipientsRefused as error:
        logging.getLogger(__name__).exception('Project email recipients were refused by SMTP.')
        st.error(f'The SMTP server refused {len(error.recipients)} recipient(s). Check the addresses or the sender’s relay permissions.')
    except PermissionError as error:
        logging.getLogger(__name__).exception('Project email sender validation failed.')
        st.error(f'Email sender validation failed: {type(error).__name__}. The From address must match email.from_address in Streamlit Secrets.')
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, OSError, ssl.SSLError):
        logging.getLogger(__name__).exception('Project email could not connect securely to SMTP.')
        st.error('The app could not establish a secure SMTP connection. Check email.smtp_host and email.smtp_port (usually port 587 with STARTTLS), and confirm the mail server accepts connections from Streamlit Cloud.')
    except smtplib.SMTPResponseException as error:
        logging.getLogger(__name__).exception('Project email was rejected by the SMTP server.')
        st.error(f'The SMTP server rejected the message (code {error.smtp_code}). Check the SMTP sender policy or message size, then retry.')
    except (KeyError, TypeError, ValueError) as error:
        logging.getLogger(__name__).exception('Project email configuration or sender validation failed.')
        st.error(f'Email configuration or sender validation failed: {type(error).__name__}. Check [email].from_address, username, host, port, app_url, and password in Streamlit Secrets.')
    except Exception as error:
        logging.getLogger(__name__).exception('Project email delivery could not be confirmed.')
        st.error(f'Email delivery could not be confirmed ({type(error).__name__}). Check the Streamlit app logs for the SMTP response before retrying to avoid sending duplicates.')
    else:
        st.success(f'Email accepted by the SMTP server for {len(recipients)} recipient(s).')


@st.fragment
def show_assignments(settings, identity, allowed):
    if not has_full_access(identity(), allowed()):
        st.info('Sign in again to access assigned activities.')
        return
    try:
        snapshot = load_snapshot(settings, identity, allowed, allow_expired=True)
        cfg = settings()
        store = WorkbookTasks(SupabaseWorkspaceService(cfg.get('supabase_storage', {})), identity, allowed,
            lambda: cfg.get('project_management', {}).get('admin_emails', ['evansasamoahadu@gmail.com']))
        admin = store.is_admin()
        tasks = [t for t in snapshot['tasks'] if admin or identity()['email'].strip().casefold() in emails(t['assignee'])]
        st.subheader('Task Management' if admin else 'My Assigned Activities')
        st.caption('Updates are saved to the Activity Register. Open forms stay unchanged during dashboard synchronization.')
        if st.session_state.pop('pm_task_saved', False):
            st.success('Activity update saved to the source workbook.')
        if st.session_state.get('pm_notification_message'):
            st.info(st.session_state.pop('pm_notification_message'))
        if not tasks:
            st.info('No activities are recorded yet.' if admin else 'No activities are assigned to your verified email. Ask the study coordinator to complete Assignee email in the register.')
        else:
            st.caption(f'{len(tasks)} recorded activities' if admin else f'{len(tasks)} activities assigned to your verified email')
            activity_table(tasks, datetime.now(timezone.utc).date(), height=min(280, 36*(len(tasks)+1)+3), key='pm_assigned_activities')
            query = st.text_input('Find a study activity' if admin else 'Find an assigned activity', key='_pm_task_search', placeholder='Search activity, owner or workstream', disabled=bool(st.session_state.get('pm_editor_open')))
            shown = [t for t in tasks if query.casefold() in ' '.join(str(t.get(k,'')) for k in ('activity','id','owner','workstream')).casefold()]
            options = {activity_key(t): t for t in shown}
            if options:
                chosen = st.selectbox('Activity to update', list(options), format_func=lambda k: f"{options[k]['id']} · {options[k]['activity']}", key='_pm_task_record', disabled=bool(st.session_state.get('pm_editor_open')))
                record = options[chosen]
                if st.session_state.get('pm_editor_open') == chosen:
                    draft = st.session_state['pm_drafts'][chosen]
                    task_form(store, allowed(), draft, admin, cfg.get('email',{}))
                else:
                    detail(record, datetime.now(timezone.utc).date())
                    if st.button('Open activity editor', type='primary'):
                        drafts = st.session_state.setdefault('pm_drafts', {})
                        if chosen not in drafts:
                            original = {**store.adapt(record, snapshot['version']), 'progress': record['progress'], 'record_key': chosen}
                            drafts[chosen] = {**original, '_baseline': original.copy()}
                        st.session_state['pm_editor_open'] = chosen
                        rerun_editor()
            else:
                st.info('No assigned activities match your search.')
        if admin:
            with st.expander('Assignment email status'):
                email=cfg.get('email',{})
                st.caption('Sender: '+str(email.get('from_address','Not configured')))
                secret_status=sender_secret_status(cfg)
                st.caption('Notification provider: '+secret_status['password'])
                if st.button('Verify email sender (no message sent)',disabled=not configured(email),key='pm_check_email'):
                    try:
                        if not store.is_admin():raise PermissionError('Administrator access required.')
                        with st.spinner('Checking the SMTP sender connection…'):verify_sender(email)
                    except Exception:
                        st.error('The SMTP sender could not be verified. Check the configured host, port, username, password and sender address in Streamlit Secrets.')
                    else:
                        st.success('SMTP sender verified. Assignment notifications can be sent from this deployment.')
                st.caption('Sent means accepted by the SMTP server. Claimed or uncertain deliveries are not automatically retried.')
                if not configured(cfg.get('email',{})):
                    st.info('Assignment notifications are queued in the workbook. Configure the email sender or background worker to deliver them.')
                    st.caption('Missing email settings: '+', '.join(k for k in ('from_address','app_url','password') if not email.get(k)))
                history = snapshot.get('notifications',[])
                if history:
                    st.dataframe([{'Activity ID':e['activity_id'],'Recipient':e['recipient'],'State':e['state'], 'Updated at':e['updated_at']} for e in history[-20:][::-1]],hide_index=True,width='stretch',key='pm_notification_history')
                else:
                    st.caption('No assignment notifications recorded yet.')
            with st.expander('Create a new study activity'):
                task_form(store, allowed(), None, True, cfg.get('email',{}))
        if not st.session_state.get('pm_editor_open'):
            if st.button('Reload assignments and discard saved drafts'):
                st.session_state.pop('pm_snapshot', None)
                st.session_state.pop('pm_drafts', None)
                st.session_state['pm_form_generation'] = st.session_state.get('pm_form_generation',0)+1
                rerun_editor()
        else:
            st.caption('To change activities, use Keep draft and choose another below the form. Reloading a draft deliberately discards its local edits.')
    except Exception:
        logging.getLogger(__name__).warning('Workbook activities could not be loaded.')
        st.error('Activities could not be loaded. Check collaborator access and the private Supabase workbook, then retry.')
        st.button('Retry loading activities')


def task_form(store, collaborators, task, admin, email_config=None):
    initial = task or {}
    record_key = initial.get('record_key', 'new')
    key = 'pm_form_' + str(record_key) + '_' + str(st.session_state.get('pm_form_generation',0)) + '_' + str(st.session_state.get('pm_form_generations', {}).get(record_key, 0))
    with st.form(key):
        fields = {}
        if admin:
            st.subheader('Activity information')
            fields['title'] = st.text_input('Activity title', initial.get('title',''), max_chars=300)
            fields['workstream'] = st.text_input('Workstream', initial.get('workstream',''))
            fields['description'] = st.text_area('Expected output / milestone', initial.get('description',''), max_chars=5000)
            st.subheader('Responsibility and assignment')
            left, right = st.columns(2)
            fields['owner'] = left.text_input('Responsible owner', initial.get('owner',''), help='Human-readable names or initials.')
            fields['assignee'] = right.text_input('Assignee email', initial.get('assignee',''), help='Exact approved collaborator email addresses, separated by commas. This controls editing permissions.')
            st.caption('Authorized assignees: ' + ', '.join(sorted(collaborators)))
            st.subheader('Schedule and milestones')
            left, right = st.columns(2)
            fields['start_date'] = left.date_input('Start date', date.fromisoformat(initial['start_date']) if initial.get('start_date') else None)
            fields['due_date'] = right.date_input('Deadline', date.fromisoformat(initial['due_date']) if initial.get('due_date') else None)
            for name in ('start_date','due_date'):
                fields[name] = fields[name].isoformat() if fields[name] else ''
        else:
            st.subheader(initial['title'])
            st.caption(initial['workstream'])
            st.text(initial['description'] or 'Expected output: Not recorded')
            st.caption(f"Schedule: {format_date(date.fromisoformat(initial['start_date']) if initial['start_date'] else None)} – {format_date(date.fromisoformat(initial['due_date']) if initial['due_date'] else None)}")
        st.subheader('Progress and reporting')
        statuses = STATUSES + ([initial['status']] if initial.get('status') and initial['status'] not in STATUSES else [])
        left, right = st.columns(2)
        fields['status'] = left.selectbox('Recorded status', statuses, index=statuses.index(initial.get('status','Not started')))
        fields['progress'] = right.number_input('Recorded progress (%)', min_value=0.0, max_value=100.0, value=float(initial['progress']) if initial.get('progress') is not None else None, step=5.0, help='Leave blank if no percentage has been recorded. Completed status is saved as 100%.')
        fields['note'] = st.text_area('Progress notes', initial.get('note',''), max_chars=3000)
        save = st.form_submit_button('Save activity' if not task else 'Save update', type='primary')
        keep = st.form_submit_button('Keep draft and choose another') if task else False
        discard = st.form_submit_button('Discard draft and reload activity') if task else False
    if keep:
        st.session_state['pm_drafts'][record_key] = {**initial, **fields}
        st.session_state.pop('pm_editor_open', None)
        rerun_editor()
    if discard:
        st.session_state.get('pm_drafts', {}).pop(record_key, None)
        st.session_state.pop('pm_editor_open', None)
        st.session_state.pop('pm_snapshot', None)
        generations = st.session_state.setdefault('pm_form_generations', {})
        generations[record_key] = generations.get(record_key,0)+1
        rerun_editor()
    if save:
        # Retain the original version and draft until a save actually succeeds.
        if task:
            st.session_state['pm_drafts'][record_key] = {**initial, **fields}
        baseline = initial.get('_baseline', initial)
        changed = {k:v for k,v in fields.items() if not task or v != baseline.get(k)}
        if task and not changed:
            st.info('No changes to save.')
            return
        # New or unchanged fields use the backend's existing validation rules.
        try:
            if not task and (not fields['start_date'] or not fields['due_date']):
                raise ValueError('Record both a start date and a deadline for a new activity.')
            with st.spinner('Saving activity to the workbook…'):
                result = store.save(changed, initial.get('id'), initial.get('version'))
        except (ValueError, PermissionError) as error:
            st.error(str(error))
            st.caption('Your draft is retained. If another workbook edit caused a conflict, copy your notes before deliberately discarding and reloading the activity.')
        except Exception:
            logging.getLogger(__name__).warning('Activity save failed.')
            st.error('The update could not be saved. Your draft is retained; check the connection before retrying.')
        else:
            events = result.get('notification_events',[]) if isinstance(result,dict) else []
            if events:
                st.session_state['pm_notification_message'] = f'{len(events)} assignment email notifications queued.'
                if configured(email_config or {}):
                    try:
                        with st.spinner('Sending assignment notifications…'):
                            delivered = process_notifications(store,email_config,send=True,event_ids=events)
                        st.session_state['pm_notification_message'] = f"Assignment email: {delivered['sent']} accepted by the mail server; {delivered['uncertain']} require delivery review. See Assignment email status for pending or skipped items."
                    except Exception:
                        st.session_state['pm_notification_message'] = 'Activity saved. Email delivery could not be confirmed; review Assignment email status before retrying.'
            st.session_state.pop('pm_snapshot', None)
            st.session_state.pop('pm_editor_open', None)
            st.session_state.get('pm_drafts', {}).pop(record_key,None)
            generations = st.session_state.setdefault('pm_form_generations', {})
            generations[record_key] = generations.get(record_key,0)+1
            st.session_state['pm_task_saved'] = True
            rerun_editor()
