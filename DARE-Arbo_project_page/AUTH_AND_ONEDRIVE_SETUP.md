# Collaborator sign-in and OneDrive connection

## Collaborator sign-in: Supabase email OTP

The app's user-facing sign-in uses Supabase email one-time codes. It does not use Google or Microsoft as a collaborator identity provider. Access is invite-only: a person must be present in Supabase Auth **and** in `[access].full_access_emails` in Streamlit Secrets. Public study pages remain public.

1. Create a Supabase project and copy its Project URL and publishable key into `[supabase_auth]` in Streamlit Secrets. The publishable key is not a service-role secret.
2. In Supabase Authentication → Users, invite each approved collaborator. App sign-in requests use `create_user = false`, so an uninvited address cannot self-register.
3. Add each invited address to `[access].full_access_emails`. Add administrator addresses to `[project_management].admin_emails`.
4. Configure a production SMTP sender in Supabase Auth so OTP messages can be delivered to collaborators. The built-in mail service is intended for testing and restricts delivery to project team addresses. Set the email OTP template to include Supabase's `{{ .Token }}` code.
5. Copy the correct Supabase URL and publishable key to Streamlit Community Cloud → Manage app → Settings → Secrets, save, and reboot.

## Private OneDrive backend

The project workbook and documents remain in OneDrive. Since anonymous sharing has been removed, the app uses one administrator-owned Microsoft Graph delegated grant on the server. Collaborators do not sign in to Microsoft. The service account must have named access to the DARE-Arbo folder and consent to delegated `Files.ReadWrite`, `User.Read`, and `Mail.Send` permissions. Its client ID, client secret, and refresh token belong only in Streamlit Secrets under `[microsoft]`; never place them in GitHub or chat.

This is still a Microsoft storage integration, even though collaborator sign-in is independent of Microsoft. If the Microsoft owner grant is revoked or expires, an administrator must reconnect it. Each collaborator is authorized by the Supabase email identity and app allowlist; all server-side file and workbook operations remain restricted to the configured DARE-Arbo item IDs.

The Supabase project URL, publishable key, Microsoft owner grant, approved-email list, admin list, and notification sender are shown as placeholders in `.streamlit/secrets.toml.example`.
