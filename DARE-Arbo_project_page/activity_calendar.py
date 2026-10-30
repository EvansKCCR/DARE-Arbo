"""Workbook-derived activity calendar and standards-compatible .ics export."""
import calendar
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html import escape
import streamlit as st
from assignment_notifications import addresses
from workspace_views import complete, blocked, overdue, due_soon, activity_table


def schedule(task):
    start, end = task.get('start'), task.get('end')
    if start and end and end < start:
        return None
    return (start or end, end or start) if start or end else None


def month_activities(tasks, year, month):
    first = date(year,month,1)
    last = date(year,month,calendar.monthrange(year,month)[1])
    return [t for t in tasks if schedule(t) and schedule(t)[0] <= last and schedule(t)[1] >= first]


def ics_escape(value):
    return str(value or '').replace('\\','\\\\').replace('\r\n','\n').replace('\r','\n').replace('\n','\\n').replace(';','\\;').replace(',','\\,')


def fold_line(line):
    lines, current = [], ''
    for char in line:
        if len((current+char).encode('utf-8')) > 75:
            lines.append(current)
            current = ' '
        current += char
    return '\r\n'.join(lines+[current])


def calendar_bytes(tasks, now=None):
    now = now or datetime.now(timezone.utc)
    lines = ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//DARE-Arbo//Study activities//EN',
             'CALSCALE:GREGORIAN','METHOD:PUBLISH']
    for task in tasks:
        dates = schedule(task)
        if not dates:
            continue
        start, end = dates
        if end == date.max:
            continue
        key = str(task.get('record_key') or task.get('id'))
        uid = sha256(('DARE-Arbo:'+key).encode()).hexdigest() + '@dare-arbo'
        description = f"Workstream: {task.get('workstream','')}\nResponsible owner: {task.get('owner','')}\nRecorded start: {task.get('start') or 'Not recorded'}\nDeadline: {task.get('end') or 'Not recorded'}\nStatus: {task.get('status','')}\nExpected output: {task.get('output','')}\nProgress: {str(task.get('progress'))+'%' if task.get('progress') is not None else 'Not recorded'}"
        title = ('Deadline: ' if not task.get('start') else 'Start: ' if not task.get('end') else '') + task['activity']
        lines.extend(['BEGIN:VEVENT','UID:'+uid,'DTSTAMP:'+now.strftime('%Y%m%dT%H%M%SZ'),
                      'DTSTART;VALUE=DATE:'+start.strftime('%Y%m%d'),
                      'DTEND;VALUE=DATE:'+(end+timedelta(days=1)).strftime('%Y%m%d'),
                      'SUMMARY:'+ics_escape(title), 'DESCRIPTION:'+ics_escape(description),
                      'STATUS:CONFIRMED','END:VEVENT'])
    lines.append('END:VCALENDAR')
    return ('\r\n'.join(fold_line(line) for line in lines)+'\r\n').encode('utf-8')


def calendar_html(tasks, year, month, today):
    rows=[]
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year,month):
        days=[]
        for day in week:
            items=[t for t in tasks if schedule(t) and schedule(t)[0] <= day <= schedule(t)[1]]
            chips=[]
            for task in items[:3]:
                state = 'Completed' if complete(task) else 'Blocked' if blocked(task) else 'Overdue' if overdue(task,today) else 'Due soon' if due_soon(task,today) else task['status']
                color = '#35785E' if complete(task) else '#A34B55' if blocked(task) or overdue(task,today) else '#A46A18' if due_soon(task,today) else '#367E87'
                kind = 'Start & deadline' if task['start']==day==task['end'] else 'Deadline' if task['end']==day else 'Start' if task['start']==day else 'Scheduled'
                title = f"{task['activity']} · {kind} · {state} · {task['owner']}"
                chips.append(f'<div class="pm-calendar-event" style="border-left-color:{color}" title="{escape(title,quote=True)}">{escape(task["activity"])}<small>{escape(kind+" · "+state)}</small></div>')
            extra=f'<small>+{len(items)-3} activities · see agenda</small>' if len(items)>3 else ''
            style='background:#EDF5F5;' if day==today else 'opacity:.5;' if day.month!=month else ''
            days.append(f'<td style="{style}"><time datetime="{day.isoformat()}">{day.day}</time>'+''.join(chips)+extra+'</td>')
        rows.append('<tr>'+''.join(days)+'</tr>')
    headings=''.join('<th scope="col">'+name+'</th>' for name in ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'])
    return f'<table class="pm-calendar" aria-label="Activity calendar for {calendar.month_name[month]} {year}"><thead><tr>{headings}</tr></thead><tbody>'+''.join(rows)+'</tbody></table>'


def show_activity_calendar(tasks, today, viewer=None):
    st.subheader('Activity Tracker Calendar')
    st.caption('Scheduled activity spans, start dates and deadlines. Calendar entries appear when assignments or dates are saved to the workbook.')
    left,right=st.columns(2)
    month=left.selectbox('Calendar month',range(1,13),index=today.month-1,format_func=lambda m:calendar.month_name[m],key='pm_calendar_month')
    year=int(right.number_input('Calendar year',min_value=1900,max_value=9998,value=today.year,step=1,key='pm_calendar_year'))
    mine=st.checkbox('Only activities assigned to me',key='pm_calendar_mine')
    selected=[t for t in tasks if not mine or viewer and viewer.strip().casefold() in addresses(t.get('assignee',''))]
    visible=month_activities(selected,year,month)
    mode=st.radio('Calendar display',['Month grid','Agenda'],horizontal=True,key='pm_calendar_mode')
    if mode=='Month grid':
        st.html(calendar_html(visible,year,month,today))
        st.caption('Today is shaded. Up to three entries appear per day; the agenda below includes every activity in the month.')
    if not visible:
        st.info('No scheduled activities match this month and assignment filter.')
    else:
        st.write('**Monthly activity agenda**')
        activity_table(sorted(visible,key=lambda t:schedule(t)[0]),today,key='pm_calendar_agenda')
        st.download_button('Download activity calendar (.ics)',calendar_bytes(visible),file_name=f'DARE-Arbo_{year}-{month:02d}.ics',mime='text/calendar',key='pm_calendar_export')
        st.caption('Import the file into Google Calendar or Outlook. This is a calendar snapshot; download it again after schedule changes.')
    missing=sum(not schedule(t) for t in selected)
    if missing:
        st.caption(f'{missing} activities have no usable schedule and remain available in the Activity Register.')
