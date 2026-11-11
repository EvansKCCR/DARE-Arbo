# Collaborator sign-in and OneDrive connection

## Collaborator sign-in: Supabase email OTP

The app's user-facing sign-in uses Supabase email one-time codes. It does not use Google or Microsoft as a collaborator identity provider. Access is invite-only: a person must be present in Supabase Auth **and** in `[access].full_access_emails` in Streamlit Secrets. Public study pages remain public.

1. Create a Supabase project and copy its Project URL and publishable key into `[supabase_auth]` in Streamlit Secrets. The publishable key is not a service-role secret.
2. In Supabase Authentication → Users, invite each approved collaborator. App sign-in requests use `create_user = false`, so an uninvited address cannot self-register.
3. Add each invited address to `[access].full_access_emails`. Add administrator addresses to `[project_management].admin_emails`.
4. Configure a production SMTP sender in Supabase Auth so OTP messages can be delivered to collaborators. The built-in mail service is intended for testing and restricts delivery to project team addresses. Set the email OTP template to include Supabase's `{{ .Token }}` code.
5. Copy the correct Supabase URL and publishable key to Streamlit Community Cloud → Manage app → Settings → Secrets, save, and reboot.

## One-time copy to Supabase Storage

The Project Management page has an administrator-only **Storage migration** tab. Add `[supabase_storage]` to Streamlit Secrets with the project URL, a server-side Supabase Secret key, and the bucket name `dare-arbo-private`. An administrator can then copy the complete OneDrive DARE-Arbo folder into a private bucket. The migration copies rather than deletes; each file is verified by SHA-256 and a manifest is written only after the full copy succeeds. Keep the Microsoft owner connection active until the manifest has been reviewed and the Supabase copy is confirmed.

After verification, deploy the Supabase Storage backend and remove the Microsoft connection from Streamlit Secrets. Do not revoke the owner grant or remove the OneDrive source until that final cutover succeeds.

## Current OneDrive source during migration

Until the verified copy and cutover are complete, the app continues to use the administrator-owned Microsoft Graph grant for source files and workbook edits. Collaborators do not sign in to Microsoft. Keep the owner grant credentials only in Streamlit Secrets under `[microsoft]`; never place them in GitHub or chat.

During this migration phase, the Microsoft owner grant remains active only so the app can copy the files and workbook. Each collaborator is authorized by the Supabase email identity and app allowlist.

The Supabase project URL, publishable key, Microsoft owner grant, approved-email list, admin list, and notification sender are shown as placeholders in `.streamlit/secrets.toml.example`.
