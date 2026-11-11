"""Invite-only Supabase email OTP sign-in for DARE-Arbo collaborators."""
import re
import time
from urllib.parse import urljoin

import httpx
import streamlit as st

SESSION_KEY = 'supabase_session'
PENDING_EMAIL_KEY = 'supabase_pending_email'


class SupabaseAuthError(RuntimeError):
    """Sanitized authentication error; never includes provider response bodies."""


def configured(config):
    url = str(config.get('url') or '').strip().rstrip('/')
    key = str(config.get('publishable_key') or config.get('anon_key') or '').strip()
    return (url.startswith('https://') and '.supabase.co' in url and bool(key)
            and not key.startswith(('YOUR_', 'sb_secret_')) and 'YOUR-PROJECT-REF' not in url)


def normalize_email(email):
    value = str(email or '').strip().casefold()
    if len(value) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise ValueError('Enter a valid email address.')
    return value


def _request(config, path, payload, access_token=None):
    if not configured(config):
        raise SupabaseAuthError('Collaborator sign-in is not configured yet.')
    base = str(config['url']).rstrip('/') + '/'
    headers = {'apikey':config.get('publishable_key') or config.get('anon_key'),
               'Content-Type':'application/json'}
    if access_token:
        headers['Authorization'] = 'Bearer ' + access_token
    try:
        response = httpx.post(urljoin(base, path.lstrip('/')), json=payload,
                              headers=headers, timeout=30)
    except httpx.HTTPError:
        raise SupabaseAuthError('The sign-in service could not be reached. Please retry.') from None
    if response.status_code >= 400:
        try:
            error_data = response.json()
        except ValueError:
            error_data = {}
        if not isinstance(error_data, dict):
            error_data = {}
        error_code = str(error_data.get('code') or error_data.get('error_code') or '').casefold()
        if response.status_code in (401, 403):
            message = 'Supabase rejected the project key. Ask the administrator to check the Supabase project URL and publishable key in Streamlit Secrets.'
        elif response.status_code == 429 or error_code in ('over_email_send_rate_limit', 'email_address_not_authorized'):
            message = 'Supabase could not send the code. Configure a production SMTP sender in Supabase Authentication, then try again.'
        elif path.endswith('/otp') and (response.status_code in (400, 422) or error_code in ('user_not_found', 'signup_disabled')):
            message = 'This address must be invited or added under Supabase Authentication → Users, and the email OTP template must include {{ .Token }}.'
        elif path.endswith('/verify') and response.status_code in (400, 422):
            message = 'That code is invalid or expired. Request a new one and enter the latest code.'
        elif response.status_code >= 500:
            message = 'Supabase Auth could not deliver or verify the code. Check the Auth email/SMTP settings and retry.'
        else:
            message = 'Supabase could not process the request. Check the Auth provider settings or ask the project administrator.'
        raise SupabaseAuthError(message)
    try:
        return response.json() if response.content else {}
    except ValueError:
        raise SupabaseAuthError('The sign-in service returned an unreadable response.') from None


def send_code(config, email):
    email = normalize_email(email)
    _request(config, 'auth/v1/otp', {'email':email, 'create_user':False})
    st.session_state[PENDING_EMAIL_KEY] = email


def verify_code(config, email, code, allowed_emails):
    email = normalize_email(email)
    if email not in {str(item).strip().casefold() for item in allowed_emails}:
        raise PermissionError('This email is not approved for collaborator access.')
    code = str(code or '').strip()
    if not re.fullmatch(r'\d{6,8}', code):
        raise ValueError('Enter the one-time code from your email.')
    result = _request(config, 'auth/v1/verify', {'email':email, 'token':code, 'type':'email'})
    user = result.get('user') or {}
    verified_email = normalize_email(user.get('email'))
    if verified_email != email or not (user.get('email_confirmed_at') or user.get('confirmed_at')):
        raise PermissionError('The verified account does not match an approved collaborator.')
    result['expires_at'] = int(time.time()) + int(result.get('expires_in', 3600))
    st.session_state[SESSION_KEY] = result
    st.session_state.pop(PENDING_EMAIL_KEY, None)
    return user


def current_identity(config):
    session = st.session_state.get(SESSION_KEY)
    if not isinstance(session, dict) or not session.get('access_token'):
        return {}
    expires_at = int(session.get('expires_at') or 0)
    if expires_at <= int(time.time()) + 90:
        refresh_token = session.get('refresh_token')
        if not refresh_token:
            st.session_state.pop(SESSION_KEY, None)
            return {}
        try:
            refreshed = _request(config, 'auth/v1/token?grant_type=refresh_token',
                                 {'refresh_token':refresh_token})
        except SupabaseAuthError:
            st.session_state.pop(SESSION_KEY, None)
            return {}
        refreshed['expires_at'] = int(time.time()) + int(refreshed.get('expires_in', 3600))
        session = refreshed
        st.session_state[SESSION_KEY] = session
    user = session.get('user') or {}
    email = str(user.get('email') or '').strip().casefold()
    return {'is_logged_in':bool(email), 'email':email,
            'name':str((user.get('user_metadata') or {}).get('full_name') or email),
            'email_verified':bool(user.get('email_confirmed_at') or user.get('confirmed_at')),
            'exp':session.get('expires_at'), 'provider':'supabase'}


def sign_out(config):
    session = st.session_state.get(SESSION_KEY) or {}
    access_token = session.get('access_token')
    if access_token and configured(config):
        try:
            _request(config, 'auth/v1/logout', {}, access_token=access_token)
        except SupabaseAuthError:
            pass
    st.session_state.pop(SESSION_KEY, None)
    st.session_state.pop(PENDING_EMAIL_KEY, None)
