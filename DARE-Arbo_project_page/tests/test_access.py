import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from access import has_full_access
from drive_store import DriveStore, FOLDER_ID, MAX_BYTES


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.user = dict(is_logged_in=True, email='Member@example.org', email_verified=True,
                         iss='https://accounts.google.com', exp=time.time() + 3600)
        self.allowed = ['member@example.org']
        self.service = Mock()
        self.store = DriveStore(self.service, lambda: self.user, lambda: self.allowed)

    def test_access_requires_verified_approved_unexpired_google_identity(self):
        self.assertTrue(has_full_access(self.user, self.allowed))
        for change in ({'is_logged_in': False}, {'email': 'other@example.org'},
                       {'email_verified': False}, {'email_verified': 'true'}, {'exp': 0},
                       {'exp': 'invalid'}, {'iss': 'https://attacker.example'}):
            self.assertFalse(has_full_access(self.user | change, self.allowed))
        self.assertFalse(has_full_access({}, self.allowed))
        self.assertFalse(has_full_access(self.user, []))

    def test_denied_operations_never_call_drive(self):
        self.allowed.clear()
        for operation in (self.store.list_files, lambda: self.store.download('file'),
                          lambda: self.store.upload('a.pdf', b'test')):
            with self.assertRaises(PermissionError): operation()
        self.service.files.assert_not_called()

    def test_folder_escape_and_trashed_files_rejected(self):
        for meta in ({'parents': ['elsewhere']}, {'parents': [FOLDER_ID], 'trashed': True}):
            self.service.files().get().execute.return_value = meta
            with self.assertRaises(PermissionError): self.store.download('outside')
        self.service.files().get_media.assert_not_called()

    def test_upload_validation(self):
        for name, data in [('run.exe', b'data'), ('blank.pdf', b''), ('huge.pdf', b'x' * (MAX_BYTES + 1))]:
            with self.assertRaises(ValueError): self.store.upload(name, data)
        self.service.files.assert_not_called()

    def test_pagination_stays_in_folder(self):
        self.service.files().list().execute.side_effect = [
            {'files': [{'id': 'one'}], 'nextPageToken': 'next'}, {'files': [{'id': 'two'}]}]
        self.assertEqual(len(self.store.list_files()), 2)
        for call in self.service.files().list.call_args_list:
            if call.kwargs:
                self.assertIn(FOLDER_ID, call.kwargs['q'])

    def test_upload_uses_only_fixed_folder(self):
        self.service.files().create().execute.return_value = {'id': 'new', 'name': 'note.txt'}
        result = self.store.upload('../note.txt', b'approved content')
        self.assertEqual(result['id'], 'new')
        args = self.service.files().create.call_args.kwargs
        self.assertEqual(args['body'], {'name': 'note.txt', 'parents': [FOLDER_ID]})

    def test_binary_download_and_native_export(self):
        for source_mime, expected_name in [('text/plain', 'note'), ('application/vnd.google-apps.document', 'note.pdf')]:
            self.service.files().get().execute.return_value = dict(parents=[FOLDER_ID],
                name='note', mimeType=source_mime, capabilities={'canDownload': True}, size='4')
            def downloader(output, request, chunksize):
                output.write(b'data')
                transfer = Mock()
                transfer.next_chunk.return_value = (None, True)
                return transfer
            with patch('googleapiclient.http.MediaIoBaseDownload', side_effect=downloader):
                name, mime, data = self.store.download('allowed')
            self.assertEqual(name, expected_name)
            self.assertEqual(data, b'data')

    def test_revocation_is_checked_again(self):
        self.assertTrue(has_full_access(self.user, self.allowed))
        self.allowed.clear()
        with self.assertRaises(PermissionError): self.store.list_files()


if __name__ == '__main__': unittest.main()
