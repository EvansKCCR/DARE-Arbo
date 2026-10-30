"""Research operations workspace backed exclusively by the Drive workbook."""
from datetime import date, datetime, timezone
import logging
import streamlit as st
from access import has_full_access
from drive_store import create_service
from workbook_tasks import WorkbookTasks, STATUSES, emails
from sheet_dashboard import show_sheet_dashboard, load_snapshot
from workspace_views import activity_key, detail, activity_table, format_date

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
        # Native tabs switch without rerunning or unmounting an open editing form.
        tabs = st.tabs(['Overview', 'Activity Register', 'Study Timeline', 'Task Management' if admin else 'My Tasks'])
        for tab, view in zip(tabs[:3], ['Overview', 'Activity Register', 'Study Timeline']):
            with tab:
                show_sheet_dashboard(settings, identity, allowed, view)
        with tabs[3]:
            show_assignments(settings, identity, allowed)


@st.fragment
def show_assignments(settings, identity, allowed):
    if not has_full_access(identity(), allowed()):
        st.info('Sign in again to access assigned activities.')
        return
    try:
        snapshot = load_snapshot(settings, identity, allowed, allow_expired=True)
        cfg = settings()
        store = WorkbookTasks(create_service(cfg['drive']), identity, allowed,
            lambda: cfg.get('project_management', {}).get('admin_emails', ['evansasamoahadu@gmail.com']))
        admin = store.is_admin()
        tasks = [t for t in snapshot['tasks'] if admin or identity()['email'].strip().casefold() in emails(t['assignee'])]
        st.subheader('Task Management' if admin else 'My Assigned Activities')
        st.caption('Updates are saved to the Activity Register. Open forms stay unchanged during dashboard synchronization.')
        if st.session_state.pop('pm_task_saved', False):
            st.success('Activity update saved to the source workbook.')
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
                    task_form(store, allowed(), draft, admin)
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
            with st.expander('Create a new study activity'):
                task_form(store, allowed(), None, True)
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
        st.error('Activities could not be loaded. Check the Drive connection, then retry.')
        st.button('Retry loading activities')


def task_form(store, collaborators, task, admin):
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
            fields['assignee'] = right.text_input('Assignee email', initial.get('assignee',''), help='Exact approved Google addresses, separated by commas. This controls editing permissions.')
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
            store.save(changed, initial.get('id'), initial.get('version'))
        except (ValueError, PermissionError) as error:
            st.error(str(error))
            st.caption('Your draft is retained. If another workbook edit caused a conflict, copy your notes before deliberately discarding and reloading the activity.')
        except Exception:
            logging.getLogger(__name__).warning('Activity save failed.')
            st.error('The update could not be saved. Your draft is retained; check the connection before retrying.')
        else:
            st.session_state.pop('pm_snapshot', None)
            st.session_state.pop('pm_editor_open', None)
            st.session_state.get('pm_drafts', {}).pop(record_key,None)
            generations = st.session_state.setdefault('pm_form_generations', {})
            generations[record_key] = generations.get(record_key,0)+1
            st.session_state['pm_task_saved'] = True
            rerun_editor()
