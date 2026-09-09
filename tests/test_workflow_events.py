import unittest
from unittest.mock import patch

from mwoif.workflow import run_cycle


class FakeSession:
    def __init__(self, seq):
        self.member_seq = seq
        self.current_lv = 1
        self.session_key = "S" * 64
        self.established = True


class FakeAuth:
    def __init__(self, mid):
        self.mid = mid
        self.game_access_token = "T" * 20
        self.fgs_id = "F"
        self.device_id = "D"
        self.metadata = {}
        self.ready = True


class Store:
    def __init__(self, values): self.values = values
    def load(self, slot): return self.values.get(str(slot).upper())


class EventTests(unittest.TestCase):
    def test_events_do_not_change_cycle(self):
        sessions = Store({"A": FakeSession(100), "1": FakeSession(200)})
        auths = Store({"A": FakeAuth("AID"), "1": FakeAuth("SID")})
        events = []
        ok = lambda **kwargs: {"ok": True}
        mailbox = lambda **kwargs: {"ok": True, "life_mail_candidates": [{"seq": 999}]}
        with (
            patch("mwoif.workflow.send_friend_request", side_effect=ok),
            patch("mwoif.workflow.handle_friend_request", side_effect=ok),
            patch("mwoif.workflow.preview_heart_send", side_effect=ok),
            patch("mwoif.workflow.preview_mail_list", side_effect=mailbox),
            patch("mwoif.workflow.preview_heart_receive", side_effect=ok),
            patch("mwoif.workflow.remove_friend", side_effect=ok),
            patch("mwoif.workflow.time.sleep", return_value=None),
        ):
            result = run_cycle(cfg=object(), sessions=sessions, auths=auths, sender="1", receiver="A", live=True, event_cb=events.append)
        self.assertTrue(result["ok"])
        names = [e["event"] for e in events]
        self.assertIn("cycle_start", names)
        self.assertIn("step_start", names)
        self.assertIn("step_done", names)
        self.assertIn("cycle_done", names)


if __name__ == "__main__":
    unittest.main()
