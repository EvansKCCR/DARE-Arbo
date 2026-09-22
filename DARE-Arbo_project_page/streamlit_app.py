"""DARE-Arbo study website and restricted collaborator document library."""
from datetime import date
from pathlib import Path
import logging
import json
from html import escape
import streamlit as st
from access import has_full_access
from drive_store import DriveStore, create_service, ALLOWED

ROOT = Path(__file__).resolve().parent
STUDY = json.loads((ROOT / 'study_data.json').read_text(encoding='utf-8'))
st.set_page_config(page_title='DARE-Arbo | Study & collaboration', page_icon='🧬', layout='wide')


def settings():
    try:
        return st.secrets.to_dict()
    except FileNotFoundError:
        return {}


def identity():
    return dict(st.user) | {'is_logged_in': True} if getattr(st.user, 'is_logged_in', False) else {}


def allowed():
    return settings().get('access', {}).get('full_access_emails', [])


def figure(filename, caption):
    if (ROOT / filename).exists():
        st.image(str(ROOT / filename), caption=caption, width='stretch')


def documents():
    st.header('Project documents')
    st.subheader('Public study protocol')
    st.write('The framework development protocol is available to everyone. Choose Public protocol in the navigation to read the full text without signing in.')
    st.divider()
    st.subheader('Collaborator library')
    st.write('A shared library for approved collaborators. Full access is required to browse, upload or download study documents.')
    if not has_full_access(identity(), allowed()):
        st.info('Sign in with an approved Google account to access documents. If you are already signed in, contact the project coordinator to request full access.')
        return
    cfg = settings().get('drive', {})
    if not all(cfg.get(k) and not cfg[k].startswith(('GOOGLE_', 'OWNER_')) for k in ('client_id', 'client_secret', 'refresh_token')):
        st.warning('The document library is awaiting administrator connection to Google Drive.')
        return
    try:
        store = DriveStore(create_service(cfg), identity, allowed)
        with st.expander('Upload a document', expanded=True):
            st.caption('Up to 25 MB per file. Uploads create new files; existing files are never replaced.')
            with st.form('upload', clear_on_submit=True):
                uploaded = st.file_uploader('Choose a document', type=sorted(x.lstrip('.') for x in ALLOWED))
                submitted = st.form_submit_button('Upload to project folder', type='primary')
            if submitted:
                if uploaded is None:
                    st.warning('Choose a document first.')
                else:
                    with st.spinner('Uploading document…'):
                        result = store.upload(uploaded.name, uploaded.getvalue())
                    st.success(f"Uploaded: {result['name']}")
        st.subheader('Document library')
        st.button('Refresh library')
        files = store.list_files()
        query = st.text_input('Search filenames', placeholder='Find a protocol, codebook or appraisal workbook…')
        files = [f for f in files if query.casefold() in f['name'].casefold()]
        if not files:
            st.info('No documents match your search.' if query else 'The project folder is empty.')
        for f in files:
            with st.container(border=True):
                left, right = st.columns([4, 1])
                left.write(f['name'])
                left.caption(f"Updated {f.get('modifiedTime', '')[:10]} · {f.get('mimeType', '').split('.')[-1]}")
                if f['mimeType'] in ('application/vnd.google-apps.folder', 'application/vnd.google-apps.shortcut'):
                    right.caption('Folder / shortcut')
                elif right.button('Prepare download', key=f"prepare_{f['id']}"):
                    with st.spinner('Preparing your document…'):
                        name, mime, content = store.download(f['id'])
                    # Payload exists only during this authorized run, never in a shared cache.
                    st.download_button('Download document', content, file_name=name, mime=mime,
                        key=f"download_{f['id']}", on_click='rerun')
        st.caption('Google Docs and Slides export as PDF; Google Sheets export as Excel. Native Google exports have a 10 MB API limit. Other files have a 25 MB limit. This library lists files directly inside the project folder.')
    except (PermissionError, ValueError) as exc:
        st.error(str(exc))
    except Exception:
        # Do not render exceptions containing credentials or Drive response data.
        logging.getLogger(__name__).warning('Project Drive operation failed; check provider configuration and access.')
        st.error('Google Drive could not complete this request. Please retry or ask the administrator to check the connection, folder permissions and storage quota.')


