import json
import unittest
from pathlib import Path


class UpdateMapTests(unittest.TestCase):
    def test_map_is_parseable_and_has_core_anchors(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / "docs" / "CURRENT_BUILD_MAP.json").read_text(encoding="utf-8"))
        anchors = {row["search_anchor"] for row in data["items"]}
        self.assertIn("game/sendLifeMail2.ds", anchors)
        self.assertIn("game/myMailList.ds", anchors)
        self.assertIn("game/acceptLifeMail4.ds", anchors)
        self.assertIn("service.api.RemoveFriendRequest.player_ids", anchors)


if __name__ == "__main__":
    unittest.main()
