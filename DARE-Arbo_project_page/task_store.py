"""Task persistence and authorization shared by the UI and reminder worker."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from uuid import uuid4
from access import has_full_access

STATUSES = ['Not started', 'In progress', 'Under review', 'Completed', 'Blocked']
PRIORITIES = ['Low', 'Normal', 'High']


class Database:
    def __init__(self, url):
        self.url = url

    @contextmanager
    def connect(self):
        if self.url.startswith('sqlite:///'):
            import sqlite3
            connection = sqlite3.connect(self.url[10:], timeout=15)
            connection.row_factory = sqlite3.Row
        elif self.url.startswith(('postgres://', 'postgresql://')):
            import psycopg
            from psycopg.rows import dict_row
            connection = psycopg.connect(self.url, row_factory=dict_row)
        else:
            raise ValueError('Use a PostgreSQL URL or a sqlite:/// local database path.')
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def execute(self, connection, sql, values=()):
        return connection.execute(sql if self.url.startswith('sqlite:') else sql.replace('?', '%s'), values)

    def initialize(self):
        with self.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS project_tasks (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                assignee TEXT NOT NULL, priority TEXT NOT NULL, start_date TEXT NOT NULL,
                due_date TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL,
                note TEXT NOT NULL, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
                version INTEGER NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS task_reminders (
                task_id TEXT NOT NULL, reminder_day TEXT NOT NULL, state TEXT NOT NULL,
                PRIMARY KEY(task_id, reminder_day))''')


class TaskStore:
    def __init__(self, database, identity, allowed, admins):
        self.db, self.identity, self.allowed, self.admins = database, identity, allowed, admins

    def actor(self):
        person = self.identity()
        if not has_full_access(person, self.allowed()):
            raise PermissionError('Sign in with an approved collaborator account.')
        return person['email'].strip().casefold()

    def is_admin(self):
        return self.actor() in {x.strip().casefold() for x in self.admins()}

    def list(self):
        email = self.actor()
        with self.db.connect() as c:
            sql = 'SELECT * FROM project_tasks'
            values = ()
            if not self.is_admin():
                sql += ' WHERE assignee = ?'
                values = (email,)
            return [dict(row) for row in self.db.execute(c, sql + ' ORDER BY due_date, title', values).fetchall()]

    def save(self, fields, task_id=None, version=None):
        email = self.actor()
        admin = self.is_admin()
        with self.db.connect() as c:
            if task_id:
                row = self.db.execute(c, 'SELECT * FROM project_tasks WHERE id = ?', (task_id,)).fetchone()
                if not row or (not admin and row['assignee'] != email):
                    raise PermissionError('You can only update your assigned tasks.')
                current = dict(row)
                if current['version'] != version:
                    raise ValueError('This task changed. Refresh the page before saving again.')
            else:
                if not admin:
                    raise PermissionError('Only administrators can create tasks.')
                current = dict(id=str(uuid4()), status='Not started', progress=0, note='', version=0)
            permitted = {'status', 'progress', 'note'}
            if admin:
                permitted |= {'title', 'description', 'assignee', 'priority', 'start_date', 'due_date'}
            if set(fields) - permitted:
                raise PermissionError('You cannot change task assignments or task details.')
            current.update(fields)
            for key, limit in [('title', 160), ('description', 5000), ('note', 3000)]:
                current[key] = str(current.get(key, '')).strip()
                if len(current[key]) > limit or (key == 'title' and not current[key]):
                    raise ValueError(f'Invalid {key}; maximum length is {limit}.')
            current['assignee'] = current.get('assignee', '').strip().casefold()
            if current['assignee'] not in {x.strip().casefold() for x in self.allowed()}:
                raise ValueError('Assign tasks only to approved collaborators.')
            if current.get('priority') not in PRIORITIES or current['status'] not in STATUSES:
                raise ValueError('Choose a valid priority and status.')
            if not isinstance(current['progress'], int) or not 0 <= current['progress'] <= 100:
                raise ValueError('Progress must be between 0 and 100.')
            if date.fromisoformat(current['due_date']) < date.fromisoformat(current['start_date']):
                raise ValueError('The deadline must be on or after the start date.')
            if current['status'] == 'Completed':
                current['progress'] = 100
            elif current['progress'] == 100:
                raise ValueError('Choose Completed when progress reaches 100%.')
            current.update(updated_by=email, updated_at=datetime.now(timezone.utc).isoformat(), version=current['version'] + 1)
            columns = ['title', 'description', 'assignee', 'priority', 'start_date', 'due_date', 'status', 'progress', 'note', 'updated_by', 'updated_at', 'version']
            values = tuple(current[k] for k in columns)
            if task_id:
                result = self.db.execute(c, 'UPDATE project_tasks SET ' + ','.join(k + '=?' for k in columns) + ' WHERE id=? AND version=?', values + (task_id, version))
                if result.rowcount != 1:
                    raise ValueError('Another update was saved. Refresh and try again.')
            else:
                self.db.execute(c, 'INSERT INTO project_tasks (' + ','.join(columns) + ',id) VALUES (' + ','.join('?' for _ in range(13)) + ')', values + (current['id'],))
            return current['id']
