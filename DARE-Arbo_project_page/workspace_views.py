"""Research operations views and workbook-derived summaries. No storage or I/O."""
from datetime import date, timedelta
from html import escape
import streamlit as st

NOT_RECORDED = 'Not recorded'


def complete(task):
    return task['status'].strip().casefold() in ('completed', 'complete', 'done') or task['progress'] == 100


def blocked(task):
    return task['status'].strip().casefold() == 'blocked'


def overdue(task, today):
    return bool(task['end'] and task['end'] < today and not complete(task))


def due_soon(task, today):
    return bool(task['end'] and today <= task['end'] <= today + timedelta(days=7) and not complete(task))


def deadline_indicator(task, today):
    if complete(task):
        return 'Completed'
    if not task['end']:
        return 'Deadline not recorded'
    if overdue(task, today):
        return 'Overdue'
    if due_soon(task, today):
        return 'Due within 7 days'
    return 'Scheduled'


def activity_key(task):
    # Workbook ID, not the position of a row in the filtered/sorted display.
    return task.get('record_key', task['id'])


def format_date(value):
    return value.strftime('%d %b %Y') if value else NOT_RECORDED


def summary(tasks, today):
    return dict(total=len(tasks), completed=sum(complete(t) for t in tasks),
                active=sum(t['status'].casefold() == 'in progress' and not complete(t) for t in tasks),
                overdue=sum(overdue(t, today) for t in tasks), due=sum(due_soon(t, today) for t in tasks))


def workstream_rows(tasks, today):
    result = []
    for name in sorted({t['workstream'] for t in tasks}):
        group = [t for t in tasks if t['workstream'] == name]
        known = [t['progress'] for t in group if t['progress'] is not None]
        result.append({'Workstream': name, 'Activities': len(group), 'Completed': sum(complete(t) for t in group),
                       'Overdue': sum(overdue(t, today) for t in group),
                       'Progress recorded': f'{len(known)} / {len(group)}',
                       'Mean recorded progress (%)': round(sum(known)/len(known), 1) if known else None})
    return result


def filter_activities(tasks, values, today):
    query = values.get('search', '').strip().casefold()
    return [t for t in tasks if
            (not query or query in ' '.join(str(t.get(k, '')) for k in ('id', 'activity', 'workstream', 'owner', 'output', 'status', 'note')).casefold())
            and all(values.get(k, 'All') in ('All', t[field]) for k, field in [('workstream', 'workstream'), ('owner', 'owner'), ('status', 'status')])
            and (values.get('deadline', 'All') == 'All'
                 or values['deadline'] == 'Overdue' and overdue(t, today)
                 or values['deadline'] == 'Due within 7 days' and due_soon(t, today)
                 or values['deadline'] == 'Blocked' and blocked(t)
                 or values['deadline'] == 'Missing dates' and (not t['start'] or not t['end']))]


def _remember_filter(scope, name):
    values = dict(st.session_state.get('pm_filters_' + scope, {}))
    values[name] = st.session_state['_pm_filter_' + scope + name]
    st.session_state['pm_filters_' + scope] = values


def _clear_filters(scope):
    st.session_state['pm_filters_' + scope] = {}
    for name in ('search', 'workstream', 'owner', 'status', 'deadline'):
        st.session_state.pop('_pm_filter_' + scope + name, None)


def filters(tasks, today, scope='register'):
    values = st.session_state.get('pm_filters_' + scope, {})
    key = '_pm_filter_' + scope + 'search'
    if key not in st.session_state:
        st.session_state[key] = values.get('search', '')
    st.text_input('Search activities', key=key, placeholder='Activity, output, owner or notes', on_change=_remember_filter, args=(scope, 'search'))
    cols = st.columns(2)
    selections = {}
    for i, (name, label, options) in enumerate([
        ('workstream', 'Workstream', sorted({t['workstream'] for t in tasks})),
        ('owner', 'Responsible owner', sorted({t['owner'] for t in tasks})),
        ('status', 'Recorded status', sorted({t['status'] for t in tasks})),
        ('deadline', 'Deadline / attention', ['Overdue', 'Due within 7 days', 'Blocked', 'Missing dates'])]):
        choices = ['All'] + options
        key = '_pm_filter_' + scope + name
        if st.session_state.get(key, values.get(name, 'All')) not in choices:
            st.session_state[key] = 'All'
        elif key not in st.session_state:
            st.session_state[key] = values.get(name, 'All')
        with cols[i % 2]:
            selections[name] = st.selectbox(label, choices, key=key, on_change=_remember_filter, args=(scope, name))
    selections['search'] = st.session_state['_pm_filter_' + scope + 'search']
    st.button('Clear filters', key='pm_clear_'+scope, on_click=_clear_filters, args=(scope,))
    shown = filter_activities(tasks, selections, today)
    st.caption(f'{len(shown)} of {len(tasks)} activities')
    return shown


