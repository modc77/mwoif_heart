import unittest

from mwoif.workflow import cycle_plan


class WorkflowPlanTests(unittest.TestCase):
    def test_sender_to_single_receiver(self):
        plan = cycle_plan("17", "A")
        self.assertEqual(
            [x["step"] for x in plan],
            [
                "friend-add",
                "friend-accept",
                "heart-send",
                "heart-mail-list",
                "heart-receive",
                "friend-remove",
            ],
        )
        self.assertEqual(plan[0]["actor"], "17")
        self.assertEqual(plan[1]["actor"], "A")
        self.assertEqual(plan[-1]["actor"], "17")


if __name__ == "__main__":
    unittest.main()
