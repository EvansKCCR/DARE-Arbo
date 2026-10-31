"""Automatically refreshed public team page and isolated administrator editor."""
from base64 import b64encode
from datetime import datetime,timezone
from hashlib import sha256
from html import escape
from pathlib import Path
import logging
import time
import streamlit as st
from access import has_full_access
from drive_store import create_service
from team_store import FIELDS, LIMITS, PORTRAITS, TeamProfiles, parse_team, public_blob


def fetch_public_team(config):
    from sheet_dashboard import SOURCE_ID
    data=public_blob(create_service(config),SOURCE_ID)
    return dict(members=parse_team(data),version=sha256(data).hexdigest(),synced=datetime.now(timezone.utc))


def load_team(settings, study, force=False):
    config=settings().get('drive',{})
    scope=sha256(str(sorted(config.items())).encode()).hexdigest()
    old=st.session_state.get('team_snapshot')
    if old and old['scope']==scope and not force and time.monotonic()-old['loaded']<60:
        return old
    try:
        if not all(config.get(k) for k in ('client_id','client_secret','refresh_token')):
            raise ValueError('Drive not configured')
        data={**fetch_public_team(config),'live':True}
    except Exception:
        logging.getLogger(__name__).warning('Public team synchronization unavailable.')
        if old and old['scope']==scope:
            data={**old,'live':False}
        else:
            members=[{**m,'profile_id':'published-'+str(i),'row':None,'photo_id':'','featured':m['name'] in PORTRAITS} for i,m in enumerate(study['team'])]
            data=dict(members=members,version=None,synced=None,live=False)
    result={**data,'scope':scope,'loaded':time.monotonic()}
    st.session_state['team_snapshot']=result
    return result


def invalidate_team():
    st.session_state.pop('team_snapshot',None)


def portrait_bytes(member,settings,root):
    photo_id=member.get('photo_id')
    if photo_id:
        cache=st.session_state.setdefault('team_photo_cache',{})
        cached=cache.get(photo_id)
        if cached and time.monotonic()-cached[0]<60:
            return cached[1]
        try:
            data=public_blob(create_service(settings().get('drive',{})),photo_id,portrait=True)
            cache[photo_id]=(time.monotonic(),data)
            return data
        except Exception:
            logging.getLogger(__name__).warning('Published team portrait unavailable.')
    filename=member.get('local_portrait') or PORTRAITS.get(member['name'])
    if filename in PORTRAITS.values() and (root/'Images'/filename).is_file():
        return (root/'Images'/filename).read_bytes()
    return None


@st.fragment(run_every='60s')
def public_team(settings,root,study):
    snapshot=load_team(settings,study)
    if snapshot['live']:
        st.caption('Team profiles synchronized · '+snapshot['synced'].strftime('%d %b %Y %H:%M:%S UTC')+' · Automatic refresh every 60 seconds')
    else:
        st.caption('Live synchronization is unavailable. Showing the last published team profiles; retrying automatically.')
    st.button('Refresh team profiles',on_click=invalidate_team,key='team_public_refresh')
    query=st.text_input('Find a team member',placeholder='Search by name, expertise or institution')
    affiliations=sorted({m['affiliation'] for m in snapshot['members']})
    institution=st.selectbox('Affiliation',['All affiliations']+affiliations)
    members=[m for m in snapshot['members'] if (institution=='All affiliations' or m['affiliation']==institution)
             and query.casefold() in ' '.join(str(m.get(k,'')) for k in FIELDS).casefold()]
    st.caption(f'{len(members)} team members')
    if not members:
        st.info('No team members match your search.')
    cards=[]
    for member in members:
        if not member['featured']:continue
        profile={k:escape(str(member.get(k,''))) for k in FIELDS}
        data=portrait_bytes(member,settings,root)
        photo=(f'<img src="data:image/png;base64,{b64encode(data).decode()}" alt="Portrait of {profile["name"]}">' if data else f'<span role="img" aria-label="Portrait not provided" style="font-size:3rem;color:#526277">{profile["initials"]}</span>')
        cards.append('<article class="team-card"><div class="team-photo">'+photo+'</div>'
                     f'<h3>{profile["name"]}</h3><p class="team-role">{profile["position"]}</p>'
                     f'<p class="team-affiliation">{profile["affiliation"]}</p>'
                     f'<div class="team-expertise"><strong>Expertise</strong><p>{profile["expertise"]}</p></div></article>')
    if cards:st.html('<div class="team-grid">'+''.join(cards)+'</div>')
    remaining=[m for m in members if not m['featured']]
    if remaining:
        st.subheader('Additional team members')
        st.dataframe([{'Name':m['name'],'Position':m['position'],'Affiliation':m['affiliation'],'Expertise':m['expertise']} for m in remaining],hide_index=True,width='stretch')
    st.caption('Public profiles and portraits supplied by the project team. Updates are read from the workbook’s Team_members tab.')


