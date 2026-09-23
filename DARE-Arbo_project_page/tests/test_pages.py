import unittest
import time
import json
from unittest.mock import patch
from pathlib import Path
from streamlit.testing.v1 import AppTest


class PageTests(unittest.TestCase):
    def test_translation_reads_workbook_and_handles_missing_file(self):
        root = Path(__file__).resolve().parents[1]
        app = AppTest.from_file(str(root / 'streamlit_app.py'), default_timeout=30).run()
        app.sidebar.radio[0].set_value('Framework & methods').run()
        self.assertFalse(app.exception)
        expected = json.loads((root / 'translation_data.json').read_text(encoding='utf-8'))
        self.assertEqual(app.table[0].value.to_dict('records'), expected)
        original = Path.is_file
        with patch.object(Path, 'is_file', lambda path: False if path.name == 'Translation.xlsx' else original(path)):
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any('translation workbook is currently unavailable' in x.value for x in app.info))
            self.assertFalse(app.get('download_button'))

    def test_public_routes_and_locked_library(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'streamlit_app.py'), default_timeout=30).run()
        self.assertFalse(app.exception)
        self.assertNotIn('Public protocol', app.sidebar.radio[0].options)
        self.assertNotIn('Study timeline', app.sidebar.radio[0].options)
        self.assertFalse(any('Project and Participation' in x.value for x in app.markdown))
        for route in ['Framework & methods', 'Team', 'Development workflow', 'Collaborate', 'Project documents']:
            app.sidebar.radio[0].set_value(route).run()
            self.assertFalse(app.exception, route)
            if route == 'Collaborate':
                self.assertTrue(any('03 May – 21 May 2027' in x.value for x in app.markdown))
            if route == 'Team':
                self.assertEqual(len(app.dataframe[0].value), 6)
        self.assertTrue(any('approved Google account' in x.value for x in app.info))
        self.assertFalse(app.get('file_uploader'))
        self.assertEqual(len(app.get('download_button')), 2)

    def test_signed_in_visitor_cannot_access_library(self):
        class User(dict):
            is_logged_in = True
        user = User(name='Visitor', email='visitor@example.org', email_verified=True,
                    iss='https://accounts.google.com', exp=time.time() + 3600)
        with patch('streamlit.user', user):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'streamlit_app.py'), default_timeout=30).run()
            app.sidebar.radio[0].set_value('Project documents').run()
            self.assertFalse(app.exception)
            self.assertFalse(app.get('file_uploader'))
            self.assertEqual(len(app.get('download_button')), 2)


if __name__ == '__main__': unittest.main()




