"""DARE-Arbo study website and restricted collaborator document library."""
from datetime import date
from pathlib import Path
import logging
import json
import streamlit as st
from access import has_full_access
from drive_store import DriveStore, create_service, ALLOWED

ROOT = Path(__file__).resolve().parent
WORKSPACE_URL = 'https://dare-arbo-3gdxlreojotdajtcsf5epr.streamlit.app/'
INTEREST_URL = 'https://docs.google.com/forms/d/e/1FAIpQLSe5dFJ4mjU7XFID1mlbH_e0r1lHR8sOxIOkEEWdnVB3LBmgBg/viewform?usp=header'
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


def translation_table(workbook_path):
    from openpyxl import load_workbook
    workbook = load_workbook(workbook_path, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = []
        for row in range(3, sheet.max_row + 1):
            values = []
            for column in range(1, 4):
                cell = sheet.cell(row, column)
                value = cell.value
                if value is None:
                    for merged in sheet.merged_cells.ranges:
                        if cell.coordinate in merged:
                            value = sheet.cell(merged.min_row, merged.min_col).value
                            break
                values.append('' if value is None else str(value))
            if any(values):
                rows.append(dict(zip(['DARE-Arbo domain', 'Empirical finding', 'Appraisal construct'], values)))
        return rows
    finally:
        workbook.close()


def documents():
    st.header('Project documents')
    st.subheader('Public project documents')
    st.caption('Read online without signing in. Source-file downloads are not offered in this public section.')
    with st.expander('Read the framework development protocol online'):
        from public_protocol import protocol_sections
        for section in protocol_sections():
            st.subheader(section['title'])
            st.html(section['html'])
    with st.expander('Read the project Gantt schedule online'):
        from openpyxl import load_workbook
        schedule_path = ROOT / 'DARE-Arbo_Project_Gantt_Updated_Sep2026-May2027.xlsx'
        if schedule_path.is_file():
            workbook = load_workbook(schedule_path, data_only=True)
            try:
                schedule = []
                for row in workbook['Activity Register'].iter_rows(min_row=4, max_col=9, values_only=True):
                    if not row[2]:
                        continue
                    schedule.append({
                        'Phase': row[1], 'Activity': row[2], 'Output / milestone': row[3],
                        'Start': row[4].strftime('%d %b %Y'), 'End': row[5].strftime('%d %b %Y'),
                        'Duration (days)': row[6], 'Owner': row[7], 'Recorded status': row[8],
                    })
                st.caption('January 2026–May 2027 · Dates and statuses from the updated project activity register.')
                st.table(schedule)
            finally:
                workbook.close()
        else:
            st.info('The project schedule is currently unavailable.')
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
.domain-title{font-size:1.3rem!important;line-height:1.3!important;color:#006B3F;margin:0 0 .8rem;font-weight:700}
[data-testid="stMetric"]{background:#FFFFFF;color:#111111;border:1px solid #D9DED9;border-top:5px solid #006B3F;border-radius:14px;padding:1rem}
[data-testid="stColumn"]:nth-child(2) [data-testid="stMetric"]{border-top-color:#FCD116}
[data-testid="stColumn"]:nth-child(3) [data-testid="stMetric"]{border-top-color:#CE1126}
[data-testid="stColumn"]:nth-child(4) [data-testid="stMetric"]{border-top-color:#111111}
[data-testid="stSidebar"]{background:linear-gradient(165deg,#8B1426 0%,#45101A 42%,#111111 100%);border-right:1px solid #661320;color:#FFFFFF}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] [data-testid="stExpander"] summary{color:#FFFFFF}
[data-testid="stSidebar"] [data-testid="stHeading"] h3{color:#FFFFFF}
[data-testid="stSidebar"] [data-testid="stImage"]{background:#FFFFFF;border-radius:12px;padding:8px}
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"]{flex-direction:row!important;flex-wrap:nowrap!important;gap:12px}
[data-testid="stSidebar"] [data-testid="stColumn"]{min-width:0!important;width:calc(50% - 6px)!important;flex:1 1 0!important}
[data-testid="stSidebar"] [data-testid="stImage"] img{height:100px;object-fit:contain}
[data-testid="stSidebar"] button svg{fill:currentColor;color:#FFFFFF}
[data-testid="stSidebar"] hr{border-color:#B76470}
[data-testid="stSidebar"] [data-testid="stExpander"]{border-color:#B76470}
[data-testid="stSidebar"] button{background:#251116;color:#FFFFFF;border-color:#B76470}
[data-testid="stSidebar"] button:hover{background:#A51D32;border-color:#FFFFFF;color:#FFFFFF}
[data-testid="stSidebar"] [role="radio"][aria-checked="true"]{background:#CE1126}
[data-testid="stHeading"] h1,[data-testid="stHeading"] h2,[data-testid="stHeading"] h3{color:#006B3F}
[data-testid="stAlertContainer"]{background:#FFF8D6;color:#111111;border-left:4px solid #FCD116}
[data-testid="stAlertContainer"] a{color:#004D2D}
a:focus-visible,button:focus-visible{outline:3px solid #CE1126;outline-offset:3px}
@media(max-width:640px){.hero{padding:1.5rem}.hero h1{font-size:2.3rem}}
</style>''', unsafe_allow_html=True)

with st.sidebar:
    ghid_logo, synergy_logo = st.columns(2)
    with ghid_logo:
        st.image(str(ROOT / 'Logo.jpeg'), width='stretch')
    with synergy_logo:
        st.image(str(ROOT / 'Synergy_NGS2025.png'), width='stretch')
    st.markdown('### DARE-Arbo')
    st.caption('Study & collaboration portal')
    page = st.radio('Explore the study', ['Overview', 'Framework & methods', 'Team', 'Development workflow', 'Collaborate', 'Project documents', 'Feedback'], label_visibility='collapsed')
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
    workspace_action, interest_action = st.columns(2)
    with workspace_action:
        st.link_button('Open the digital workspace ↗', WORKSPACE_URL, type='primary', width='stretch')
    with interest_action:
        st.link_button('Express interest in collaborating ↗', INTEREST_URL, width='stretch')
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
            st.markdown('<h3 class="domain-title">' + title + '</h3>', unsafe_allow_html=True)
            st.write(body)
    st.info('Primary objective: develop and prospectively refine DARE-Arbo using the article-outcome as the unit of appraisal. Primary endpoint: inter-assessor agreement on criterion ratings.')
    st.subheader('A collaborative study')
    c1, c2 = st.columns(2)
    with c1:
        st.image(str(ROOT / 'Logo.jpeg'), width=110)
        st.write('**Global Health and Infectious Disease Research Group**')
        st.caption('Kumasi Centre for Collaborative Research in Tropical Medicine')
        st.write('GHID advances One Health research on the connections between humans, animals and the environment. The group combines epidemiology, clinical and laboratory science, and implementation research to improve disease prevention, diagnosis and treatment. It also trains scientists and health professionals and connects research evidence with policy and public health action.')
        st.link_button('Learn about GHID at KCCR ↗', 'https://kccr-ghana.org/research-impact/research-groups/global-health-infectious-diseases/')
    with c2:
        st.image(str(ROOT / 'Synergy_NGS2025.png'), width=110)
        st.write('**SYNERGY-NGS-2025**')
        st.write('**ADVANCING COLLABORATIVE SCIENCE & INNOVATION**')
        st.link_button('SYNERGY-NGS-2025 on LinkedIn ↗', 'https://www.linkedin.com/company/synergy-ngs-2025/?viewAsMember=true')

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
    st.header('Primary endpoint-defining assay pathway')
    figure('primary_endpoint_defining_assay_pathway.png', 'Primary endpoint-defining assay pathway')
    st.header('DARE-Arbo workflow')
    figure('DARE_Arbo_workflow.png', 'DARE-Arbo appraisal workflow')
    st.header('From literature findings to appraisal constructs')
    st.write('Translation of primary-literature findings into DARE-Arbo domains and appraisal constructs.')
    translation_path = ROOT / 'Translation.xlsx'
    if translation_path.is_file():
        with st.expander('Read the translation table', expanded=True):
            st.table(translation_table(translation_path))
        st.download_button('Download Translation workbook', translation_path.read_bytes(), file_name='Translation.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', key='translation_workbook')
    else:
        st.info('The translation workbook is currently unavailable. The project administrator needs to include Translation.xlsx in this deployment.')

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
    portraits = {
        'John Humphrey Amuasi': 'John_H_Amuasi.png',
        'Anthony Afum-Adjei Awuah': 'Anthony_A_A_Awuah.png',
        'Christian Obirikorang': 'Christian_Obirikorang.png',
        'Evans Asamoah Adu': 'Evans_Asamoah_Adu.png',
        'Afolabi Owoloye': 'Afolabi_Owoloye.png',
        'Natalia Shakela': 'Natalia_Shakela.png',
    }
    featured = [m for m in members if m['name'] in portraits]
    for offset in range(0, len(featured), 2):
        for col, member in zip(st.columns(2), featured[offset:offset+2]):
            with col.container(border=True):
                st.image(str(ROOT / 'Images' / portraits[member['name']]), width=220)
                st.subheader(member['name'])
                st.write('**' + member['position'] + '**')
                st.caption(member['affiliation'])
                st.write('**Expertise**')
                st.write(member['expertise'])
    remaining = [m for m in members if m['name'] not in portraits]
    if remaining:
        st.subheader('Additional team members')
        st.dataframe([{'Name': m['name'], 'Position': m['position'], 'Affiliation': m['affiliation'],
                       'Expertise': m['expertise']} for m in remaining], hide_index=True, width='stretch')
    st.caption('Source: ' + STUDY['source'] + ' · Team_members. Photographs supplied by the project team.')

elif page == 'Development workflow':
    st.title('Development workflow')
    st.write('Evidence review and construct identification inform domain mapping, criteria and the scoring codebook. Independent pilot testing then supports disagreement auditing and iterative refinement before final implementation.')
    figure('DARE-Arbo_development_pipeline.png', 'Development pipeline supplied with the study materials. Week ranges are indicative, not verified completion dates.')
    with st.expander('View the additional framework workflow'):
        figure('Framework_development_worflow.png', 'Additional framework development workflow from the source folder.')

elif page == 'Collaborate':
    st.title('Help refine DARE-Arbo')
    st.write('Researchers and practitioners are invited to contribute to pilot testing and expert review of the framework.')
    opening, closing = date(2026, 9, 22), date(2026, 10, 31)
    today = date.today()
    st.info('Expression-of-interest window: 22 September–31 October 2026. ' + ('The scheduled window is open.' if opening <= today <= closing else 'The scheduled window has closed.' if today > closing else 'The scheduled window has not yet opened.'))
    st.link_button('Complete the expression-of-interest form ↗', INTEREST_URL, type='primary')
    c1, c2 = st.columns(2)
    with c1:
        st.subheader('Who can contribute?')
        st.markdown('- Epidemiology and infectious disease surveillance\n- Arbovirus surveillance and outbreak investigation\n- Laboratory diagnosis, serology, molecular testing and assay validation\n- Systematic reviews, meta-analysis and evidence appraisal\n- Public health, clinical research, entomology, veterinary health and One Health\n- Biostatistics and methodological or reporting framework development')
    with c2:
        st.subheader('Contribution opportunities')
        st.markdown('- Independently appraise selected article-outcomes\n- Review criteria, the scoring codebook and decision rules\n- Assess clarity, relevance and applicability\n- Identify ambiguous criteria and implementation challenges\n- Contribute to disagreement audits and refinement\n- Review the manuscript or supplementary materials, where applicable')
    st.markdown((ROOT / 'participation.md').read_text(encoding='utf-8'))

elif page == 'Feedback':
    from feedback import show_feedback
    show_feedback(settings, create_service)
else:
    documents()

st.divider()
st.caption('DARE-Arbo · Study content: development protocol v1.0 and collaborator call. Timeline statuses and team profiles: project activity workbook. Planned pilot outputs are not completed validation results.')
