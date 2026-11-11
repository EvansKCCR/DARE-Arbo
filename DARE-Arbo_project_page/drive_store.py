"""Microsoft Graph access to the DARE-Arbo OneDrive workspace."""
import base64
from email.utils import getaddresses
from pathlib import PurePosixPath
from urllib.parse import quote
import httpx
import streamlit as st
from access import require_full_access

DRIVE_ID = '5e3948114b4b5428'
FOLDER_ID = '5E3948114B4B5428!s525acf2c09ef4bc2ae079112d40f753d'
WORKBOOK_FOLDER_ID = '5E3948114B4B5428!s22536612d1fe4de390b13372e05d5dcc'
SOURCE_ID = '5E3948114B4B5428!sf86a6ffd1ef7409a990e92bf1932e76a'
MAX_BYTES = 25 * 1024 * 1024
ALLOWED = {'.pdf', '.docx', '.xlsx', '.pptx', '.csv', '.tsv', '.txt', '.png', '.jpg', '.jpeg'}
GRAPH_ROOT = 'https://graph.microsoft.com/v1.0'


class GraphAPIError(RuntimeError):
    def __init__(self, status, message='Microsoft Graph request failed'):
        super().__init__(message)
        self.resp = type('ResponseStatus', (), {'status': status})()
        self.status_code = status


def create_service(config):
    """Use the server-side OneDrive owner grant after app-level authorization."""
    if not config or not all(config.get(k) for k in ('client_id','refresh_token')):
        raise ValueError('The OneDrive owner connection is not configured.')
    token = _owner_graph_token(str(config['client_id']), str(config.get('client_secret','')),
                               str(config['refresh_token']))
    return GraphClient(token, config, refresh_callback=lambda: _refresh_owner_token(config))


@st.cache_data(ttl=2700, show_spinner=False)
def _owner_graph_token(client_id, client_secret, refresh_token):
    return refresh_access_token({'client_id':client_id,'client_secret':client_secret,
                                 'refresh_token':refresh_token})


def _refresh_owner_token(config):
    _owner_graph_token.clear()
    return _owner_graph_token(str(config['client_id']), str(config.get('client_secret','')),
                              str(config['refresh_token']))


def refresh_access_token(config):
    form = {'client_id':config['client_id'], 'grant_type':'refresh_token',
        'refresh_token':config['refresh_token'],
        'scope':'https://graph.microsoft.com/Files.ReadWrite https://graph.microsoft.com/User.Read https://graph.microsoft.com/Mail.Send offline_access'}
    if config.get('client_secret'):
        form['client_secret'] = config['client_secret']
    try:
        response = httpx.post('https://login.microsoftonline.com/consumers/oauth2/v2.0/token',
            data=form, timeout=30)
    except httpx.HTTPError:
        raise RuntimeError('The private OneDrive connection could not be refreshed.') from None
    if response.status_code >= 400:
        raise PermissionError('The private OneDrive authorization has expired. The administrator must reconnect its owner account.')
    return response.json().get('access_token')


