"""Private meeting calendar and an administrator-only scheduler."""
import calendar
from datetime import datetime, time, timedelta, timezone
from html import escape
import logging
import time as clock
from zoneinfo import ZoneInfo, available_timezones
import streamlit as st
from access import has_full_access
from workspace_storage import SupabaseWorkspaceService
from sheet_dashboard import snapshot_scope, invalidate_snapshot
from assignment_notifications import configured
from meeting_store import MeetingStore, instant, local_instant, meeting_calendar, send_invitations

DEFAULT_ADMINS=['evansasamoahadu@gmail.com']


def store_for(settings,identity,allowed):
    return MeetingStore(SupabaseWorkspaceService(settings().get('supabase_storage', {})),identity,allowed,
                        lambda:settings().get('project_management',{}).get('admin_emails',DEFAULT_ADMINS))


def load_meetings(settings,identity,allowed,force=False):
    if not has_full_access(identity(),allowed()):raise PermissionError('Sign in with an approved collaborator account.')
    scope=snapshot_scope(settings,identity,allowed)
    cached=st.session_state.get('pm_meeting_cache')
    if cached and cached['scope']==scope and not force and clock.monotonic()-cached['loaded']<60:return cached
    result={**store_for(settings,identity,allowed).snapshot(),'scope':scope,'loaded':clock.monotonic(),'synced':datetime.now(timezone.utc)}
    st.session_state['pm_meeting_cache']=result
    return result


def invalidate_meetings():
    st.session_state.pop('pm_meeting_cache',None)
    invalidate_snapshot()


