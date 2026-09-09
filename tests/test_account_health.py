import base64
import json
import time
import unittest

from mwoif.account_health import auth_token_info, format_remaining
from mwoif.auth_store import AuthRecord


def fake_jwt(exp):
    def b64(obj):
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return f"{b64({'alg':'none'})}.{b64({'exp':exp,'sub':'TEST001'})}.x"


class AccountHealthTests(unittest.TestCase):
    def auth(self, exp):
        return AuthRecord(
            schema="mwoif-auth-cache-v1",
            slot="1",
            mid="TEST001",
            game_access_token=fake_jwt(exp),
            fgs_id="x",
            device_id="d",
            source="test",
            metadata={},
            imported_at="",
        )

    def test_expired(self):
        info = auth_token_info(self.auth(int(time.time()) - 10))
        self.assertEqual(info["state"], "EXPIRED")
        self.assertEqual(info["remaining_text"], "หมดแล้ว")

    def test_soon(self):
        info = auth_token_info(self.auth(int(time.time()) + 600), warn_seconds=1800)
        self.assertEqual(info["state"], "EXPIRING_SOON")
        self.assertTrue(info["subject_matches_mid"])

    def test_ok(self):
        info = auth_token_info(self.auth(int(time.time()) + 7200), warn_seconds=1800)
        self.assertEqual(info["state"], "OK")


if __name__ == "__main__":
    unittest.main()