def table_rows(tasks, today):
    return [{'ID': t['id'], 'Activity / task': t['activity'], 'Workstream': t['workstream'],
             'Responsible owner': t['owner'], 'Status': t['status'], 'Recorded progress (%)': t['progress'],
             'Start date': format_date(t['start']), 'Deadline': format_date(t['end']),
             'Deadline indicator': deadline_indicator(t, today)} for t in tasks]


def activity_table(tasks, today, height='auto', key=None):
    st.dataframe(table_rows(tasks, today), hide_index=True, width='stretch', height=height, key=key,
                 column_config={'Activity / task': st.column_config.TextColumn(width='large'),
                                'Recorded progress (%)': st.column_config.ProgressColumn(min_value=0, max_value=100, format='%.1f%%')})


def detail(task, today):
    with st.container(border=True):
        st.subheader(task['activity'])
        st.caption(f"Activity {task['id']} · {task['workstream']}")
        left, right = st.columns(2)
        left.text(f"Responsible owner: {task['owner']}")
        right.text(f"Recorded status: {task['status']}")
        left.text(f"Start: {format_date(task['start'])}")
        right.text(f"Deadline: {format_date(task['end'])}")
        st.caption('Deadline indicator: ' + deadline_indicator(task, today))
        st.write('**Expected output / milestone**')
        st.text(task.get('output') or NOT_RECORDED)
        if task['progress'] is None:
            st.caption('Recorded progress: Not recorded')
        else:
            st.progress(task['progress']/100, text=f"Recorded progress · {task['progress']:g}%")
        st.write('**Progress notes**')
        st.text(task.get('note') or NOT_RECORDED)


def overview(tasks, today):
    counts = summary(tasks, today)
    for col, label, name in zip(st.columns(5), ['Recorded activities', 'In progress', 'Completed', 'Overdue', 'Due within 7 days'], ['total', 'active', 'completed', 'overdue', 'due']):
        col.metric(label, counts[name])
    st.caption('Overdue: deadline has passed and completion is not recorded. Due soon: unfinished activities due today through the next seven days.')
    if not tasks:
        st.info('No study activities have been recorded.')
        return
    left, right = st.columns(2)
    with left:
        st.progress(counts['completed']/len(tasks), text=f"Activities completed · {counts['completed']}/{len(tasks)} ({counts['completed']/len(tasks):.0%})")
    dated = [t for t in tasks if t['start'] and t['end'] and t['end'] >= t['start']]
    with right:
        if dated:
            from sheet_dashboard import elapsed
            start, end = min(t['start'] for t in dated), max(t['end'] for t in dated)
            st.progress(elapsed(start, end, today), text=f'Scheduled calendar time elapsed · {elapsed(start, end, today):.0%}')
            st.caption(f'{format_date(start)} – {format_date(end)}')
        else:
            st.caption('Study schedule: Not recorded')
    st.subheader('Workstream Progress')
    st.dataframe(workstream_rows(tasks, today), hide_index=True, width='stretch', key='pm_workstream_summary')
    st.caption('Mean progress uses only explicit numeric values; coverage shows how many activities have a recorded percentage. It is not an overall study performance score.')
    st.subheader('Upcoming Milestones')
    upcoming = sorted([t for t in tasks if t['end'] and t['end'] >= today and not complete(t)], key=lambda t: t['end'])[:6]
    if upcoming:
        activity_table(upcoming, today, key='pm_upcoming_milestones')
        st.caption('Next six scheduled activities, ordered by deadline; outputs are taken from the register.')
    else:
        st.info('No upcoming deadlines are recorded.')
    st.subheader('Activities Requiring Attention')
    attention = sorted([t for t in tasks if blocked(t) or overdue(t, today)], key=lambda t: t['end'] or date.max)
    if attention:
        activity_table(attention, today, key='pm_attention_activities')
    else:
        st.info('No overdue or blocked activities are recorded.')


