"""Live, read-only study dashboard backed by the project Drive workbook."""
from datetime import date, datetime, timezone
from html import escape
from io import BytesIO
import logging
import streamlit as st
from openpyxl import load_workbook
from google.auth.exceptions import RefreshError
from access import has_full_access
from drive_store import DriveStore, create_service

SOURCE_ID = '1Pmf6ZYq3HyooclARiVtZFqYtI6f-sNQx'
SOURCE_URL = f'https://docs.google.com/spreadsheets/d/{SOURCE_ID}/edit#gid=1783506628'


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or '').strip().replace('Sept', 'Sep')
    for pattern in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%d-%b-%y', '%d %b %Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    raise ValueError('Unrecognized date')


def parse_register(content):
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        sheet = workbook['Activity Register'] if 'Activity Register' in workbook.sheetnames else None
        if sheet is None:
            raise ValueError('The workbook needs an Activity Register tab.')
        header = None
        tasks, issues = [], []
        for number, row in enumerate(sheet.iter_rows(values_only=True), 1):
            labels = [str(v or '').strip().casefold() for v in row]
            if header is None:
                if {'activity', 'start', 'end', 'owner', 'status'}.issubset(labels):
                    header = {label: index for index, label in enumerate(labels) if label}
                continue
            def get(key, default=''):
                index = header.get(key)
                return row[index] if index is not None and index < len(row) and row[index] is not None else default
            if not str(get('activity')).strip():
                continue
            try:
                start, end = parse_date(get('start')), parse_date(get('end'))
                if end < start:
                    raise ValueError('End before start')
            except ValueError:
                issues.append(f'Row {number}: missing or invalid start/end dates; timeline omitted.')
                start = end = None
            status = str(get('status', 'Not recorded')).strip()
            raw = get('progress (%)', get('progress', get('% complete', '')))
            progress = None
            if str(raw).strip():
                try:
                    progress = float(str(raw).replace('%', '').strip())
                    if isinstance(raw, (float, int)) and 0 < raw < 1:
                        progress *= 100
                    if not 0 <= progress <= 100:
                        raise ValueError()
                except ValueError:
                    issues.append(f'Row {number}: invalid progress percentage; using status only.')
                    progress = None
            if status.casefold() in ('completed', 'complete', 'done'):
                progress = 100
            elif progress is None and status.casefold() in ('not started', 'planned', 'planned / update'):
                progress = 0
            tasks.append(dict(id=str(get('id', number)), activity=str(get('activity')).strip(),
                              row=number, assignee=str(get('assignee email')).strip(), note=str(get('progress notes')),
                              reminder_day=str(get('last reminder date')), reminder_state=str(get('last reminder state')),
                              workstream=str(get('workstream', 'Other')).strip(),
                              output=str(get('key output / milestone')), owner=str(get('owner', 'Unassigned')),
                              status=status, start=start, end=end, progress=progress))
        if header is None:
            raise ValueError('Activity Register headers were not found.')
        return tasks, issues
    finally:
        workbook.close()


def elapsed(start, end, today):
    return max(0.0, min(1.0, (today - start).days / max(1, (end - start).days)))


def timeline(tasks, start, end, today):
    span = max(1, (end-start).days + 1)
    marker = max(0, min(100, (today-start).days / span * 100))
    rows = []
    for task in tasks:
        if not task['start']:
            continue
        left = (task['start']-start).days / span * 100
        width = ((task['end']-task['start']).days+1) / span * 100
        color = '#43866B' if task['progress'] == 100 else '#A63348' if task['end'] < today else '#347F8A'
        label = f"{task['activity']} · {task['start']:%d %b %Y} – {task['end']:%d %b %Y} · {task['status']}"
        rows.append(f'<div style="margin:14px 0"><div>{escape(task["activity"])}</div>'
                    f'<div style="height:22px;background:#EDF4F0;position:relative;border-radius:5px" title="{escape(label, quote=True)}">'
                    f'<div style="position:absolute;left:{left:.4f}%;width:{width:.4f}%;height:22px;background:{color};border-radius:5px"></div>'
                    f'<div style="position:absolute;left:{marker:.4f}%;height:22px;border-left:2px solid #111"></div></div></div>')
    st.html('<div role="img" aria-label="Study schedule; black line marks today">' + ''.join(rows) + '</div>')


