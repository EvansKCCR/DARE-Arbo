import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from feedback import save_feedback, validate_feedback
from drive_store import FOLDER_ID
from streamlit.testing.v1 import AppTest


class FeedbackTests(unittest.TestCase):
    def test_validation_and_fixed_private_destination(self):
        service = Mock()
        with self.assertRaises(ValueError):
            save_feedback(service, 'Suggestion', 'Overview', ' ')
        service.files.assert_not_called()
        with self.assertRaises(ValueError):
            validate_feedback('Suggestion', 'Overview', 'A useful comment', email='invalid')
        reference = save_feedback(service, 'Suggestion', 'Overview', 'Please clarify the study aims.')
        request = service.files().create.call_args.kwargs
        self.assertEqual(request['body']['parents'], [FOLDER_ID])
        self.assertTrue(request['body']['name'].endswith(reference + '.txt'))
        self.assertNotIn('permissions', request['body'])
        service.files().list.assert_not_called()

    def test_anonymous_form_submission_and_duplicate(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'streamlit_app.py'), default_timeout=60)
        app.secrets['drive'] = dict(client_id='test', client_secret='test', refresh_token='test')
        app.run()
        app.sidebar.radio[0].set_value('Feedback').run()
        self.assertFalse(app.exception)
        app.text_area[0].set_value('Please make the study objectives easier to find.')
        with patch('feedback.save_feedback', return_value='test-reference') as save, patch('drive_store.create_service', return_value=Mock()):
            app.button[-1].click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.success)
            app.button[-1].click().run()
            self.assertEqual(save.call_count, 1)


if __name__ == '__main__':
    unittest.main()
