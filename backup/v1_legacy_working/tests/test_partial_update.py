import json
import tempfile
import unittest
from pathlib import Path

from mwoif.accounts import update_pair_raw
from mwoif.auth_store import AuthStore
from mwoif.session_store import SessionStore


class PartialUpdateTests(unittest.TestCase):
    def test_replace_auth_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sessions = SessionStore(root)
            auths = AuthStore(root)
            session1 = json.dumps({
                "schema":"mwoif-session-v13",
                "member_seq":123,
                "current_lv":1,
                "session_key":"S"*64,
                "source":"test",
            })
            auth1 = json.dumps({
                "schema":"mwoif-auth-v13.1",
                "mid":"TEST001",
                "game_access_token":"old.token.value",
                "metadata":{"fgs-id":"f","device-id":"d"},
            })
            sessions.import_v13(slot="1", raw=session1)
            auths.import_v13_1(slot="1", raw=auth1)
            old_key = sessions.load("1").session_key

            auth2 = json.dumps({
                "schema":"mwoif-auth-v13.1",
                "mid":"TEST001",
                "game_access_token":"new.token.value",
                "metadata":{"fgs-id":"f2","device-id":"d"},
            })
            result = update_pair_raw(
                slot="1", sessions=sessions, auths=auths, auth_raw=auth2
            )
            self.assertFalse(result["updated"]["session"])
            self.assertTrue(result["updated"]["auth"])
            self.assertEqual(sessions.load("1").session_key, old_key)
            self.assertEqual(auths.load("1").game_access_token, "new.token.value")


if __name__ == "__main__":
    unittest.main()