def calendar_html(meetings,year,month,zone,today):
    tz=ZoneInfo(zone);rows=[]
    for week in calendar.Calendar().monthdatescalendar(year,month):
        cells=[]
        for day in week:
            events=[]
            for meeting in meetings:
                start=instant(meeting['start']).astimezone(tz);end=instant(meeting['end']).astimezone(tz)
                if start.date()<=day<=(end-timedelta(microseconds=1)).date():
                    label=(start.strftime('%H:%M') if start.date()==day else 'Continues')+' · '+meeting['title']
                    if meeting['status']=='Cancelled':label='Cancelled · '+label
                    events.append('<div class="pm-calendar-event">'+escape(label)+'</div>')
            extra=f'<small>+{len(events)-3} more · see meeting list</small>' if len(events)>3 else ''
            style='background:#EDF5F5;' if day==today else 'opacity:.5;' if day.month!=month else ''
            cells.append(f'<td style="{style}"><time datetime="{day.isoformat()}">{day.day}</time>'+''.join(events[:3])+extra+'</td>')
        rows.append('<tr>'+''.join(cells)+'</tr>')
    headings=''.join('<th scope="col">'+d+'</th>' for d in ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'])
    return f'<table class="pm-calendar" aria-label="Meeting calendar for {calendar.month_name[month]} {year}"><thead><tr>{headings}</tr></thead><tbody>'+''.join(rows)+'</tbody></table>'


@st.fragment(run_every='60s')
def meeting_calendar_view(settings,identity,allowed):
    if not has_full_access(identity(),allowed()):return
    try:snapshot=load_meetings(settings,identity,allowed)
    except PermissionError as exc:
        st.error(str(exc))
        st.button('Retry meeting synchronization',on_click=invalidate_meetings,key='pm_meeting_retry');return
    except ValueError as exc:
        st.error(str(exc))
        st.button('Retry meeting synchronization',on_click=invalidate_meetings,key='pm_meeting_retry');return
    except Exception:
        logging.getLogger(__name__).exception('Meeting synchronization failed.')
        st.error('Meetings could not be loaded from the project workbook. Retry synchronization; if the problem continues, ask the administrator to check the app logs.')
        st.button('Retry meeting synchronization',on_click=invalidate_meetings,key='pm_meeting_retry');return
    now=datetime.now(timezone.utc)
    st.subheader('Meeting calendar')
    st.caption('Administrators see every meeting. Collaborators see meetings they are invited to. Refreshes every 60 seconds.')
    st.caption('Synchronized · '+snapshot['synced'].strftime('%d %b %Y %H:%M:%S UTC'))
    st.button('Refresh meetings',on_click=invalidate_meetings,key='pm_meeting_refresh')
    zones=sorted({'Africa/Accra','UTC','Europe/London','Europe/Berlin','America/New_York','Africa/Johannesburg','Africa/Nairobi','Asia/Kolkata'}|{m['timezone'] for m in snapshot['meetings']})
    zone=st.selectbox('Display time zone',zones,index=zones.index('Africa/Accra'),key='pm_meeting_display_zone')
    today=now.astimezone(ZoneInfo(zone)).date()
    left,right=st.columns(2)
    month=left.selectbox('Meeting calendar month',range(1,13),index=today.month-1,format_func=lambda n:calendar.month_name[n],key='pm_meeting_month')
    year=int(right.number_input('Meeting calendar year',min_value=1900,max_value=9998,value=today.year,key='pm_meeting_year'))
    archive=st.checkbox('Include past and cancelled meetings',key='pm_meeting_archive')
    meetings=sorted([m for m in snapshot['meetings'] if archive or m['status']=='Scheduled' and instant(m['end'])>now],key=lambda m:m['start'])
    st.html(calendar_html(meetings,year,month,zone,today))
    if not meetings:
        st.info('No meetings match this view. Administrators can schedule a meeting below.');return
    st.write('**Meeting list · all dates**')
    by_id={m['id']:m for m in meetings}
    selected=st.selectbox('Meeting details',list(by_id),format_func=lambda k:instant(by_id[k]['start']).astimezone(ZoneInfo(zone)).strftime('%d %b %Y %H:%M')+' · '+by_id[k]['title'],key='pm_meeting_details')
    meeting=by_id[selected]
    with st.container(border=True):
        st.subheader(meeting['title'])
        start=instant(meeting['start']).astimezone(ZoneInfo(zone));end=instant(meeting['end']).astimezone(ZoneInfo(zone))
        st.write(f'{start:%d %b %Y %H:%M} – {end:%d %b %Y %H:%M} · {zone}')
        st.caption('Status: '+meeting['status']+' · Organizer: '+meeting['organizer'])
        st.text(meeting['agenda'] or 'No agenda supplied.')
        if meeting['status']=='Scheduled':st.link_button('Join meeting',meeting['link'])
        st.download_button('Add / update in my calendar (.ics)',meeting_calendar(meeting),file_name='DARE-Arbo_meeting.ics',mime='text/calendar',key='pm_meeting_download')
        st.caption('Calendar downloads are snapshots. Email invitations carry schedule updates and cancellations. RSVP responses stay in your email/calendar; this page does not track attendance.')


def after_save(message,meeting_id):
    invalidate_meetings();st.session_state.pop('pm_meeting_editor_snapshot',None)
    st.session_state['pm_meeting_flash']=message
    st.session_state['pm_meeting_focus']=meeting_id
    st.session_state['pm_meeting_generation']=st.session_state.get('pm_meeting_generation',0)+1
    st.rerun()


@st.fragment
def meeting_admin(settings,identity,allowed):
    if not has_full_access(identity(),allowed()):return
    cfg=settings();admins=cfg.get('project_management',{}).get('admin_emails',DEFAULT_ADMINS)
    if identity()['email'].strip().casefold() not in {e.strip().casefold() for e in admins}:return
    st.divider();st.subheader('Schedule and manage meetings')
    st.caption('Administrator access · Paste your own meeting link. External invitations do not grant workspace access.')
    scope=snapshot_scope(settings,identity,allowed)
    current=st.session_state.get('pm_meeting_editor_snapshot')
    if not current or current['scope']!=scope:
        try:current=load_meetings(settings,identity,allowed)
        except Exception:st.error('Check collaborator access and the private Supabase workbook before scheduling meetings.');return
        st.session_state['pm_meeting_editor_snapshot']=current
    if st.session_state.get('pm_meeting_flash'):st.success(st.session_state.pop('pm_meeting_flash'))
    if st.button('Reload meeting editor (discards unsaved changes)',key='pm_meeting_reload'):
        after_save('Meeting editor reloaded. No invitations sent.',st.session_state.get('pm_meeting_focus','new'))
    meetings={m['id']:m for m in current['meetings']};options=['new']+list(meetings)
    generation=st.session_state.get('pm_meeting_generation',0)
    focus=st.session_state.get('pm_meeting_focus','new')
    selected=st.selectbox('Meeting to manage',options,index=options.index(focus) if focus in options else 0,
                          format_func=lambda k:'Schedule a new meeting' if k=='new' else meetings[k]['title']+' · '+meetings[k]['status'],key='pm_meeting_manage_'+str(generation))
    meeting=meetings.get(selected)
    email=cfg.get('email',{})
    if not meeting or meeting['status']!='Cancelled':
        tz=ZoneInfo(meeting['timezone'] if meeting else 'Africa/Accra')
        start=instant(meeting['start']).astimezone(tz) if meeting else datetime.combine(datetime.now(tz).date()+timedelta(days=1),time(14))
        end=instant(meeting['end']).astimezone(tz) if meeting else start+timedelta(hours=1)
        approved=sorted({str(e).strip().casefold() for e in allowed()})
        existing=set(meeting['attendees'] if meeting else [])
        st.caption('Save before selecting another meeting. Automatic calendar refreshes do not replace this form.')
        with st.form('pm_meeting_form_'+selected+'_'+str(generation)):
            title=st.text_input('Meeting title',value=meeting['title'] if meeting else '',max_chars=200)
            zones=sorted(available_timezones())
            zone=st.selectbox('Meeting time zone',zones,index=zones.index(tz.key),help='Both start and end use this time zone.')
            left,right=st.columns(2)
            start_day=left.date_input('Start date',value=start.date());start_clock=left.time_input('Start time',value=start.time(),step=60)
            end_day=right.date_input('End date',value=end.date());end_clock=right.time_input('End time',value=end.time(),step=60)
            link=st.text_input('Meeting link',value=meeting['link'] if meeting else '',placeholder='Paste the meeting link',max_chars=2000,help='Paste a link from your chosen meeting service.')
            agenda=st.text_area('Agenda / meeting notes',value=meeting['agenda'] if meeting else '',max_chars=5000)
            collaborators=st.multiselect('Invite approved collaborators',approved,default=sorted(existing&set(approved)))
            partners=st.text_area('Additional partner email addresses',value=', '.join(sorted(existing-set(approved))),help='Separate addresses with commas, semicolons or new lines. Up to 50 invitees in total.')
            submitted=st.form_submit_button('Save meeting',type='primary')
        if submitted:
            try:
                fields=dict(title=title,agenda=agenda,start=local_instant(start_day,start_clock,zone),end=local_instant(end_day,end_clock,zone),timezone=zone,link=link,attendees=','.join(collaborators)+','+partners)
                result=store_for(settings,identity,allowed).save_meeting(fields,current['version'],email.get('from_address') or identity()['email'],meeting['id'] if meeting else None)
            except (ValueError,PermissionError) as exc:st.error(str(exc))
            except Exception:
                logging.getLogger(__name__).warning('Meeting save could not be confirmed.')
                st.error('The save could not be confirmed. Reload meetings before trying again. No invitations were sent.')
            else:after_save('Meeting saved. Invitations and updates are queued below; choose Send queued invitations to email them.',result['meeting']['id'])
    else:st.info('This meeting is cancelled. You can send its queued cancellation notices below, or schedule a new meeting.')
    if meeting:
        st.write('**Invitees**');st.text(', '.join(meeting['attendees']))
        history=[e for e in current['invitations'] if e['meeting_id']==meeting['id']]
        queued=[e for e in history if e['state']=='queued']
        st.write('**Invitations and updates**')
        st.caption('Email sender: '+str(email.get('from_address','Not configured'))+f' · {len(queued)} queued notices')
        if st.button('Send queued invitations',disabled=not queued or not configured(email),key='pm_meeting_send_'+selected):
            try:
                with st.spinner('Sending meeting invitations…'):result=send_invitations(store_for(settings,identity,allowed),email,meeting['id'])
            except Exception:
                invalidate_meetings();st.session_state.pop('pm_meeting_editor_snapshot',None)
                st.error('Delivery could not be completed. Reload the invitation history before trying again. Claimed or uncertain deliveries will not be automatically repeated.')
            else:after_save(f"Mail server accepted {result['sent']} notices; {result['skipped']} skipped; {result['uncertain']} uncertain. Acceptance does not confirm inbox delivery.",meeting['id'])
        if not configured(email):st.info('Configure the existing email sender to deliver these invitations. The schedule remains saved in the workbook.')
        st.caption('Each email shows only its recipient. Claimed or uncertain attempts are not retried automatically. Editing a meeting queues fresh updates; removing a guest queues a cancellation for them.')
        if history:st.dataframe([{'Recipient':e['recipient'],'Notice':e['method'],'Delivery':e['state'],'Updated UTC':e['updated_at']} for e in history],hide_index=True,width='stretch')
        if meeting['status']=='Scheduled':
            confirm=st.checkbox('Confirm cancellation of this meeting',key='pm_meeting_cancel_confirm_'+selected)
            if st.button('Cancel meeting',disabled=not confirm,key='pm_meeting_cancel_'+selected):
                try:store_for(settings,identity,allowed).save_meeting({},current['version'],meeting['organizer'],meeting['id'],cancel=True)
                except (ValueError,PermissionError) as exc:st.error(str(exc))
                except Exception:st.error('Cancellation could not be confirmed. Reload meetings before trying again.')
                else:after_save('Meeting cancelled. Send the queued cancellation notices to inform invitees.',meeting['id'])


def show_meetings(settings,identity,allowed):
    if not has_full_access(identity(),allowed()):return
    admins=settings().get('project_management',{}).get('admin_emails',DEFAULT_ADMINS)
    if identity()['email'].strip().casefold() in {e.strip().casefold() for e in admins}:
        calendar_tab,scheduler_tab=st.tabs(['Meeting calendar','Schedule & invitations'])
        with calendar_tab:meeting_calendar_view(settings,identity,allowed)
        with scheduler_tab:meeting_admin(settings,identity,allowed)
    else:meeting_calendar_view(settings,identity,allowed)
