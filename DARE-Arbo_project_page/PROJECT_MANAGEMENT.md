# Project management: Google Drive workbook

## Assignment notifications and activity calendar

Administrators assigning an activity to a new approved Assignee email now create
a notification event in the same conditional workbook write as the assignment.
Only newly added addresses are notified. Changes to notes or an unchanged
assignee do not produce new mail. The app attempts immediate delivery when its
private [email] configuration is complete; otherwise events remain queued.
An email failure does not roll back the saved activity. Task Management includes
Assignment email status: queued, claimed, sent, failed_or_uncertain or skipped.
Sent means SMTP-server acceptance, not proof of inbox delivery.

The workbook gains two metadata worksheets on use: Assignment Tracking and
Assignment Notifications. They are not a second task store. Keep their headers
unchanged. Existing activity worksheets and formulas remain intact. Assignment
tracking is initialized as a baseline, so historical assignments are not mailed
in bulk on first setup. Newly assigned app tasks queue immediately. Unique
activity IDs are required. Removed or no-longer-approved recipients are skipped.
Claimed or uncertain deliveries are not automatically retried: inspect the mail
provider before an administrator deliberately resets an event to queued.

The sender is the project administrator, evansasamoahadu@gmail.com. Add [email]
to Streamlit Cloud secrets, preserving other sections:

```toml
[email]
host = "smtp.gmail.com"
port = 587
username = "evansasamoahadu@gmail.com"
from_address = "evansasamoahadu@gmail.com"
# Keep your existing password entry here; do not replace it with a template.
app_url = "https://dare-arbo-gxsla2u2vugc8gnsjyessi.streamlit.app/"
```

Google Drive consent does not grant SMTP access. Generate a Gmail app password
privately (requires Google 2-Step Verification; account policy may restrict it):
https://support.google.com/accounts/answer/185833 . Never put it in chat or Git.
This example deliberately supplies no password value. Merge non-secret keys
without replacing existing live secrets. Enter a Gmail app password privately
as password in [email]; do not use the regular Google account password.

For assignments made directly in Google Sheets and for queued delivery while
Streamlit sleeps, run the independent worker once per minute using a single
scheduled job with this project's dependencies installed:

```sh
python send_assignment_notifications.py --send
```

Use the same environment variables as deadline reminders: DRIVE_CLIENT_ID,
DRIVE_CLIENT_SECRET, DRIVE_REFRESH_TOKEN, TASK_ALLOWED_EMAILS, TASK_ADMIN_EMAIL,
TASK_APP_URL, SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM.
The first live worker run baselines existing assignments; subsequent runs detect
new rows and assignee changes. Polling detects saved changes, not transient
reassignments made and reversed between polls. A dry run without --send only
reads/counts already queued events; it does not initialize tracking or send mail.
The worker reports uncertain attempts as failures and retains their claims.
Do not run the worker from a page-refresh callback.

Study Timeline now offers Gantt schedule and Activity calendar. The calendar
has a month grid, full monthly agenda, start/deadline labels, owner/status
details and an Only activities assigned to me filter. Its data uses the existing
60-second synchronization. New assignments and saved dates appear on refresh.
An .ics download exports the filtered month's scheduled activities; assignment
emails also attach the assigned activity's calendar entry. These are static
calendar imports for Google Calendar/Outlook, not a live calendar subscription.
Importing does not grant Google Calendar account access or create events remotely.

Deploy assignment_notifications.py, activity_calendar.py and
send_assignment_notifications.py along with the updated workbook_tasks.py,
sheet_dashboard.py, project_management.py and workspace_views.py. The existing
three-day deadline reminder worker remains unchanged.

Project management uses one source: the Drive-hosted workbook
`1Pmf6ZYq3HyooclARiVtZFqYtI6f-sNQx`, edited through Google Sheets.
There is no SQLite or PostgreSQL task backend. Existing local database files
are left untouched but are not read or updated.

## Dashboard and editing

The workspace has Overview, Activity Register, Study Timeline, and a role-specific
Task Management / My Tasks section. Overview reports five separately defined
activity counts, workstream progress with numeric-data coverage, upcoming
deadlines and recorded overdue/blocked activities. The register provides text,
workstream, owner, workflow status and deadline filters, sorting, and a detail
panel selected by workbook ID. The calendar timeline uses month ticks, a today
marker, state labels, and safely escaped hover details.

Read sections synchronize every 60 seconds, sharing one private session snapshot
per signed-in identity, permissions and Drive configuration. There is no global
cross-user cache. Tab switching is client-side and does not download the workbook.
A saved workbook change appears on the next refresh. A manual Refresh forces a
fresh read. Authorization is rechecked on refresh and write. Failed reads clear
the displayed snapshot rather than presenting old data as current.

