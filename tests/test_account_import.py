import json
import tempfile
import unittest
from pathlib import Path

from mwoif.accounts import existing_slots, import_pair, pair_status
from mwoif.auth_store import AuthStore
from mwoif.session_store import SessionStore


class AccountImportTests(unittest.TestCase):
    def test_numeric_sender_slot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            session_file = root / 'session.json'
            auth_file = root / 'auth.json'
            session_file.write_text(json.dumps({
                'schema': 'mwoif-session-v13',
                'member_seq': 12345,
                'current_lv': 1,
                'session_key': 'S' * 64,
                'source': 'test',
            }), encoding='utf-8')
            auth_file.write_text(json.dumps({
                'schema': 'mwoif-auth-v13.1',
                'mid': 'PLAYER001',
                'game_access_token': 'T' * 64,
                'source': 'test',
                'metadata': {'player-id': 'PLAYER001'},
            }), encoding='utf-8')

            sessions = SessionStore(root)
            auths = AuthStore(root)
            import_pair(
                slot='17',
                session_file=session_file,
                auth_file=auth_file,
                sessions=sessions,
                auths=auths,
            )

            self.assertEqual(existing_slots(root), ['17'])
            self.assertTrue(pair_status('17', sessions, auths)['ready'])


if __name__ == '__main__':
    unittest.main()