class GraphClient:
    def __init__(self, token, config, refresh_callback=None):
        self.headers = {'Authorization': f'Bearer {token}'}
        self.config = config
        self.drive_id = str(config.get('drive_id') or DRIVE_ID)
        self.refresh_callback = refresh_callback

    def item_path(self, item_id):
        return f'/drives/{quote(self.drive_id, safe="")}/items/{quote(item_id, safe="")}'

    def request(self, method, path, *, params=None, content=None, json_data=None, headers=None, raw=False):
        url = path if path.startswith('https://') else GRAPH_ROOT + path
        request_headers = {**self.headers, **(headers or {})}
        try:
            response = httpx.request(method, url, headers=request_headers, params=params if not path.startswith('https://') else None,
                content=content, json=json_data, timeout=45, follow_redirects=True)
        except httpx.HTTPError:
            raise RuntimeError('Microsoft Graph could not be reached. Check the network and retry.') from None
        if response.status_code == 401 and self.refresh_callback:
            self.headers['Authorization'] = 'Bearer ' + self.refresh_callback()
            request_headers['Authorization'] = self.headers['Authorization']
            try:
                response = httpx.request(method, url, headers=request_headers,
                    params=params if not path.startswith('https://') else None,
                    content=content, json=json_data, timeout=45, follow_redirects=True)
            except httpx.HTTPError:
                raise RuntimeError('Microsoft Graph could not be reached. Check the network and retry.') from None
        if response.status_code >= 400:
            raise GraphAPIError(response.status_code, f'Microsoft Graph request failed ({response.status_code}).')
        if raw:
            return response.content
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def get_item(self, item_id):
        return self.request('GET', self.item_path(item_id), params={'$select':'id,name,size,file,folder,parentReference,eTag,lastModifiedDateTime,webUrl,deleted'})

    def list_children(self, folder_id):
        items, path = [], self.item_path(folder_id) + '/children'
        while path:
            page = self.request('GET', path, params={'$select':'id,name,size,file,folder,parentReference,eTag,lastModifiedDateTime,webUrl,deleted','$top':'200'})
            items.extend(page.get('value', []))
            path = page.get('@odata.nextLink')
        return items

    def download(self, item_id):
        return self.request('GET', self.item_path(item_id) + '/content', raw=True)

    def upload(self, parent_id, name, content, mime_type):
        path = self.item_path(parent_id) + f':/{quote(name, safe="")}:/content'
        headers = {'Content-Type': mime_type, 'If-None-Match':'*'}
        return self.request('PUT', path, content=content, headers=headers)

    def update_item(self, item_id, content, etag, mime_type):
        return self.request('PUT', self.item_path(item_id) + '/content', content=content,
            headers={'Content-Type':mime_type, 'If-Match':etag})

    def verify_mail_sender(self, expected_address):
        profile = self.request('GET', '/me', params={'$select':'mail,userPrincipalName'})
        actual = str(profile.get('mail') or profile.get('userPrincipalName') or '').strip().casefold()
        if not actual or actual != str(expected_address or '').strip().casefold():
            raise PermissionError('The signed-in Microsoft account does not match the configured notification sender.')
        return actual

    def send_message(self, message):
        """Send a plain-text email and optional small attachments through Graph."""
        from email.utils import parseaddr
        sender = parseaddr(message.get('From', ''))[1]
        self.verify_mail_sender(sender)
        recipients = [address for _, address in getaddresses(message.get_all('To', [])) if address]
        if not recipients:
            raise ValueError('The notification has no email recipients.')
        body_part = message.get_body(preferencelist=('plain',))
        body_text = body_part.get_content() if body_part else ''
        attachments = []
        for part in message.iter_attachments():
            payload = part.get_content()
            if isinstance(payload, str):
                payload = payload.encode(part.get_content_charset() or 'utf-8')
            attachments.append({
                '@odata.type':'#microsoft.graph.fileAttachment',
                'name':part.get_filename() or 'attachment',
                'contentType':part.get_content_type(),
                'contentBytes':base64.b64encode(payload).decode('ascii'),
            })
        graph_message = {
            'subject':str(message.get('Subject') or ''),
            'body':{'contentType':'Text','content':body_text},
            'toRecipients':[{'emailAddress':{'address':address}} for address in recipients],
        }
        if attachments:
            graph_message['attachments'] = attachments
        return self.request('POST', '/me/sendMail', json_data={'message':graph_message,'saveToSentItems':True})


class DriveStore:
    def __init__(self, service, identity, allowed_emails):
        self.service, self.identity, self.allowed_emails = service, identity, allowed_emails

    def authorize(self):
        require_full_access(self.identity(), self.allowed_emails())

    def list_files(self):
        self.authorize()
        result = []
        for item in self.service.list_children(FOLDER_ID):
            result.append({'id':item['id'], 'name':item['name'],
                'mimeType':item.get('file',{}).get('mimeType','application/vnd.microsoft.graph.folder' if item.get('folder') else 'application/octet-stream'),
                'size':item.get('size',0), 'modifiedTime':item.get('lastModifiedDateTime',''),
                'parents':[item.get('parentReference',{}).get('id','')],
                'capabilities':{'canDownload':not bool(item.get('folder'))}})
        return result

    def download(self, file_id):
        self.authorize()
        meta = self.service.get_item(file_id)
        if meta.get('deleted') or meta.get('parentReference',{}).get('id') != FOLDER_ID:
            raise PermissionError('This file is outside the project folder.')
        if meta.get('folder') or not meta.get('file'):
            raise PermissionError('Folders cannot be downloaded here.')
        if int(meta.get('size',0)) > MAX_BYTES:
            raise ValueError('This file exceeds the 25 MB download limit.')
        content = self.service.download(file_id)
        if len(content) > MAX_BYTES:
            raise ValueError('This file exceeds the 25 MB download limit.')
        mime = meta['file'].get('mimeType','application/octet-stream')
        name = PurePosixPath(meta['name'].replace('\\','/')).name
        return name, mime, content

    def upload(self, name, content):
        self.authorize()
        name = PurePosixPath(name.replace('\\','/')).name.strip()
        if not name or any(ord(c) < 32 for c in name) or PurePosixPath(name).suffix.lower() not in ALLOWED:
            raise ValueError('Choose a supported document or image filename.')
        if not content or len(content) > MAX_BYTES:
            raise ValueError('Uploads must contain data and be no larger than 25 MB.')
        if any(item['name'].casefold() == name.casefold() for item in self.service.list_children(FOLDER_ID)):
            raise ValueError('A file with that name already exists. Rename the new upload and try again.')
        import mimetypes
        mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        return self.service.upload(FOLDER_ID, name, content, mime)
