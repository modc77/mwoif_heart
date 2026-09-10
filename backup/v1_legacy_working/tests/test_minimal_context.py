import json
import tempfile
import unittest
from pathlib import Path

from mwoif.auth_store import AuthStore
from mwoif.session_store import SessionStore


CONFIG = {
    "server": {
        "game_base_url": "https://server.live.prod.devsnova.cloud/",
        "friend_grpc_target": "gserver.live.prod.devsnova.cloud:443",
    },
    "devplay": {
        "runtime_metadata_version": "V13.6.1",
        "timezone": "Asia/Bangkok",
        "location_country": "US",
        "os_type": "A",
        "os_version": "12",
        "locale": "en-US",
        "device_name": "SM-A156E",
        "device_model": "SM-A156E",
        "device_id": "DEVICE001",
        "time_zone_distance": "25200",
        "market_type": "GOOGLE_PLAY",
        "login_platform": "email",
    },
    "game": {
        "version": "26.8.02",
        "build_version": "651",
        "index_file_hash": "HASH001",
    },
    "workflow": {},
}


class MinimalContextTests(unittest.TestCase):
    def _root(self, td):
        root = Path(td)
        (root / "config.json").write_text(
            json.dumps(CONFIG), encoding="utf-8"
        )
        return root

    def test_minimal_session(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            rec = SessionStore(root).import_v13(
                slot="1",
                raw=json.dumps({
                    "schema": "mwoif-session-min-v1",
                    "member_seq": 123,
                    "current_lv": 7,
                    "session_key": "S" * 64,
                }),
            )
            self.assertEqual(rec.member_seq, 123)
            self.assertEqual(rec.current_lv, 7)
            self.assertEqual(rec.source, "established_game_state")

    def test_minimal_auth_builds_fixed_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            rec = AuthStore(root).import_v13_1(
                slot="1",
                raw=json.dumps({
                    "schema": "mwoif-auth-min-v1",
                    "mid": "PLAYER001",
                    "game_access_token": "TOKEN.VALUE.TEST",
                    "fgs_id": "FGS001",
                    "game_process_elapsed_ms": 98765,
                }),
            )
            m = rec.metadata
            self.assertEqual(m["player-id"], "PLAYER001")
            self.assertEqual(m["fgs-id"], "FGS001")
            self.assertEqual(m["game-process-elapsed-ms"], "98765")
            self.assertEqual(m["friend-grpc-target"], "gserver.live.prod.devsnova.cloud:443")
            self.assertEqual(m["version"], "26.8.02")
            self.assertEqual(m["version-code"], "651")
            self.assertEqual(m["index-file-hash"], "HASH001")
            self.assertEqual(m["device-id"], "DEVICE001")
            self.assertEqual(rec.device_id, "DEVICE001")

    def test_minimal_auth_requires_dynamic_runtime(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            with self.assertRaises(Exception):
                AuthStore(root).import_v13_1(
                    slot="1",
                    raw=json.dumps({
                        "schema": "mwoif-auth-min-v1",
                        "mid": "PLAYER001",
                        "game_access_token": "TOKEN",
                    }),
                )

    def test_old_full_auth_still_supported_and_target_auto_added(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            rec = AuthStore(root).import_v13_1(
                slot="1",
                raw=json.dumps({
                    "schema": "mwoif-auth-v13.1",
                    "mid": "PLAYER001",
                    "game_access_token": "TOKEN",
                    "metadata": {
                        "player-id": "PLAYER001",
                        "fgs-id": "FGSOLD",
                        "game-process-elapsed-ms": "55",
                    },
                }),
            )
            self.assertEqual(rec.metadata["friend-grpc-target"], "gserver.live.prod.devsnova.cloud:443")
            self.assertEqual(rec.metadata["fgs-id"], "FGSOLD")


if __name__ == "__main__":
    unittest.main()