@st.fragment(run_every='60s')
def show_sheet_dashboard(settings, identity, allowed):
    # Authorization is rechecked on every timed rerun. No shared data cache.
    if not has_full_access(identity(), allowed()):
        st.info('Sign in with an approved collaborator account to view the live study dashboard.')
        return
    st.subheader('Live study dashboard')
    st.caption('Refreshes every 60 seconds while this page is open. Save workbook edits in Google Drive to publish them here.')
    st.button('Refresh study dashboard', key='refresh_sheet_dashboard')
    config = settings().get('drive', {})
    if not all(config.get(key) for key in ('client_id', 'client_secret', 'refresh_token')):
        st.info('Connect the existing project Google Drive account in deployment secrets to enable the live workbook dashboard.')
        return
    try:
        store = DriveStore(create_service(config), identity, allowed)
        _, _, content = store.download(SOURCE_ID)
        tasks, issues = parse_register(content)
    except RefreshError:
        st.error('The Google Drive connection has expired or been revoked. The administrator needs to reconnect the folder-owner account and update the Drive refresh token in deployment secrets.')
        return
    except Exception:
        logging.getLogger(__name__).warning('Live study workbook could not be read.')
        st.error('The live workbook could not be loaded. Check the Drive connection, folder access and Activity Register tab. Retry with Refresh study dashboard.')
        return
    today = datetime.now(timezone.utc).date()  # Ghana time is UTC.
    st.caption(f'Last successful read: {datetime.now(timezone.utc):%d %b %Y %H:%M:%S} UTC · Activity Register')
    st.link_button('Open source workbook ↗', SOURCE_URL)
    for issue in issues:
        st.warning(issue)
    dated = [t for t in tasks if t['start']]
    if not tasks:
        st.info('No activities recorded yet.')
        return
    completed = sum(t['progress'] == 100 for t in tasks)
    overdue = sum(t['end'] is not None and t['end'] < today and t['progress'] != 100 for t in tasks)
    for col, label, value in zip(st.columns(3), ['Activities', 'Completed', 'Past deadline · not recorded complete'], [len(tasks), completed, overdue]):
        col.metric(label, value)
    st.progress(completed / len(tasks), text=f'Recorded completion · {completed} of {len(tasks)} activities completed')
    if dated:
        start, end = min(t['start'] for t in dated), max(t['end'] for t in dated)
        st.subheader('Entire study timeline')
        st.progress(elapsed(start, end, today), text=f'{start:%d %b %Y} – {end:%d %b %Y} · {elapsed(start, end, today):.0%} of scheduled time elapsed')
        st.caption('Timeline bars show calendar time, not work completed. Study dates come from the earliest start and latest end in the register.')
    workstream = st.selectbox('Workstream', ['All workstreams'] + sorted({t['workstream'] for t in tasks}))
    owner = st.selectbox('Assignment / owner', ['All owners'] + sorted({t['owner'] for t in tasks}))
    status = st.selectbox('Recorded status', ['All statuses'] + sorted({t['status'] for t in tasks}))
    shown = [t for t in tasks if (workstream == 'All workstreams' or t['workstream'] == workstream)
             and (owner == 'All owners' or t['owner'] == owner) and (status == 'All statuses' or t['status'] == status)]
    st.subheader('Activity schedule')
    if dated:
        st.caption(f'{start:%d %b %Y} → {end:%d %b %Y} · Black line: today · Green: completed · Red: past deadline · Teal: scheduled')
        timeline(shown, start, end, today)
    if not shown:
        st.info('No activities match these filters.')
    st.subheader('Activity and task details')
    for index, task in enumerate(shown):
        with st.expander(f'{task["id"]} · {task["activity"]}', expanded=len(shown) <= 3):
            st.write(f'**Workstream:** {task["workstream"]} · **Owner:** {task["owner"]}')
            st.write(f'**Status:** {task["status"]}')
            st.text(task['output'])
            if task['start']:
                st.progress(elapsed(task['start'], task['end'], today), text=f'Timeline · {task["start"]:%d %b %Y} – {task["end"]:%d %b %Y} · {elapsed(task["start"], task["end"], today):.0%} elapsed')
            if task['progress'] is not None:
                st.progress(task['progress']/100, text=f'Recorded task progress · {task["progress"]:g}%')
            else:
                st.caption('Work completion percentage not recorded. Add a Progress (%) column to the register for numeric progress.')
