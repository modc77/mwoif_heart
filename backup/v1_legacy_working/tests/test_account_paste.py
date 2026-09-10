import tempfile
import unittest
from pathlib import Path

from mwoif.accounts import import_pair_raw, next_sender_slot
from mwoif.auth_store import AuthStore
from mwoif.session_store import SessionStore


SESSION = r'''{
  "schema":"mwoif-session-v13",
  "member_seq":123456789,
  "current_lv":1,
  "session_key":"SSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSS",
  "source":"test"
}'''

AUTH = r'''{
  "schema":"mwoif-auth-v13.1",
  "mid":"PLAYER001",
  "game_access_token":"TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT",
  "source":"test",
  "metadata":{"player-id":"PLAYER001"}
}'''


class AccountPasteTests(unittest.TestCase):
    def test_raw_pair_and_next_slot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sessions = SessionStore(root)
            auths = AuthStore(root)
            result = import_pair_raw(
                slot="1",
                session_raw=SESSION,
                auth_raw=AUTH,
                sessions=sessions,
                auths=auths,
            )
            self.assertEqual(result["slot"], "1")
            self.assertTrue(sessions.load("1").established)
            self.assertTrue(auths.load("1").ready)
            self.assertEqual(next_sender_slot(root, "A"), "2")

    def test_failed_auth_rolls_back_session(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sessions = SessionStore(root)
            auths = AuthStore(root)
            with self.assertRaises(Exception):
                import_pair_raw(
                    slot="1",
                    session_raw=SESSION,
                    auth_raw="not-json",
                    sessions=sessions,
                    auths=auths,
                )
            self.assertIsNone(sessions.load("1"))
            self.assertIsNone(auths.load("1"))


if __name__ == "__main__":
    unittest.main()