def register(tasks, today):
    st.subheader('Activity Register')
    shown = filters(tasks, today)
    if not shown:
        st.info('No activities match the current filters.')
        return
    activity_table(shown, today, key='pm_activity_register')
    st.caption('Select column headers to sort. Blank progress cells mean Not recorded.')
    options = {activity_key(t): t for t in shown}
    previous = st.session_state.get('pm_detail_record')
    keys = list(options)
    chosen = st.selectbox('Activity details', keys, index=keys.index(previous) if previous in keys else 0,
                          format_func=lambda key: f"{options[key]['id']} · {options[key]['activity']}", key='_pm_detail_record')
    st.session_state['pm_detail_record'] = chosen
    detail(options[chosen], today)


def timeline_html(tasks, start, end, today):
    span = max(1, (end-start).days+1)
    months = []
    tick = start.replace(day=1)
    while tick <= end:
        if tick >= start:
            months.append(tick)
        tick = date(tick.year + (tick.month == 12), tick.month % 12 + 1, 1)
    stride = max(1, (len(months)+6)//7)
    tick_dates = [start + timedelta(days=n) for n in range(0,span,max(7,7*((span+48)//49)))] if span <= 90 else months[::stride]
    date_format = '%d %b' if span <= 90 else '%b %y'
    ticks = ''.join(f'<span style="left:{(d-start).days/span*100:.3f}%">{d.strftime(date_format)}</span>' for d in tick_dates if (d-start).days/span < .94)
    marker = (today-start).days/span*100 if start <= today <= end else None
    rows = []
    for task in sorted(tasks, key=lambda t: (t['workstream'], t['start'])):
        state = 'Completed' if complete(task) else 'Blocked' if blocked(task) else 'Overdue' if overdue(task,today) else 'Due soon' if due_soon(task,today) else 'Active' if task['status'].casefold() == 'in progress' else 'Scheduled'
        color = {'Completed':'#35785E','Blocked':'#A34B55','Overdue':'#A34B55','Due soon':'#A46A18','Active':'#367E87','Scheduled':'#8997A8'}[state]
        left, width = (task['start']-start).days/span*100, ((task['end']-task['start']).days+1)/span*100
        tooltip = f"{task['activity']} | {task['owner']} | {format_date(task['start'])} – {format_date(task['end'])} | {task['status']} | Progress: {str(task['progress'])+'%' if task['progress'] is not None else NOT_RECORDED}"
        line = f'<i class="pm-today" style="left:{marker:.3f}%"></i>' if marker is not None else ''
        rows.append(f'<div class="pm-gantt-row"><div class="pm-gantt-label">{escape(task["activity"])}<small>{escape(task["workstream"])} · {escape(state)}<br>{format_date(task["start"])} – {format_date(task["end"])}</small></div><div class="pm-gantt-track" role="img" aria-label="{escape(tooltip,quote=True)}" title="{escape(tooltip,quote=True)}"><div class="pm-gantt-bar" style="left:{left:.3f}%;width:{width:.3f}%;background:{color}"></div>{line}</div></div>')
    return '<div class="pm-gantt"><div class="pm-gantt-axis"><div>Activity / workstream</div><div class="pm-ticks">'+ticks+'</div></div>'+''.join(rows)+'</div>'


def study_timeline(tasks, today, viewer=None):
    st.subheader('Study Timeline')
    view = st.radio('Schedule view', ['Gantt schedule','Activity calendar'],horizontal=True,key='pm_schedule_view')
    if view == 'Activity calendar':
        from activity_calendar import show_activity_calendar
        show_activity_calendar(filters(tasks,today,scope='calendar'),today,viewer)
        return
    st.caption(f'Today · {format_date(today)}. Bars represent scheduled duration; labels show workflow state. Completion percentages are reported separately in the Activity Register.')
    shown = filters(tasks, today, scope='timeline')
    dated = [t for t in tasks if t['start'] and t['end'] and t['end'] >= t['start']]
    visible = [t for t in shown if t['start'] and t['end'] and t['end'] >= t['start']]
    if not visible:
        st.info('No valid activity schedules match the current filters.')
        return
    st.caption('Green: completed · Teal: active · Amber: due soon · Red: overdue or blocked · Slate: scheduled. Hover for owner, dates and recorded progress.')
    st.html(timeline_html(visible, min(t['start'] for t in dated), max(t['end'] for t in dated), today))
    st.caption('Vertical line marks today when it falls within the study schedule. Activities without valid date pairs remain visible in the Activity Register.')
