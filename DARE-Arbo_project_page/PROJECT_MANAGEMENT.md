# Project management setup

## Live study dashboard

The first tab reads the linked Drive workbook
`1Pmf6ZYq3HyooclARiVtZFqYtI6f-sNQx` directly using the existing `[drive]`
OAuth connection. The source is an Excel workbook edited in Google Sheets;
no conversion, publication or new sharing permissions are required. The owner
account must retain access and the file must remain in the project Drive folder.
If converted to a native Google Sheet with the same file ID, the existing Drive
export path also supports it. A new file ID requires updating `SOURCE_ID`.

Keep the `Activity Register` tab and its Activity, Start, End, Owner and Status
headers. All populated activity rows are read on every refresh, including newly
added tasks and assignments. Optional `Progress (%)`, `Progress`, or `% complete`
columns provide numeric work progress. Use 0–100 numbers or percent-formatted
cells. Completed status supplies 100%; planned/not-started supplies 0%. Other
statuses without a numeric value show no invented completion percentage.
Invalid dates are reported, while the activity remains visible in details.

The dashboard refreshes every 60 seconds while open, with a manual refresh
button and successful-read timestamp. It uses no shared data cache. A failed
read shows an error rather than stale data. It requires approved sign-in,
including on automatic refresh. Its overall date range is derived from the
earliest start and latest end; calendar elapsed bars are distinct from recorded
completion. Workstream, owner and status filters affect the detailed schedule,
not the overall study summary. Today is measured in UTC / Ghana time.

This tab is read-only. The existing administrator/collaborator forms remain in
Manage assigned tasks and retain their database permissions. These assignments
are separate from the source workbook; database reminders continue to cover
database tasks only. Update workbook owners/statuses in Google Sheets to change
the study dashboard. Deploy `sheet_dashboard.py` along with the other app files.

The Project management page is private. It uses the existing verified Google
identity and `access.full_access_emails`. Administrators can create, assign and
edit all tasks; collaborators can see only their assignments and edit only
status, progress and notes. Administrator status alone does not grant access:
the account must also be on the full-access list. The existing project owner,
evansasamoahadu@gmail.com, is the default administrator; override this with
`project_management.admin_emails` in secrets.

## Storage

For Streamlit Cloud, provision a private PostgreSQL database and add this to
the app's deployment secrets, not GitHub:

```toml
[project_management]
admin_emails = ["evansasamoahadu@gmail.com"]
database_url = "postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require"
```

Use a dedicated database/user with permission to create and read/write the
`project_tasks` and `task_reminders` tables. Tables are created on first use.
Install the updated requirements. Set up backups with the database provider.
No task data is written to Google Drive or committed to the repository.

For local development only, use `database_url = "sqlite:///project_tasks.sqlite3"`.
The database lives in the app's working directory and is ignored by Git.
SQLite on Streamlit Cloud is ephemeral and must not hold production assignments.
There is no automatic migration from SQLite: choose PostgreSQL before entering
real project tasks. Missing configuration displays a setup notice without crashing.

## Daily email reminders

The scheduler runs independently of Streamlit, including while the app sleeps.
Use your existing scheduler or a private scheduled CI job with Python and this
project's requirements installed. Configure its secret environment variables:

- `TASK_DATABASE_URL`: the same PostgreSQL URL used by the app.
- `TASK_ALLOWED_EMAILS`: comma-separated addresses from `access.full_access_emails`.
  Keep this synchronized when adding or removing collaborators.
- `TASK_APP_URL`: your deployed project page URL.
- `SMTP_HOST`, `SMTP_PORT` (default 587), `SMTP_USERNAME`, `SMTP_PASSWORD`,
  `SMTP_FROM`: credentials for an authorized email provider using STARTTLS.

Run from the project directory:

```sh
python send_task_reminders.py
```

This dry run prints only an eligible count and sends nothing. Once the sender
and recipient list are configured, schedule the following once each morning
(for example 08:00 UTC / Ghana time):

```sh
python send_task_reminders.py --send
```

Reminders cover tasks due in three days or less, including overdue tasks, after
their start date. Completed tasks and recipients outside the scheduler's approved
list are skipped. A unique daily claim prevents concurrent jobs from duplicating
emails. Failed or uncertain deliveries retain their claim: inspect provider logs
before deliberately deleting a claim to retry. A crash after claiming can suppress
that day's reminder; this avoids blindly sending duplicates. Configure scheduler
failure notifications. Assignment emails and automatic reminder sending on page
load are not enabled; this worker handles deadline reminders only.

The current implementation stores each task's latest notes, updater and update
time; it does not provide a full historical audit trail. Concurrent edits use a
version check, so a stale submission must be refreshed before retrying.

Deploy `streamlit_app.py`, `project_management.py`, `task_store.py`, the updated
requirements, and the existing app assets. The scheduler also needs
`send_task_reminders.py` and `access.py`. Never commit database URLs, mail passwords,
local databases or `.streamlit/secrets.toml`.
