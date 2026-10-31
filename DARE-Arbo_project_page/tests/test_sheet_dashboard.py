from datetime import date
from io import BytesIO
import time
import unittest
from unittest.mock import patch
from openpyxl import Workbook
from streamlit.testing.v1 import AppTest
from sheet_dashboard import parse_register, elapsed


def fixture(extra=False):
    book = Workbook()
    sheet = book.active
    sheet.title = 'Activity Register'
    sheet.append(['Project title'])
    sheet.append(['ID', 'Workstream', 'Activity', 'Key output / milestone', 'Start', 'End', 'Owner', 'Status', 'Progress (%)'])
    sheet.append([1, 'Review', 'First task', 'Review output', date(2026, 1, 5), date(2026, 2, 1), 'EAA', 'Completed'])
    sheet.append([2, 'Pilot', 'Second task', 'Dataset', '22-Sept-26', '2027-05-21', 'All collaborators', 'In progress', '35%'])
    if extra:
        sheet.append([3, 'Pilot', 'New assignment', '', '2027-04-01', '2027-05-25', 'AO', 'Not started'])
    out = BytesIO()
    book.save(out)
    book.close()
    return out.getvalue()


class SheetDashboardTests(unittest.TestCase):
    def test_dates_progress_and_live_added_row(self):
        tasks, issues = parse_register(fixture())
        self.assertEqual([], issues)
        self.assertEqual(2, len(tasks))
        self.assertIsNone(tasks[0]['progress'])
        from workspace_views import complete
        self.assertTrue(complete(tasks[0]))
        self.assertEqual(35, tasks[1]['progress'])
        self.assertEqual(date(2026, 9, 22), tasks[1]['start'])
        updated, _ = parse_register(fixture(True))
        self.assertEqual('AO', updated[-1]['owner'])
        self.assertEqual(date(2027, 5, 25), max(t['end'] for t in updated))
        self.assertEqual(0, elapsed(date(2026, 1, 1), date(2026, 2, 1), date(2025, 1, 1)))
        self.assertEqual(1, elapsed(date(2026, 1, 1), date(2026, 2, 1), date(2027, 1, 1)))

    def test_live_refresh_and_visitor_isolation(self):
        script = '''
from sheet_dashboard import show_sheet_dashboard
import streamlit as st
show_sheet_dashboard(lambda: {'drive': dict(client_id='test', client_secret='test', refresh_token='test')},
    lambda: st.session_state['person'], lambda: ['admin@example.org'])
'''
        app = AppTest.from_string(script, default_timeout=30)
        app.session_state['person'] = dict(email='admin@example.org', is_logged_in=True, email_verified=True, iss='https://accounts.google.com', exp=time.time()+3600)
        with patch('sheet_dashboard.create_service'), patch('sheet_dashboard.DriveStore.download', return_value=('test.xlsx', '', fixture())) as download:
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual('2', app.metric[0].value)
            download.return_value = ('test.xlsx', '', fixture(True))
            app.button[0].click().run()
            self.assertEqual('3', app.metric[0].value)
            # A timed refresh after the 60-second window reads the source again.
            download.return_value = ('test.xlsx', '', fixture())
            app.session_state['pm_snapshot']['loaded'] = time.monotonic() - 61
            app.run()
            self.assertEqual('2', app.metric[0].value)
            app.session_state['person'] = {}
            download.reset_mock()
            app.run()
            download.assert_not_called()
            self.assertEqual(0, len(app.metric))


if __name__ == '__main__':
    unittest.main()
