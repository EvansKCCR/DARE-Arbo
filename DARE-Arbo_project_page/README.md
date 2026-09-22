# DARE-Arbo project page

A Streamlit study website based on the two supplied Word documents, workflow figures and partner logos. Public pages cover the study, planned methods and collaboration. The restricted library connects to folder `1CxVCUxSs86L2FNt9Rw5RihAU5NAmiRb7`.

## Run locally

Use Python 3.11 or newer. Run these commands from this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

Public pages work without credentials. The Public protocol page exposes the full text and tables of DARE-Arbo_framework_development.docx without sign-in, as explicitly authorized. Other documents and all library uploads/downloads remain restricted. Images displayed in public pages are public assets.

## Google sign-in and full access

1. Create a Google Cloud project and configure its OAuth consent screen.
2. Create a **Web application** OAuth client with the exact redirect URI `http://localhost:8501/oauth2callback`. Add the production HTTPS callback when deploying.
3. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`. Fill the `[auth]` client ID and secret. Generate a random cookie secret, for example with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
4. Under `[access]`, populate `full_access_emails` with approved Google email addresses. An empty list grants nobody full access. Only the administrator edits this list; users cannot assign themselves a role.
5. While Google OAuth is in Testing, add users to the consent screen's test-user list. Use the appropriate production publishing/verification process before general use.

Full access means a signed-in, verified Google identity with an unexpired identity token whose email is in this server-side list. Expired identities must sign out and sign back in. Signed-in visitors may read the study but cannot browse filenames or transfer documents. This app-level grant does not modify Google Drive sharing permissions.

## Connect the Drive folder

The supplied folder is in **My Drive**, so use OAuth consent from the folder owner (or another account with the required folder access and storage). A service account cannot own uploads in a personal Drive folder.

1. Enable **Google Drive API** in the Cloud project.
2. Create a separate **Desktop app** OAuth client and download its JSON into private storage (never commit it).
3. On your own computer run `python connect_drive.py PATH_TO_DESKTOP_CLIENT_JSON`. Sign in as the folder owner and approve access. The helper requests the Drive scope so it can access the existing folder and its existing documents; Google may require app verification for production use of this restricted scope.
4. Move the generated `[drive]` section from `owner_credentials.toml` into `.streamlit/secrets.toml`, then remove the temporary credential file. Never paste tokens into chat or commit them. The helper does not print credentials.
5. Add at least one approved collaborator email to `[access]`, restart the app, sign in, and test a small upload and download.

The server uses the owner's grant only after app authorization. It can list and download direct children and create new files only in the fixed folder. It does not alter sharing, overwrite or delete files, traverse subfolders, or follow shortcuts. All approved full-access users can see the same library: keep blinded assessor ratings in separately controlled storage until reconciliation is permitted. Existing direct Google Drive access is governed by Google and cannot be revoked by this application.

OAuth refresh tokens for external apps in Google's Testing mode can expire after seven days when Drive scopes are requested. Configure production consent appropriately and reconnect if the token is revoked or expires. Keep the server and its secrets accessible only to trusted administrators.

## File behavior

- Uploads accept PDF, DOCX, XLSX, PPTX, CSV, TSV, TXT, PNG and JPEG, up to 25 MB. Empty files are rejected. Same-name uploads create separate files.
- Downloads preserve binary files. Google Docs/Slides export as PDF; Sheets export as XLSX. Google's native export API has a 10 MB export limit; binary downloads are capped at 25 MB.
- Downloads are prepared on demand in the authorized user's session. No document bytes or credentials are stored in shared caches. A file already downloaded cannot be recalled; prepared download URLs should be treated as private, short-lived bearer links managed by Streamlit.
- Upload validation checks extension, size and filename, not malware. Uploaded documents are stored, never executed or rendered by the app.

## Deploy

Deploy this directory as a Streamlit app with `streamlit_app.py` as its entry point and `requirements.txt` as dependencies. Launch from this directory so `.streamlit/config.toml` is discovered. Configure secrets through your host's secret manager and update the Google callback to `https://YOUR_HOST/oauth2callback`. Keep static file serving disabled. Do not deploy the `.venv` or secrets files. No credentials are bundled and no deployment is performed by these source files.

## Verify

```powershell
python -m unittest discover -s tests -v
```

Tests cover denial for anonymous, unapproved, unverified and expired identities, folder escape prevention, file restrictions and all public page routes. A real OAuth round trip and live transfers require the administrator's credentials and should be verified after setup.

## Content sources

The Study timeline and Team pages use `study_data.json`, a snapshot of the supplied `DARE-Arbo_Project_Gantt_Jan-Dec_2026.xlsx` workbook's Activity Register and Team_members tabs. The snapshot contains 19 activities and 12 members. Update it when the workbook changes; the app does not read Excel live. Position spelling is normalized (PhD, Senior); names and affiliations are preserved. No photos or biographies are invented. Team membership does not grant sign-in access.

The activity register supplies the timeline dates and recorded statuses, including activities through 20 February 2027 despite the workbook filename. Its discrepancy audit and manuscript start dates differ from the earlier collaborator call; the page labels both schedules. Owner initials AAA and JM remain unresolved because they do not match the roster exactly.

- `DARE-Arbo_framework_development.docx`: protocol v1.0 dated 01 April 2026, objectives, domains, scoring, pilot design, analysis and dissemination.
- `Call for Collaborators.docx`: contribution opportunities, participation dates and the embedded expression-of-interest link.
- Supplied workflow PNGs, `Logo.jpeg`, `Synergy_NGS2025.png` and the GHID text file: workflow and partner identity.

No completion status or validation results are inferred from planned dates. Update the curated text in `streamlit_app.py` when source documents change.

Implementation references: [Streamlit Google authentication](https://docs.streamlit.io/develop/tutorials/authentication/google), [Google OAuth web server flow](https://developers.google.com/identity/protocols/oauth2/web-server), [Drive file creation](https://developers.google.com/workspace/drive/api/guides/create-file).
