import unittest
from unittest.mock import patch
from types import SimpleNamespace

from mwoif.workflow import run_cycle


class FakeSession:
    def __init__(self, member_seq):
        self.member_seq = member_seq
        self.current_lv = 1
        self.session_key = 'S' * 64
        self.established = True


class FakeAuth:
    def __init__(self, mid):
        self.mid = mid
        self.game_access_token = 'T' * 20
        self.fgs_id = 'F'
        self.device_id = 'D'
        self.metadata = {}
        self.ready = True


class FakeStore:
    def __init__(self, values):
        self.values = values
    def load(self, slot):
        return self.values.get(str(slot).upper())


class WorkflowRunTests(unittest.TestCase):
    def test_full_sequence(self):
        sessions = FakeStore({'A': FakeSession(100), '1': FakeSession(200)})
        auths = FakeStore({'A': FakeAuth('RECEIVER'), '1': FakeAuth('SENDER')})
        calls = []

        def ok(name):
            def inner(**kwargs):
                calls.append(name)
                return {'ok': True, 'elapsed_ms': 1}
            return inner

        def mailbox(**kwargs):
            calls.append('heart-mail-list')
            return {
                'ok': True,
                'life_mail_candidates': [{'seq': 123456, 'fromMemberSeq': 200}],
            }

        with (
            patch('mwoif.workflow.send_friend_request', side_effect=ok('friend-add')),
            patch('mwoif.workflow.handle_friend_request', side_effect=ok('friend-accept')),
            patch('mwoif.workflow.preview_heart_send', side_effect=ok('heart-send')),
            patch('mwoif.workflow.preview_mail_list', side_effect=mailbox),
            patch('mwoif.workflow.preview_heart_receive', side_effect=ok('heart-receive')),
            patch('mwoif.workflow.remove_friend', side_effect=ok('friend-remove')),
            patch('mwoif.workflow.time.sleep', return_value=None),
        ):
            result = run_cycle(
                cfg=SimpleNamespace(),
                sessions=sessions,
                auths=auths,
                sender='1',
                receiver='A',
                live=True,
            )

        self.assertTrue(result['ok'])
        self.assertEqual(result['life_mail_seq'], 123456)
        self.assertEqual(calls, [
            'friend-add',
            'friend-accept',
            'heart-send',
            'heart-mail-list',
            'heart-receive',
            'friend-remove',
        ])


if __name__ == '__main__':
    unittest.main()
