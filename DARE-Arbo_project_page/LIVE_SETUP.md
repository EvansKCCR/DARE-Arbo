# Live Streamlit setup

App: https://dare-arbo-gxsla2u2vugc8gnsjyessi.streamlit.app/

## Gmail sender

The administrator reported saving the new Gmail app password to the live app.
The local password is intentionally still empty. Local tests cannot confirm a
password stored only in Streamlit Cloud. Do not overwrite the live password by
copying the local secrets file or the example file wholesale.

In Manage app → Settings → Secrets, keep the existing [auth], [access], [drive]
and saved [email].password. Add any missing keys to the existing [email] section:

```toml
host = "smtp.gmail.com"
port = 587
username = "evansasamoahadu@gmail.com"
from_address = "evansasamoahadu@gmail.com"
app_url = "https://dare-arbo-gxsla2u2vugc8gnsjyessi.streamlit.app/"
```

Do not add a second [email] section. Save secrets, deploy the updated code from
the app's configured GitHub branch, then reboot the app. Keep the cloud OAuth
redirect URI set to its public /oauth2callback URL.

Sign in as an approved administrator. Open Project Management → Task Management
→ Assignment email status → Verify email connection (no message sent).
The check runs on the live server, uses STARTTLS, and verifies Gmail login.
It does not expose the password or send a test message. A successful check means
authentication succeeded, not proof that a recipient's inbox accepts messages.
Assignment email history reports SMTP-server acceptance separately.

Runtime secret check reports whether email.password is missing, empty, a wrong
value type, or present. It reports only key names if a password is found under
common alternative settings such as email.app_password or SMTP_PASSWORD. The
check never displays, copies, stores, clears or migrates secret values. A missing
runtime value alone does not establish that a saved Cloud setting was deleted.
The repository has no real secrets.toml and its example file is never loaded by
the application. Do not replace a saved Secrets configuration with that example.

For assignments made directly in the workbook, enable the external polling
worker described in PROJECT_MANAGEMENT.md. Page refreshes do not send email.

## Automatically updated Team page

The public Team page reads the Team_members tab in the existing Drive-hosted
Excel workbook every 60 seconds while open. Only public profile fields are
rendered: name, initials, role, affiliation, expertise, portrait and display mode.
Private workbook columns, task assignments and Updated by are not rendered.
The initial six portraits remain in equal-sized cards; other profiles stay in
the additional-member table unless an administrator selects profile-card display.
If Drive is temporarily unavailable, the page labels its last/published snapshot
and retries. Existing packaged profiles are the fallback for a fresh session.

Approved Google accounts in project_management.admin_emails can edit through
Team → Manage team profiles. The default administrator is
evansasamoahadu@gmail.com, and it must also be in access.full_access_emails.
Approved accounts without administrator access cannot edit profiles or upload
portraits. Adding a member to the public team does not grant sign-in permissions.

Administrators may add members, edit text, select card/table display and upload
JPEG/PNG portraits (up to 5 MB / 16 megapixels). Uploaded portraits are validated,
resized proportionally, stripped of metadata and saved as PNG in the project
folder. Their IDs are stored on the corresponding Team_members row. Public
image reads require the app's published-portrait marker and project-folder
membership; arbitrary private Drive files cannot be displayed as portraits.

Publishing adds Profile ID, Featured, Photo file ID, Local portrait, Updated at,
and Updated by columns where needed. Existing rows, worksheets and formulas are
preserved. Existing local portrait references survive name edits. Source and
portrait file permissions are not changed: the app renders the designated public
fields using its owner connection rather than making the workbook public.

The public auto-refresh is separate from the editor. An open form retains its
original workbook version. Concurrent workbook changes trigger a conflict rather
than overwriting another edit. Save before selecting another profile, or use
Reload profiles for editing to discard unsaved changes. A failed conditional
write after portrait upload can leave an unreferenced private photo; no existing
image is deleted automatically.

Deploy team_profiles.py and team_store.py, the updated streamlit_app.py,
project_management.py, assignment_notifications.py and requirements.txt, along
with all existing app modules and assets. No database is required. The team
schema is initialized only when an administrator saves; reading never writes.

## Deployment bundle

The prepared deployment ZIP contains the DARE-Arbo_project_page directory and
its code/assets, excluding real secrets, OAuth client files, tokens, local
databases and virtual environments. Extract it and update the corresponding
directory in the live app's configured GitHub repository/branch. Streamlit Cloud
does not deploy a ZIP directly. A repository URL and branch are needed to publish
from this local non-Git project folder.
