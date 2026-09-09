import unittest

from main import parse_slot_selector


class SelectorTests(unittest.TestCase):
    def test_range_and_list(self):
        self.assertEqual(
            parse_slot_selector(["1,3,5-7"]),
            ["1", "3", "5", "6", "7"],
        )

    def test_reverse_range(self):
        self.assertEqual(
            parse_slot_selector(["3-1"]),
            ["3", "2", "1"],
        )


if __name__ == "__main__":
    unittest.main()
