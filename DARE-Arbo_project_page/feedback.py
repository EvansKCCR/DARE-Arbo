"""Public, write-only feedback: fixed destination and no document-read access."""
from datetime import datetime, timezone
import re
import uuid
from access import has_full_access
from workspace_storage import FOLDER_ID

CATEGORIES = ['Suggestion', 'Report a problem', 'Content correction', 'Other comment']
PAGES = ['General', 'Overview', 'Framework & methods', 'Team', 'Development workflow', 'Collaborate', 'Project documents']


def validate_feedback(category, page, comment, name='', email=''):
    comment, name, email = comment.strip(), name.strip(), email.strip()
    if category not in CATEGORIES or page not in PAGES:
        raise ValueError('Choose a valid feedback category and page.')
    if not 10 <= len(comment) <= 5000:
        raise ValueError('Please enter a comment between 10 and 5,000 characters.')
    if len(name) > 100 or len(email) > 254:
        raise ValueError('Name or email is too long.')
    if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise ValueError('Enter a valid email address or leave it blank.')
    if any(c in name + email for c in '\r\n'):
        raise ValueError('Name and email must each fit on one line.')
    return dict(category=category, page=page, comment=comment, name=name, email=email)


def save_feedback(service, category, page, comment, name='', email=''):
    fields = validate_feedback(category, page, comment, name, email)
    now = datetime.now(timezone.utc)
    reference = uuid.uuid4().hex
    body = '\n'.join([
        'DARE-Arbo website feedback', f'Reference: {reference}', f'Submitted (UTC): {now.isoformat()}',
        f"Category: {fields['category']}", f"Page: {fields['page']}",
        f"Name (self-reported): {fields['name'] or 'Not provided'}",
        f"Email (self-reported): {fields['email'] or 'Not provided'}", '', 'Comment:', fields['comment'],
    ])
    # Public callers can only create this bounded text record, never choose a file ID,
    # folder, MIME type, filename or permissions, or read other submissions.
    service.upload(FOLDER_ID, f'Feedback_{now:%Y%m%d_%H%M%S}_{reference}.txt', body.encode('utf-8'), 'text/plain')
    return reference


def show_feedback(settings, workspace_service, identity, allowed):
    import time
    import streamlit as st
    st.title('Feedback')
    st.write('Share a suggestion, report a problem, or tell us how this project page could be improved.')
    st.caption('Comments are saved privately in Supabase Storage. Name and email are optional. Please do not include passwords or sensitive participant information.')
    config = settings().get('supabase_storage', {})
    ready = bool(config.get('url') and config.get('secret_key') and config.get('bucket') and has_full_access(identity(), allowed()))
    if not ready:
        st.info('Sign in with an approved collaborator email to submit feedback.')
    with st.form('visitor_feedback', clear_on_submit=False):
        category = st.selectbox('Feedback type', CATEGORIES)
        page = st.selectbox('Which page is this about?', PAGES)
        comment = st.text_area('Your comment', max_chars=5000, height=180, placeholder='What could we improve? If something went wrong, describe the steps and what you expected.')
        name = st.text_input('Name (optional)', max_chars=100)
        email = st.text_input('Email (optional, if you would like a reply)', max_chars=254)
        submitted = st.form_submit_button('Send feedback', type='primary', disabled=not ready)
    if submitted:
        try:
            fields = validate_feedback(category, page, comment, name, email)
            if fields == st.session_state.get('last_feedback_fields'):
                st.info('This feedback has already been submitted. Edit your comment to send something new.')
                return
            if time.time() - st.session_state.get('last_feedback_time', 0) < 60:
                st.warning('Please wait a minute before sending another comment.')
                return
            with st.spinner('Saving your feedback…'):
                reference = save_feedback(workspace_service(config), **fields)
            st.session_state['last_feedback_fields'] = fields
            st.session_state['last_feedback_time'] = time.time()
            st.success('Thank you. Your feedback has been saved for the project team.')
            st.caption('Submission reference: ' + reference)
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error('Your feedback could not be saved. Your text is still here; please retry later or contact the project team.')
