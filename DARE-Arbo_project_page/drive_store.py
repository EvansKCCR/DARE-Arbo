"""Folder-scoped Drive operations. No global credential or document cache."""
from io import BytesIO
from pathlib import PurePosixPath
from access import require_full_access

FOLDER_ID = '1CxVCUxSs86L2FNt9Rw5RihAU5NAmiRb7'
MAX_BYTES = 25 * 1024 * 1024
ALLOWED = {'.pdf', '.docx', '.xlsx', '.pptx', '.csv', '.tsv', '.txt', '.png', '.jpg', '.jpeg'}
EXPORTS = {
    'application/vnd.google-apps.document': ('application/pdf', '.pdf'),
    'application/vnd.google-apps.spreadsheet': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
    'application/vnd.google-apps.presentation': ('application/pdf', '.pdf'),
}


def create_service(config):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    credentials = Credentials(token=None, refresh_token=config['refresh_token'],
        token_uri='https://oauth2.googleapis.com/token', client_id=config['client_id'],
        client_secret=config['client_secret'])
    return build('drive', 'v3', credentials=credentials, cache_discovery=False)


class DriveStore:
    def __init__(self, service, identity, allowed_emails):
        self.service, self.identity, self.allowed_emails = service, identity, allowed_emails

    def authorize(self):
        require_full_access(self.identity(), self.allowed_emails())

    def list_files(self):
        self.authorize()
        result, token = [], None
        while True:
            page = self.service.files().list(q=f"'{FOLDER_ID}' in parents and trashed = false",
                fields='nextPageToken,files(id,name,mimeType,size,modifiedTime)',
                pageSize=100, pageToken=token, orderBy='folder,name', supportsAllDrives=True,
                includeItemsFromAllDrives=True).execute()
            result.extend(page.get('files', []))
            token = page.get('nextPageToken')
            if not token:
                return result

    def download(self, file_id):
        self.authorize()
        meta = self.service.files().get(fileId=file_id,
            fields='id,name,mimeType,parents,trashed,size,capabilities(canDownload)',
            supportsAllDrives=True).execute()
        if meta.get('trashed') or FOLDER_ID not in meta.get('parents', []):
            raise PermissionError('This file is outside the project folder.')
        if not meta.get('capabilities', {}).get('canDownload', False):
            raise PermissionError('Drive does not permit downloading this file.')
        mime = meta['mimeType']
        name = PurePosixPath(meta['name'].replace('\\', '/')).name
        if mime in EXPORTS:
            mime, extension = EXPORTS[mime]
            request = self.service.files().export_media(fileId=file_id, mimeType=mime)
            name += extension
        elif mime.startswith('application/vnd.google-apps.'):
            raise ValueError('Folders, shortcuts and this Google file type cannot be downloaded here.')
        else:
            if int(meta.get('size', 0)) > MAX_BYTES:
                raise ValueError('This file exceeds the 25 MB download limit.')
            request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        from googleapiclient.http import MediaIoBaseDownload
        output = BytesIO()
        transfer = MediaIoBaseDownload(output, request, chunksize=1024 * 1024)
        done = False
        while not done:
            self.authorize()
            _, done = transfer.next_chunk()
            if output.tell() > MAX_BYTES:
                raise ValueError('This file exceeds the 25 MB download limit.')
        return name, mime, output.getvalue()

    def upload(self, name, content):
        self.authorize()
        name = PurePosixPath(name.replace('\\', '/')).name.strip()
        if not name or any(ord(c) < 32 for c in name) or PurePosixPath(name).suffix.lower() not in ALLOWED:
            raise ValueError('Choose a supported document or image filename.')
        if not content or len(content) > MAX_BYTES:
            raise ValueError('Uploads must contain data and be no larger than 25 MB.')
        from googleapiclient.http import MediaIoBaseUpload
        import mimetypes
        media = MediaIoBaseUpload(BytesIO(content), mimetype=mimetypes.guess_type(name)[0] or
            'application/octet-stream', resumable=True)
        return self.service.files().create(body={'name': name, 'parents': [FOLDER_ID]},
            media_body=media, fields='id,name', supportsAllDrives=True).execute()
