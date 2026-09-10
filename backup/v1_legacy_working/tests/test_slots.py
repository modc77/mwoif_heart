import unittest

from mwoif.slots import normalize_slot


class SlotTests(unittest.TestCase):
    def test_many_slot_formats(self):
        self.assertEqual(normalize_slot("a"), "A")
        self.assertEqual(normalize_slot("1"), "1")
        self.assertEqual(normalize_slot("s001"), "S001")
        self.assertEqual(normalize_slot("sender-100"), "SENDER-100")

    def test_path_traversal_rejected(self):
        for value in ("../A", "A/B", "..", ""):
            with self.assertRaises(ValueError):
                normalize_slot(value)


if __name__ == "__main__":
    unittest.main()