def show_team(settings,identity,allowed,root,study):
    root=Path(root)
    st.title('Meet the study team')
    st.write('A multidisciplinary collaboration spanning infectious diseases, surveillance, laboratory diagnosis, evidence synthesis and quantitative methods.')
    public_team(settings,root,study)
    admin_team(settings,identity,allowed,study)


@st.fragment
def admin_team(settings,identity,allowed,study):
    if not has_full_access(identity(),allowed()):return
    cfg=settings()
    admins=cfg.get('project_management',{}).get('admin_emails',['evansasamoahadu@gmail.com'])
    if identity()['email'].strip().casefold() not in {e.strip().casefold() for e in admins}:return
    st.divider();st.subheader('Manage team profiles')
    st.caption('Administrator access · Saved profile text and portraits are visible to all visitors.')
    scope=identity()['email'].casefold()+str(sorted(allowed()))+str(admins)+str(cfg.get('drive',{}))
    scope=sha256(scope.encode()).hexdigest()
    current=st.session_state.get('team_editor_snapshot')
    if not current or current['editor_scope']!=scope:
        current={**load_team(settings,study),'editor_scope':scope}
        st.session_state['team_editor_snapshot']=current
    if st.button('Reload profiles for editing (discards unsaved changes)'):
        st.session_state['team_editor_snapshot']={**load_team(settings,study,force=True),'editor_scope':scope}
        st.session_state['team_form_generation']=st.session_state.get('team_form_generation',0)+1
        st.rerun()
    if not current['live'] or not current.get('version'):
        st.info('Restore the live Drive connection before editing profiles.')
        return
    if st.session_state.pop('team_saved',False):st.success('Team profile published. Visitors see the update on their next refresh.')
    options={m['profile_id']:m for m in current['members']}
    selected=st.selectbox('Profile to edit',['new']+list(options),format_func=lambda k:'Add a new team member' if k=='new' else options[k]['name'])
    member=options.get(selected,{})
    st.caption('Save changes before selecting another profile. Public-page refreshes do not change this form.')
    with st.form('team_editor_'+selected+'_'+str(st.session_state.get('team_form_generation',0))):
        name=st.text_input('Full name',member.get('name',''),max_chars=LIMITS['name'])
        left,right=st.columns(2)
        initials=left.text_input('Initials',member.get('initials',''),max_chars=LIMITS['initials'])
        position=right.text_input('Position / role',member.get('position',''),max_chars=LIMITS['position'])
        affiliation=st.text_input('Institution / affiliation',member.get('affiliation',''),max_chars=LIMITS['affiliation'])
        expertise=st.text_area('Expertise / profile',member.get('expertise',''),max_chars=LIMITS['expertise'])
        featured=st.checkbox('Show as a profile card',value=member.get('featured',True))
        photo=st.file_uploader('Profile photograph (JPEG or PNG, up to 5 MB)',type=['jpg','jpeg','png'])
        submitted=st.form_submit_button('Publish team profile',type='primary')
    if submitted:
        fields=dict(name=name,initials=initials,position=position,affiliation=affiliation,expertise=expertise,featured=featured)
        try:
            store=TeamProfiles(create_service(cfg['drive']),identity,allowed,lambda:admins)
            with st.spinner('Publishing team profile…'):
                store.save_profile(fields,member.get('row'),current['version'],photo.getvalue() if photo else None)
        except (ValueError,PermissionError) as exc:
            st.error(str(exc))
        except Exception:
            logging.getLogger(__name__).warning('Team profile publication failed.')
            st.error('The profile could not be published. Check the Drive connection and reload before retrying.')
        else:
            invalidate_team()
            st.session_state.pop('team_editor_snapshot',None)
            st.session_state['team_saved']=True
            st.session_state['team_form_generation']=st.session_state.get('team_form_generation',0)+1
            st.rerun()