st.markdown('''<style>
.block-container{max-width:1200px;padding-top:2rem;padding-bottom:3rem}
.hero{background:linear-gradient(120deg,#004D2D,#006B3F);padding:2.8rem;border-radius:20px;margin:1rem 0 1.6rem;color:white;border-top:8px solid #FCD116;border-bottom:5px solid #CE1126}
.hero h1{color:white;font-size:3.1rem;letter-spacing:-1.5px;margin:0}
.hero p{color:#FFFFFF;font-size:1.13rem;max-width:820px;line-height:1.7}
.eyebrow{font-size:.78rem;letter-spacing:.16em;font-weight:700;text-transform:uppercase;color:#FCD116}
[data-testid="stMetric"]{background:#FFFFFF;color:#111111;border:1px solid #D9DED9;border-top:5px solid #006B3F;border-radius:14px;padding:1rem}
[data-testid="stColumn"]:nth-child(2) [data-testid="stMetric"]{border-top-color:#FCD116}
[data-testid="stColumn"]:nth-child(3) [data-testid="stMetric"]{border-top-color:#CE1126}
[data-testid="stColumn"]:nth-child(4) [data-testid="stMetric"]{border-top-color:#111111}
[data-testid="stSidebar"]{background:#FFF8D6;border-right:1px solid #FCD116}
[data-testid="stHeading"] h1,[data-testid="stHeading"] h2,[data-testid="stHeading"] h3{color:#006B3F}
[data-testid="stAlertContainer"]{background:#FFF8D6;color:#111111;border-left:4px solid #FCD116}
[data-testid="stAlertContainer"] a{color:#004D2D}
a:focus-visible,button:focus-visible{outline:3px solid #CE1126;outline-offset:3px}
@media(max-width:640px){.hero{padding:1.5rem}.hero h1{font-size:2.3rem}}
</style>''', unsafe_allow_html=True)

with st.sidebar:
    st.image(str(ROOT / 'Logo.jpeg'), width=115)
    st.markdown('### DARE-Arbo')
    st.caption('Study & collaboration portal')
    page = st.radio('Explore the study', ['Overview', 'Public protocol', 'Framework & methods', 'Study timeline', 'Team', 'Development workflow', 'Collaborate', 'Project documents'], label_visibility='collapsed')
    st.divider()
    config = settings()
    if getattr(st.user, 'is_logged_in', False):
        st.write(st.user.get('name', 'Signed-in collaborator'))
        st.caption('Full access' if has_full_access(identity(), allowed()) else 'Visitor access')
        st.button('Sign out', on_click=st.logout)
    elif config.get('auth', {}).get('client_id') and not config['auth']['client_id'].startswith('GOOGLE_'):
        st.button('Sign in with Google', on_click=st.login, width='stretch')
    else:
        st.caption('Public study information is available. Collaborator sign-in will be enabled after administrator setup.')
    with st.expander('How sign-in works'):
        st.write('1. Choose Sign in with Google when sign-in is enabled.\n2. Use the Google email approved by the project administrator.\n3. Approved accounts receive full access to the document library; other accounts retain visitor access.')
        st.caption('Submitting the collaborator form or appearing on the team page does not grant document access. Contact the project coordinator for approval. If your session expires, sign out and sign in again.')

