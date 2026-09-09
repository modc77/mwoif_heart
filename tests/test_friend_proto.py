import unittest

from mwoif.friend_proto import (
    build_handle_friend_request,
    build_remove_friend_request,
    build_send_friend_request,
)


class FriendProtoTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(
            build_send_friend_request("PLAYER001", 2).hex(),
            "1209504c415945523030311802",
        )

    def test_accept(self):
        self.assertEqual(
            build_handle_friend_request("PLAYER001", True).hex(),
            "1209504c415945523030311801",
        )

    def test_remove_field_2(self):
        self.assertEqual(
            build_remove_friend_request(["PLAYER001"]).hex(),
            "1209504c41594552303031",
        )


if __name__ == "__main__":
    unittest.main()