The read fragments never refresh the task editor. Once an editor is opened,
its task selection is locked. Keep draft and choose another captures the form
and its original version before changing activities; returning restores the
draft. Discard draft and reload activity explicitly drops it. Task drafts are
session-local and are not permanent storage. Sidebar navigation or reloading
the browser can still lose unsubmitted browser form values. Save or keep the
draft before leaving the workspace. Dashboard tab changes preserve open forms.

Three distinct measures are shown: completed-activity fraction, explicitly
recorded numeric work progress, and scheduled calendar time elapsed. Missing
progress remains missing even for planned activities. Completed workflow status
still counts as completed without inventing an explicit percentage in read
views. The backend's existing Completed-on-save rule still writes 100%.
Mean workstream progress uses only explicit numeric cells; coverage is shown.
The seven-day dashboard deadline horizon is independent of three-day reminders.
Invalid or missing dates generate data-check notices; valid individual dates
remain visible. Missing/duplicate IDs are reported and fall back to row identity
until the workbook has unique IDs. Stable selection across row insertion needs
unique workbook IDs. Malformed date pairs are omitted from the timeline.

Administrators create tasks and edit assignments, owners, dates and outputs
through Task Management. Approved collaborators can edit only
status, Progress (%) and Progress notes on rows assigned to their email.
All changes are written back to the same Activity Register, preserving the
other tabs. The workbook must remain an Excel file in the project folder.

The app adds these columns when first needed (no setup edits are made merely
by opening the page): Assignee email, Progress (%), Progress notes,
Last reminder date, Last reminder state. Assignee email contains exact approved
Google addresses, separated by commas. Initials in Owner are display labels;
they do not grant editing rights. Administrators assign emails before
collaborators can edit existing rows. Assignees must also be on
access.full_access_emails. Unassigned tasks can remain in the dashboard.

Writes compare the displayed workbook hash with a fresh download and use
Drive If-Match version tags. A stale edit must be refreshed before retrying.
If Drive does not return a version tag, writes are disabled. Avoid simultaneous
manual and app edits; if a conflict occurs, refresh and review before resaving.
The app uses openpyxl to update the source Excel file, not native Sheets cell
APIs. Converting it to a native Google Sheet would require an editing adapter.
Existing Gantt formulas are preserved; review fixed chart/formula ranges in
Google Sheets when extending the workbook with additional rows.

## Streamlit deployment

Deploy streamlit_app.py, project_management.py, workspace_views.py, workbook_tasks.py,
sheet_dashboard.py, send_task_reminders.py, access.py and drive_store.py along
with the existing assets and requirements. Reboot after updating code/secrets.
Use the existing [drive] secrets, including the renewed owner refresh token.
No database URL is required. Never commit secrets, tokens or local databases.
Keep the cloud [auth] redirect URI set to its public OAuth callback.

```toml
[project_management]
admin_emails = ["evansasamoahadu@gmail.com"]
```

The administrator account must also be on access.full_access_emails.
Default administrator: evansasamoahadu@gmail.com. The dashboard requires
approved Google sign-in on every refresh. Changing local secrets does not
update Streamlit Cloud secrets; copy only the renewed [drive] values into
the cloud app settings. If Google consent remains in external Testing mode,
Drive refresh tokens can expire after seven days; review production consent
configuration for unattended use.

## Deadline reminders

Run send_task_reminders.py daily from an independent scheduler with the same
Drive owner credentials. It reads the live workbook, using Assignee email for
recipients. It records a daily claim in the workbook before SMTP delivery;
completed, future-start and unassigned tasks are excluded. Reminder claims
are also stored in Drive, so no task/reminder database is needed.

Configure private environment variables: DRIVE_CLIENT_ID, DRIVE_CLIENT_SECRET,
DRIVE_REFRESH_TOKEN, TASK_ALLOWED_EMAILS (comma-separated approved emails),
TASK_ADMIN_EMAIL (default owner above), TASK_APP_URL, SMTP_HOST, SMTP_PORT
(default 587), SMTP_USERNAME, SMTP_PASSWORD and SMTP_FROM. Keep the approved
list synchronized with the app. The reminder administrator must be approved.

Dry run (reads only, prints a count):

```sh
python send_task_reminders.py
```

Send, once daily using a single scheduled worker:

```sh
python send_task_reminders.py --send
```

Tasks due in three days or less, including overdue tasks, are eligible.
Only explicitly approved assignee emails receive reminders. No email is sent
by simply opening the app. SMTP and the external schedule still need setup.
Last reminder state records claimed, not confirmed delivery. Failed/uncertain
emails retain the claim to prevent automatic duplicates. Inspect mail-provider
logs before an administrator deliberately clears a claim to retry. Configure
scheduler failure notifications. The app does not yet maintain a full edit
history; use Drive file version history for workbook recovery.