if page == 'Overview':
    st.markdown('''<div class="hero"><div class="eyebrow">Arbovirus surveillance · Methodological development</div>
    <h1>DARE-Arbo</h1><p>An endpoint-specific appraisal framework for strengthening the interpretation and synthesis of arbovirus surveillance evidence.</p></div>''', unsafe_allow_html=True)
    st.header('About DARE-Arbo')
    st.write((ROOT / 'Overview.txt').read_text(encoding='utf-8-sig').strip())
    for column, value, label in zip(st.columns(4), ['3', '12', '47', '36'], ['Appraisal domains', 'Scored components', 'Planned pilot article-outcomes', 'Pilot publications']):
        column.metric(label, value)
    st.header('Make each estimate interpretable')
    st.write('A single publication may report molecular detection, antigen positivity and several antibody outcomes, each with a different testing pathway or denominator. DARE-Arbo links each estimate to its target population, biological endpoint, assay pathway, numerator and denominator.')
    st.subheader('Three connected domains')
    for col, title, body in zip(st.columns(3), ['Study design', 'Assay design', 'Outcome reporting'], [
        'Surveillance purpose, population representation, sampling-frame coverage and participant or specimen selection.',
        'Biological endpoints, diagnostic pathways, assay validity, confirmatory testing and cross-reactivity.',
        'Numerator–denominator compatibility, assay-specific estimates, composite outcomes and article-outcome reconstruction.']):
        with col.container(border=True):
            st.subheader(title)
            st.write(body)
    st.info('Primary objective: develop and prospectively refine DARE-Arbo using the article-outcome as the unit of appraisal. Primary endpoint: inter-assessor agreement on criterion ratings.')
    st.subheader('A collaborative study')
    c1, c2 = st.columns(2)
    with c1:
        st.image(str(ROOT / 'Logo.jpeg'), width=110)
        st.write('**Global Health and Infectious Disease Research Group**')
        st.caption('Kumasi Centre for Collaborative Research in Tropical Medicine')
    with c2:
        st.image(str(ROOT / 'Synergy_NGS2025.png'), width=110)
        st.write('**SYNERGY-NGS-2025**')
        st.caption('Framework development collaboration')
    st.link_button('Express interest in collaborating ↗', 'https://forms.gle/4jigcLBfeqFhMVZP6', type='primary')

elif page == 'Public protocol':
    from public_protocol import protocol_sections
    st.title('Framework development protocol')
    st.caption('Public access · No sign-in required · Protocol version 1.0, 01 April 2026')
    st.write('Read the full text of DARE-Arbo_framework_development.docx, including its study methods, analysis plan and references.')
    sections = protocol_sections()
    selected = st.selectbox('Read a section', ['Full document'] + [s['title'] for s in sections])
    for section in sections:
        if selected == 'Full document' or selected == section['title']:
            st.header(section['title'])
            st.html(section['html'])
    st.caption('Text and tables are read from the supplied Word protocol. Layout is adapted for online reading.')

elif page == 'Framework & methods':
    st.title('Framework & methods')
    st.caption('Methodology paper development protocol · Version 1.0 · 01 April 2026')
    st.header('The article-outcome is the unit of appraisal')
    st.write('Each assessment is defined by publication, target population, sampling period where relevant, arbovirus, biological endpoint, assay pathway, numerator and denominator. Separate assessments are created when these features differ within a publication.')
    st.write('Pilot coverage includes DENV, CHIKV, ZIKV, YFV, WNV and RVFV. IgG or total-antibody prevalence, IgM positivity, NS1 positivity, neutralizing-antibody prevalence and molecular detection are appraised separately when outcome-specific counts are available.')
    with st.expander('Scoring and interpretation', expanded=True):
        st.write('The framework contains 12 scored components across three domains. Binary and ordered responses are summed without additional weighting. Each assessment retains its applicable maximum, so a non-applicable criterion is not counted as a deficit.')
        st.write('Q10, correction for assay sensitivity and specificity, applies only to IgG or total-antibody prevalence, IgM positivity and NS1 positivity. It is not applicable to neutralizing-antibody prevalence or direct molecular or viable-virus detection.')
        st.info('Overall scores are descriptive and must not be converted into automatic quality categories. Interpret criterion ratings, domain profiles and narrative rationale together. DARE-Arbo does not replace diagnostic-accuracy tools when test performance is the primary objective.')
    with st.expander('Planned pilot evaluation', expanded=True):
        st.write('The purposive pilot comprises 47 article-outcomes from 36 publications. Four external assessors will work independently in two teams using a two-period crossover: manual appraisal followed by the digital workspace, or the reverse. Assessors remain blinded to other ratings and do not access their previous assessments when switching methods.')
        st.write('Reconciliation and source-level adjudication occur after all independent assessments are complete. The pilot evaluates methodological coverage, usability and preliminary reliability.')
    with st.expander('Analysis, refinement and planned outputs'):
        st.write('Analyses will summarize applicability, missingness, response distributions, domain scores and disagreements. Agreement will include observed percentage agreement and a chance-corrected coefficient with 95% confidence intervals, with weighted agreement for ordinal criteria and publication-level clustering accounted for.')
        st.write('Discrepancy audits will distinguish ambiguous rules, insufficient reporting, differing interpretations and application errors. Refinements will be versioned with dates and rationales across the codebook, decision tree and digital workspace.')
        st.write('Planned outputs include the framework, scoring codebook, decision tree, reliability evaluation, discrepancy audit, digital workspace and methodology manuscript.')
    with st.expander('Ethics and dissemination'):
        st.write('The protocol evaluates published reports and does not involve recruitment of human participants or collection of identifiable participant-level data. Materials will be disseminated with the methodology paper, subject to repository, licensing and copyright requirements.')
    st.link_button('Related systematic review protocol ↗', 'https://doi.org/10.1186/s13643-025-02879-z')

