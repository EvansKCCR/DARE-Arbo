# Supabase workspace setup

## Collaborator sign-in

The app uses invite-only Supabase email one-time codes. An address must be registered in Supabase Auth and listed in `[access].full_access_emails` in Streamlit Secrets. Administrators are listed in `[project_management].admin_emails`.

## Private project files and workbook

The collaborator library, feedback records, meeting schedule, and Activity Register workbook are stored in the private `dare-arbo-private` Supabase Storage bucket. The workbook path is `DARE-Arbo/DARE-Arbo_Project_Gantt_Updated_Sep2026-May2027.xlsx`.

Keep these values in Streamlit Secrets only:

```toml
[supabase_storage]
url = "https://your-project.supabase.co"
secret_key = "sb_secret_..."
bucket = "dare-arbo-private"
```

The secret key is backend-only and must never be committed to GitHub or pasted into chat. The Storage migration tab accepts local files or a ZIP and writes a SHA-256 manifest after reading the uploaded objects back and verifying their contents.

## Assignment and meeting email

The app sends notices through the administrator's SMTP account. For Gmail, create an app password in Google Account security settings and store it only in Streamlit Secrets:

```toml
[email]
from_address = "administrator@gmail.com"
app_url = "https://your-app.streamlit.app/"
smtp_host = "smtp.gmail.com"
smtp_port = 587
username = "administrator@gmail.com"
password = "GMAIL_APP_PASSWORD"
```

Never commit the populated secrets file. Assignment and meeting records are stored in the workbook. The app records queued, sent, skipped and uncertain mail states to avoid automatic duplicate delivery.

## Migration state

Local migration manifests currently report ten uploaded and checksum-verified project files. `Pilot_Articles.zip` is intentionally excluded and is to be shared with collaborators by email. Keep the source files until the live document library, activity dashboard and meeting schedule have been checked against Supabase.
