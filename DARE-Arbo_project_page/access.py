"""Deny-by-default authorization, independent of UI visibility."""
import time
import math


def has_full_access(identity, allowed_emails, now=None):
    if not identity.get('is_logged_in') or identity.get('email_verified') is not True:
        return False
    if identity.get('iss') not in ('https://accounts.google.com', 'accounts.google.com'):
        return False
    try:
        expiry = float(identity.get('exp', 0))
        if not math.isfinite(expiry) or expiry <= (time.time() if now is None else now):
            return False
    except (TypeError, ValueError):
        return False
    email = str(identity.get('email', '')).strip().casefold()
    return bool(email) and email in {str(x).strip().casefold() for x in allowed_emails}


def require_full_access(identity, allowed_emails):
    if not has_full_access(identity, allowed_emails):
        raise PermissionError('Full access is required for project documents.')