elif page == 'Study timeline':
    import altair as alt
    import pandas as pd
    st.title('Study timeline')
    st.write('From protocol consolidation to repository archiving and journal submission: January 2026–February 2027.')
    st.caption('Dates, owners and recorded statuses come from the workbook’s Activity Register. Statuses are recorded values, not inferred from today’s date.')
    tasks = STUDY['tasks']
    for col, label, value in zip(st.columns(3), ['Activities', 'Recorded completed', 'Recorded in progress'], [len(tasks), sum(t['status']=='Completed' for t in tasks), sum(t['status']=='In progress' for t in tasks)]):
        col.metric(label, value)
    selection = st.multiselect('Filter workstreams', list(dict.fromkeys(t['workstream'] for t in tasks)))
    rows = [t for t in tasks if not selection or t['workstream'] in selection]
    if rows:
        frame = pd.DataFrame(rows)
        frame['chart_end'] = pd.to_datetime(frame['end']) + pd.Timedelta(days=1)
        frame['label'] = frame['id'] + ' · ' + frame['workstream']
        chart = alt.Chart(frame).mark_bar(cornerRadius=3).encode(
            x=alt.X('start:T', title='Scheduled dates', axis=alt.Axis(format='%b %Y')),
            x2='chart_end:T',
            y=alt.Y('label:N', sort=frame['label'].tolist(), title=None),
            color=alt.Color('status:N', title='Recorded status', scale=alt.Scale(
                domain=['Completed', 'In progress', 'Planned / update', 'Not started'],
                range=['#006B3F', '#FCD116', '#CE1126', '#111111'])),
            tooltip=[alt.Tooltip('activity:N', title='Activity'), alt.Tooltip('start:T', title='Start', format='%d %b %Y'),
                     alt.Tooltip('end:T', title='End', format='%d %b %Y'), alt.Tooltip('status:N', title='Status'),
                     alt.Tooltip('owner:N', title='Owner'), alt.Tooltip('output:N', title='Output')]
        ).properties(height=max(220, len(rows)*30)).configure_legend(orient='bottom')
        st.altair_chart(chart, width='stretch')
        st.subheader('Activities and milestones')
        for task in rows:
            with st.expander(f"{task['id']} · {task['activity']}"):
                start, end = date.fromisoformat(task['start']), date.fromisoformat(task['end'])
                st.write(f"**{start:%d %b %Y} – {end:%d %b %Y}** · {task['status']}")
                st.write(f"**Output:** {task['output']}")
                st.write(f"**Owner (as recorded):** {task['owner']}")
    st.caption('Source: ' + STUDY['source'] + ' · Activity Register. Owner initials AAA and JM appear in the register but do not exactly match the team list; they are retained without assigning them to a person.')
    with st.expander('Schedule differences in the earlier collaboration notice'):
        st.write('The earlier notice places discrepancy auditing on 09–20 November 2026 and manuscript development from 05 January 2027. The activity register places agreement analysis on 09–20 November, discrepancy auditing on 23 November–11 December, and manuscript development from 04 January 2027. This timeline follows the activity register. Its dated rows extend beyond the workbook title to 20 February 2027.')

