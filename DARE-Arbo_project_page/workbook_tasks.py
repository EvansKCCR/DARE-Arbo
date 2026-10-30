"""Authorized task edits in the single Drive-hosted project workbook."""
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import re
from openpyxl import load_workbook
from googleapiclient.http import MediaIoBaseUpload
from access import has_full_access
from drive_store import FOLDER_ID, MAX_BYTES
from sheet_dashboard import SOURCE_ID, parse_register, parse_date

STATUSES = ['Not started', 'Planned / update', 'In progress', 'Under review', 'Completed', 'Blocked']
PRIORITIES = ['Low', 'Normal', 'High']
MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def emails(value):
    return {s.strip().casefold() for s in re.split('[,;\\n]', value) if s.strip()}


class WorkbookTasks:
    def __init__(self, service, identity, allowed, admins):
        self.service, self.identity, self.allowed, self.admins = service, identity, allowed, admins
        self.version_service = None

    def versioned(self):
        if self.version_service is None:
            from googleapiclient.discovery import build
            # v2 exposes file ETags explicitly; v3 omits them on this workbook.
            self.version_service = build('drive', 'v2', http=self.service._http, cache_discovery=False)
        return self.version_service

    def actor(self):
        who = self.identity()
        if not has_full_access(who, self.allowed()):
            raise PermissionError('Sign in with an approved collaborator account.')
        return who['email'].strip().casefold()

    def is_admin(self):
        return self.actor() in {x.strip().casefold() for x in self.admins()}

    def read(self):
        self.actor()
        metadata_request = self.service.files().get(fileId=SOURCE_ID, fields='mimeType,parents,trashed,size', supportsAllDrives=True)
        meta = metadata_request.execute()
        if meta.get('trashed') or FOLDER_ID not in meta.get('parents', []) or meta['mimeType'] != MIME:
            raise PermissionError('Task editing requires the original Excel workbook in the project folder.')
        if int(meta.get('size', 0)) > MAX_BYTES:
            raise ValueError('The workbook exceeds the 25 MB limit.')
        etag = self.versioned().files().get(fileId=SOURCE_ID, fields='etag', supportsAllDrives=True).execute().get('etag')
        request = self.service.files().get_media(fileId=SOURCE_ID, supportsAllDrives=True)
        content = request.execute()
        if len(content) > MAX_BYTES:
            raise ValueError('The workbook exceeds the 25 MB limit.')
        return content, etag

    def list(self):
        actor = self.actor()
        content, _ = self.read()
        tasks, _ = parse_register(content)
        version = sha256(content).hexdigest()
        return [self.adapt(t, version) for t in tasks if self.is_admin() or actor in emails(t['assignee'])]

    @staticmethod
    def adapt(task, version):
        return dict(id=task['row'], title=task['activity'], description=task['output'],
                    assignee=task['assignee'], owner=task['owner'], workstream=task['workstream'],
                    start_date=task['start'].isoformat() if task['start'] else '',
                    due_date=task['end'].isoformat() if task['end'] else '', status=task['status'],
                    progress=100 if task['status'].casefold() in ('completed', 'complete', 'done') and task['progress'] is None else task['progress'], note=task['note'], version=version,
                    reminder_day=task['reminder_day'], reminder_state=task['reminder_state'])

    def write(self, content, etag, change):
        if not etag:
            raise ValueError('Drive did not provide a version tag. Editing is disabled to protect concurrent workbook changes.')
        book = load_workbook(BytesIO(content))
        try:
            sheet = book['Activity Register']
            for row in sheet.iter_rows():
                mapping = {str(cell.value or '').strip().casefold(): cell.column for cell in row if cell.value}
                if {'activity', 'start', 'end', 'owner', 'status'}.issubset(mapping):
                    header_row = row[0].row
                    break
            else:
                raise ValueError('Activity Register headers are missing.')
            def column(name):
                if name.casefold() not in mapping:
                    mapping[name.casefold()] = sheet.max_column + 1
                    sheet.cell(header_row, mapping[name.casefold()], name)
                return mapping[name.casefold()]
            change(sheet, column, header_row)
            out = BytesIO()
            book.save(out)
        finally:
            book.close()
        self.actor()
        request = self.versioned().files().update(fileId=SOURCE_ID,
            media_body=MediaIoBaseUpload(BytesIO(out.getvalue()), mimetype=MIME, resumable=False),
            fields='id', supportsAllDrives=True)
        request.headers['If-Match'] = etag
        try:
            request.execute()
        except Exception as exc:
            if getattr(getattr(exc, 'resp', None), 'status', None) == 412:
                raise ValueError('The workbook changed. Refresh before saving again.') from None
            raise

    def save(self, fields, task_id=None, version=None):
        actor, admin = self.actor(), self.is_admin()
        content, etag = self.read()
        if task_id and sha256(content).hexdigest() != version:
            raise ValueError('The workbook changed. Refresh before saving again.')
        tasks, _ = parse_register(content)
        task = next((t for t in tasks if t['row'] == task_id), None)
        if task_id and (not task or (not admin and actor not in emails(task['assignee']))):
            raise PermissionError('You can only update your assigned tasks.')
        permitted = {'status', 'progress', 'note'}
        if admin:
            permitted |= {'title', 'description', 'assignee', 'owner', 'workstream', 'start_date', 'due_date'}
        if (not task_id and not admin) or set(fields) - permitted:
            raise PermissionError('Only administrators can create tasks or change assignments.')
        current = self.adapt(task, version) if task else dict(status='Not started', progress=0, note='')
        current.update(fields)
        if current['status'] not in STATUSES:
            raise ValueError('Choose a valid status.')
        if current['progress'] is not None and (not isinstance(current['progress'], (int, float)) or not 0 <= current['progress'] <= 100):
            raise ValueError('Progress must be between 0 and 100.')
        if current['status'] == 'Completed':
            current['progress'] = 100
        elif current['progress'] == 100:
            raise ValueError('Choose Completed when progress reaches 100%.')
        if not str(current.get('title', '')).strip() or len(current['title']) > 300:
            raise ValueError('Enter a title of up to 300 characters.')
        if len(current.get('note', '')) > 3000 or len(current.get('description', '')) > 5000:
            raise ValueError('Description or notes exceed their length limit.')
        if not emails(current.get('assignee', '')).issubset({e.strip().casefold() for e in self.allowed()}):
            raise ValueError('Assignee email must contain only approved collaborator addresses.')
        schedule_changed = not task_id or bool({'start_date', 'due_date'} & fields.keys())
        if schedule_changed:
            if parse_date(current['due_date']) < parse_date(current['start_date']):
                raise ValueError('The deadline must be on or after the start date.')
        def change(sheet, column, header_row):
            target = task_id or max([header_row] + [t['row'] for t in tasks]) + 1
            if not task_id:
                sheet.cell(target, column('ID'), 'T' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f'))
            names = dict(title='Activity', description='Key output / milestone', assignee='Assignee email',
                         owner='Owner', workstream='Workstream', start_date='Start', due_date='End',
                         status='Status', progress='Progress (%)', note='Progress notes')
            changed = set(fields)
            if current['status'] == 'Completed':
                changed.add('progress')
            for key in changed:
                cell = sheet.cell(target, column(names[key]))
                value = current[key]
                if key in ('start_date', 'due_date'):
                    cell.value = parse_date(value)
                    cell.number_format = 'dd mmm yyyy'
                else:
                    cell.value = value
                    if isinstance(value, str):
                        cell.data_type = 's'  # Treat user text literally, including leading '='.
                    if key == 'progress':
                        cell.number_format = '0.##'
            if schedule_changed and 'duration (days)' in [str(c.value or '').casefold() for c in sheet[header_row]]:
                sheet.cell(target, column('Duration (days)'), (parse_date(current['due_date'])-parse_date(current['start_date'])).days+1)
        self.write(content, etag, change)

    def claim_reminder(self, row, day):
        if not self.is_admin():
            raise PermissionError('Administrator access required for reminder delivery.')
        content, etag = self.read()
        tasks, _ = parse_register(content)
        task = next((t for t in tasks if t['row'] == row), None)
        if not task or task['reminder_day'] == day or task['progress'] == 100:
            return False
        def change(sheet, column, header_row):
            sheet.cell(row, column('Last reminder date'), day)
            sheet.cell(row, column('Last reminder state'), 'claimed')
        self.write(content, etag, change)
        return True
