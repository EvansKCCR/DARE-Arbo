"""Administrator dashboard and private collaborator task updates."""
from datetime import date
import logging
import streamlit as st
from access import has_full_access
from task_store import Database, TaskStore, STATUSES, PRIORITIES


def show_project_management(settings, identity, allowed):
    st.title('Project management')
    if not has_full_access(identity(), allowed()):
        st.info('Sign in with an approved collaborator account to access your tasks.')
        return
    cfg = settings().get('project_management', {})
    admins = lambda: cfg.get('admin_emails', ['evansasamoahadu@gmail.com'])
    if not cfg.get('database_url'):
        st.info('Task management is awaiting database setup. The administrator can follow PROJECT_MANAGEMENT.md to connect persistent storage and reminders.')
        return
    db = Database(cfg['database_url'])
    store = TaskStore(db, identity, allowed, admins)
    try:
        db.initialize()
        admin = store.is_admin()
        tasks = store.list()
        st.caption('Administrator workspace · Manage all assignments and monitor progress.' if admin else 'My assignments · Update progress, status and notes on your own tasks.')
        if cfg['database_url'].startswith('sqlite:'):
            st.caption('Local database mode. Use PostgreSQL for durable Streamlit Cloud storage.')
        if st.session_state.pop('task_saved', False):
            st.success('Task saved.')
        today = date.today().isoformat()
        active = [t for t in tasks if t['status'] != 'Completed']
        for col, label, value in zip(st.columns(4), ['Total tasks', 'Completed', 'Overdue', 'Blocked'], [len(tasks), len(tasks)-len(active), sum(t['due_date'] < today for t in active), sum(t['status'] == 'Blocked' for t in tasks)]):
            col.metric(label, value)
        status = st.selectbox('Filter by status', ['All statuses'] + STATUSES)
        if admin:
            assignee = st.selectbox('Filter by collaborator', ['All collaborators'] + sorted(set(allowed())))
        else:
            assignee = 'All collaborators'
        shown = [t for t in tasks if (status == 'All statuses' or t['status'] == status) and (assignee == 'All collaborators' or t['assignee'] == assignee)]
        if shown:
            st.dataframe([{k: t[k] for k in ['title', 'assignee', 'priority', 'due_date', 'status', 'progress']} for t in shown], hide_index=True, width='stretch')
        else:
            st.info('No tasks match this view.')
        if admin:
            with st.expander('Create a task'):
                task_form(store, allowed(), None, True)
        st.subheader('Manage a task' if admin else 'Update my task')
        if shown:
            selected = st.selectbox('Task', [t['id'] for t in shown], format_func=lambda key: next(t['title'] + ' — ' + t['assignee'] for t in shown if t['id'] == key))
            task = next(t for t in shown if t['id'] == selected)
            st.caption(f"Last updated by {task['updated_by']} · {task['updated_at'][:16]} UTC")
            task_form(store, allowed(), task, admin)
        st.button('Refresh tasks')
        if admin:
            st.subheader('Email reminders')
            st.write('The reminder worker sends one email per active task per day when its deadline is within three days or overdue. Completed tasks and removed collaborators are excluded.')
            st.caption('Requires an external scheduler and SMTP configuration. Configure these using PROJECT_MANAGEMENT.md; opening this page does not send email.')
    except Exception:
        logging.getLogger(__name__).warning('Task dashboard could not access storage.')
        st.error('Task storage is unavailable. Ask the administrator to check the database connection and configuration.')


def task_form(store, collaborators, task, admin):
    initial = task or {}
    version_key = 'task_version_' + initial.get('id', 'new')
    displayed_version = st.session_state.get(version_key, initial.get('version'))
    with st.form('task_' + initial.get('id', 'new')):
        fields = {}
        if admin:
            fields['title'] = st.text_input('Title', initial.get('title', ''), max_chars=160)
            fields['description'] = st.text_area('Description', initial.get('description', ''), max_chars=5000)
            people = sorted({x.strip().casefold() for x in collaborators} | ({initial['assignee']} if task else set()))
            fields['assignee'] = st.selectbox('Assign to', people, index=people.index(initial['assignee']) if task else 0)
            fields['priority'] = st.selectbox('Priority', PRIORITIES, index=PRIORITIES.index(initial.get('priority', 'Normal')))
            fields['start_date'] = st.date_input('Start date', date.fromisoformat(initial['start_date']) if task else date.today()).isoformat()
            fields['due_date'] = st.date_input('Deadline', date.fromisoformat(initial['due_date']) if task else date.today()).isoformat()
        else:
            st.write(initial['title'])
            st.text(initial['description'])
            st.caption(f"Priority: {initial['priority']} · Start: {initial['start_date']} · Deadline: {initial['due_date']}")
        fields['status'] = st.selectbox('Status', STATUSES, index=STATUSES.index(initial.get('status', 'Not started')))
        fields['progress'] = st.slider('Progress (%)', 0, 100, initial.get('progress', 0), step=5)
        fields['note'] = st.text_area('Progress notes', initial.get('note', ''), max_chars=3000)
        submitted = st.form_submit_button('Save task', type='primary')
    if submitted:
        try:
            store.save(fields, initial.get('id'), displayed_version)
        except (ValueError, PermissionError) as error:
            st.error(str(error))
        else:
            st.session_state['task_saved'] = True
            st.rerun()
    else:
        st.session_state[version_key] = initial.get('version')