elif page == 'Team':
    st.title('Meet the study team')
    st.write('A multidisciplinary collaboration spanning infectious diseases, surveillance, laboratory diagnosis, evidence synthesis and quantitative methods.')
    st.caption('Profiles reflect the names, positions, affiliations and experience listed in the project workbook.')
    query = st.text_input('Find a team member', placeholder='Search by name, expertise or institution')
    affiliations = sorted({m['affiliation'] for m in STUDY['team']})
    institution = st.selectbox('Affiliation', ['All affiliations'] + affiliations)
    members = [m for m in STUDY['team'] if (institution == 'All affiliations' or m['affiliation'] == institution)
               and query.casefold() in ' '.join(m.values()).casefold()]
    st.caption(f'{len(members)} team members')
    if not members:
        st.info('No team members match your search.')
    for offset in range(0, len(members), 2):
        for col, member in zip(st.columns(2), members[offset:offset+2]):
            with col.container(border=True):
                st.markdown('<div style="display:inline-block;background:#006B3F;color:white;border-bottom:4px solid #FCD116;border-radius:12px;padding:12px 18px;font-weight:700">' + escape(member['initials']) + '</div>', unsafe_allow_html=True)
                st.subheader(member['name'])
                st.write('**' + member['position'] + '**')
                st.caption(member['affiliation'])
                st.write('**Expertise**')
                st.write(member['expertise'])
    st.caption('Source: ' + STUDY['source'] + ' · Team_members. Profile initials are used in place of photographs.')

elif page == 'Development workflow':
    st.title('Development workflow')
    st.write('Evidence review and construct identification inform domain mapping, criteria and the scoring codebook. Independent pilot testing then supports disagreement auditing and iterative refinement before final implementation.')
    figure('DARE-Arbo_development_pipeline.png', 'Development pipeline supplied with the study materials. Week ranges are indicative, not verified completion dates.')
    with st.expander('View the additional framework workflow'):
        figure('Framework_development_worflow.png', 'Additional framework development workflow from the source folder.')

elif page == 'Collaborate':
    st.title('Help refine DARE-Arbo')
    st.write('Researchers and practitioners are invited to contribute to pilot testing and expert review of the framework.')
    opening, closing = date(2026, 9, 22), date(2026, 10, 10)
    today = date.today()
    st.info('Expression-of-interest window: 22 September–10 October 2026. ' + ('The scheduled window is open.' if opening <= today <= closing else 'The scheduled window has closed.' if today > closing else 'The scheduled window has not yet opened.'))
    st.link_button('Complete the expression-of-interest form ↗', 'https://forms.gle/4jigcLBfeqFhMVZP6', type='primary')
    c1, c2 = st.columns(2)
    with c1:
        st.subheader('Who can contribute?')
        st.markdown('- Epidemiology and infectious disease surveillance\n- Arbovirus surveillance and outbreak investigation\n- Laboratory diagnosis, serology, molecular testing and assay validation\n- Systematic reviews, meta-analysis and evidence appraisal\n- Public health, clinical research, entomology, veterinary health and One Health\n- Biostatistics and methodological or reporting framework development')
    with c2:
        st.subheader('Contribution opportunities')
        st.markdown('- Independently appraise selected article-outcomes\n- Review criteria, the scoring codebook and decision rules\n- Assess clarity, relevance and applicability\n- Identify ambiguous criteria and implementation challenges\n- Contribute to disagreement audits and refinement\n- Review the manuscript or supplementary materials, where applicable')
    st.header('Participation timeline')
    st.caption('Dates from the original collaborator call. See Study timeline for the activity register schedule and its date differences.')
    for dates, title, body in [
        ('22 Sep–10 Oct 2026', 'Expression of interest', 'Submit the form; collaborator selection and collaboration agreements follow.'),
        ('12 Oct–06 Nov 2026', 'Orientation and independent appraisal', 'Attend assessor orientation and appraise assigned article-outcomes.'),
        ('09–20 Nov 2026', 'Discrepancy audit and adjudication', 'Selected collaborators clarify judgments or contribute to source-level adjudication.'),
        ('23 Nov–23 Dec 2026', 'Framework refinement', 'Review revisions to the codebook, decision tree, workspace or appendices as relevant.'),
        ('05 Jan–08 Feb 2027', 'Manuscript development', 'Contribute to internal review of the methodology manuscript and supporting materials.')]:
        with st.container(border=True):
            st.caption(dates)
            st.subheader(title)
            st.write(body)
else:
    documents()

st.divider()
st.caption('DARE-Arbo · Study content: development protocol v1.0 and collaborator call. Timeline statuses and team profiles: project activity workbook. Planned pilot outputs are not completed validation results.')
